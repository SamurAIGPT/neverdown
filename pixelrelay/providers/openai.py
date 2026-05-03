"""OpenAI image-generation provider.

OpenAI's image API is **synchronous** — there are no webhooks. To fit our
webhook-native gateway, this adapter uses a "submit-then-self-callback" pattern:

  1. ``submit_async()`` returns immediately with a synthesized job ID and spawns
     an asyncio background task.
  2. The background task POSTs to OpenAI synchronously (waits 5–30s for the
     image), then POSTs the result to the gateway's own callback URL
     (``/v1/callback/openai/{job_id}``).
  3. From there the flow is identical to fal/replicate: the gateway parses the
     callback, marks the job succeeded/failed, and forwards to the user's
     webhook_url.

If the gateway restarts mid-flight, the asyncio task dies. The failover worker
catches the orphaned job via deadline expiry and resubmits to the next provider
in the chain. This costs the user one wasted OpenAI request but doesn't lose the
job — acceptable for v0.2.x; a proper task queue is on the v1.0 roadmap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Dict

import httpx

from .base import BaseProvider, CallbackPayload, GenerationResult, SubmitResult
from ..exceptions import JobFailedError, JobTimeoutError, ProviderUnavailableError
from ..models import resolve_for_provider

logger = logging.getLogger(__name__)

OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _resolve_model(self, model: str) -> str:
        return resolve_for_provider(model, self.name)

    def _build_input(self, model: str, prompt: str, kwargs: dict) -> dict:
        body = {
            "model": model,
            "prompt": prompt,
            "n": kwargs.pop("n", 1),
            "size": kwargs.pop("size", "1024x1024"),
        }
        # gpt-image-1 doesn't accept response_format (it's always url-style on output);
        # dall-e-2/dall-e-3 do accept it. Be conservative: only set for dall-e.
        if model.startswith("dall-e"):
            body["response_format"] = kwargs.pop("response_format", "url")
        # Pass through any remaining knobs (quality, style, etc.) the user supplied.
        body.update(kwargs)
        return body

    async def generate(
        self,
        prompt: str,
        model: str,
        timeout: float = 120.0,
        **kwargs,
    ) -> GenerationResult:
        """Library-mode entry point. Synchronous from OpenAI's side; we just
        await the response."""
        openai_model = self._resolve_model(model)
        body = self._build_input(openai_model, prompt, dict(kwargs))
        start = time.monotonic()

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    OPENAI_IMAGE_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=timeout,
                )
            except httpx.ConnectError as e:
                raise ProviderUnavailableError(str(e), provider=self.name)
            except httpx.TimeoutException:
                raise JobTimeoutError(f"Exceeded {timeout}s timeout", provider=self.name)

        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"HTTP {resp.status_code}", provider=self.name, status_code=resp.status_code
            )
        if resp.status_code >= 400:
            raise JobFailedError(
                resp.text, provider=self.name, status_code=resp.status_code
            )

        data = resp.json()
        images = data.get("data") or []
        if not images:
            raise JobFailedError("OpenAI returned no images", provider=self.name)
        image_url = images[0].get("url")
        if not image_url:
            # b64_json mode would put it in 'b64_json'; for v0.2.3 we expect URL
            raise JobFailedError(
                "OpenAI response had no image URL (b64_json mode not yet supported)",
                provider=self.name,
            )
        return GenerationResult(
            image_url=image_url,
            provider=self.name,
            model=model,
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def submit_async(
        self,
        prompt: str,
        model: str,
        webhook_url: str,
        **kwargs,
    ) -> SubmitResult:
        """Spawn a background task that calls OpenAI sync, then POSTs the result
        to webhook_url (the gateway's own callback URL)."""
        openai_model = self._resolve_model(model)
        provider_job_id = f"openai-{uuid.uuid4().hex}"

        # Fire and forget. Errors inside _run_and_callback are caught and POSTed
        # as failure callbacks; we don't want them to bubble up to the dispatcher
        # since submit_async itself succeeded the moment we accepted the job.
        asyncio.create_task(
            self._run_and_callback(
                openai_model=openai_model,
                prompt=prompt,
                webhook_url=webhook_url,
                provider_job_id=provider_job_id,
                kwargs=dict(kwargs),
            )
        )

        return SubmitResult(provider_job_id=provider_job_id, raw={})

    async def _run_and_callback(
        self,
        *,
        openai_model: str,
        prompt: str,
        webhook_url: str,
        provider_job_id: str,
        kwargs: dict,
    ) -> None:
        body = self._build_input(openai_model, prompt, kwargs)
        payload: Dict[str, str] = {"provider_job_id": provider_job_id}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    OPENAI_IMAGE_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=180.0,
                )

            if resp.status_code >= 400:
                payload["status"] = "failed"
                payload["error"] = (
                    f"OpenAI HTTP {resp.status_code}: {resp.text[:300]}"
                )
            else:
                data = resp.json()
                images = data.get("data") or []
                if images and images[0].get("url"):
                    payload["status"] = "succeeded"
                    payload["image_url"] = images[0]["url"]
                else:
                    payload["status"] = "failed"
                    payload["error"] = "OpenAI returned no image URL"
        except httpx.TimeoutException:
            payload["status"] = "failed"
            payload["error"] = "OpenAI request timed out"
        except Exception as e:
            logger.exception("OpenAI background task raised")
            payload["status"] = "failed"
            payload["error"] = f"{type(e).__name__}: {e}"

        # POST the result to the gateway's own callback URL. If this hop fails
        # (e.g. gateway is restarting), the failover worker catches via deadline.
        try:
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json=payload, timeout=15.0)
        except Exception as e:
            logger.warning("Failed to deliver self-callback to %s: %s", webhook_url, e)

    @staticmethod
    def parse_callback(headers: Dict[str, str], body: bytes) -> CallbackPayload:
        data = json.loads(body)
        status = data.get("status")
        if status == "succeeded":
            return CallbackPayload(
                provider_job_id=data.get("provider_job_id"),
                status="succeeded",
                image_url=data.get("image_url"),
                raw=data,
            )
        return CallbackPayload(
            provider_job_id=data.get("provider_job_id"),
            status="failed",
            error=data.get("error") or "OpenAI provider reported failure",
            raw=data,
        )
