"""Google AI Studio provider — Gemini (Nano Banana family) + Imagen 4.

Like OpenAI, Google's image-gen APIs are synchronous — no webhooks. This adapter
uses the same submit-then-self-callback pattern as ``OpenAIProvider``.

Two endpoint shapes are handled here:

* Gemini image models (``gemini-*-image*``) — POST to ``:generateContent``
  with ``contents/parts/generationConfig``. Response candidates contain
  ``inlineData.data`` (base64).
* Imagen 4 (``imagen-4.*``) — POST to ``:predict`` with ``instances/parameters``.
  Response ``predictions[].bytesBase64Encoded`` (base64).

Both return base64 image data, not URLs. We wrap the result as a data URI
(``data:image/png;base64,...``) so the existing ``image_url`` field in webhooks
and ``GET /v1/jobs/{id}`` responses works unchanged. The trade-off is bigger
webhook payloads (~1.4× the raw image size). Storage-backed URL serving is on
the v0.3 roadmap.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Dict, Optional

import httpx

from .base import BaseProvider, CallbackPayload, GenerationResult, SubmitResult
from ..exceptions import JobFailedError, JobTimeoutError, ProviderUnavailableError
from ..models import resolve_for_provider

logger = logging.getLogger(__name__)

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GoogleProvider(BaseProvider):
    name = "google"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _resolve_model(self, model: str) -> str:
        return resolve_for_provider(model, self.name)

    @staticmethod
    def _is_imagen(google_model: str) -> bool:
        return google_model.startswith("imagen-")

    def _build_request(self, google_model: str, prompt: str, kwargs: dict):
        """Build (endpoint_suffix, body) for the given model.

        Returns the URL path suffix (``:generateContent`` or ``:predict``) and
        the JSON body shaped for that endpoint.
        """
        input_image = kwargs.pop("input_image", None)

        if self._is_imagen(google_model):
            params = {}
            if "n" in kwargs:
                params["sampleCount"] = kwargs.pop("n")
            for k in ("aspectRatio", "imageSize", "personGeneration"):
                if k in kwargs:
                    params[k] = kwargs.pop(k)
            body = {
                "instances": [{"prompt": prompt}],
                "parameters": params or {"sampleCount": 1},
            }
            return ":predict", body

        # Gemini image-gen path. Supports image-edit by adding the input image
        # as an inline_data part alongside the text prompt.
        parts = [{"text": prompt}]
        if input_image:
            inline = _to_inline_data(input_image)
            if inline is not None:
                parts.append({"inline_data": inline})

        gen_config = {"responseModalities": ["IMAGE"]}
        if "aspectRatio" in kwargs or "imageSize" in kwargs:
            image_config = {}
            if "aspectRatio" in kwargs:
                image_config["aspectRatio"] = kwargs.pop("aspectRatio")
            if "imageSize" in kwargs:
                image_config["imageSize"] = kwargs.pop("imageSize")
            gen_config["imageConfig"] = image_config

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": gen_config,
        }
        # Pass through anything else the caller supplied (e.g. tools, safety).
        body.update(kwargs)
        return ":generateContent", body

    def _extract_image_data_uri(self, google_model: str, resp_json: dict) -> Optional[str]:
        """Pull base64 image bytes out of either Gemini or Imagen response shapes.
        Returns a `data:image/<mime>;base64,...` URI or None if no image was found."""
        if self._is_imagen(google_model):
            preds = resp_json.get("predictions") or []
            for p in preds:
                b64 = p.get("bytesBase64Encoded") or p.get("imageBytes")
                if b64:
                    mime = p.get("mimeType") or "image/png"
                    return f"data:{mime};base64,{b64}"
            return None

        # Gemini
        candidates = resp_json.get("candidates") or []
        for cand in candidates:
            for part in (cand.get("content", {}).get("parts") or []):
                inline = part.get("inline_data") or part.get("inlineData") or {}
                b64 = inline.get("data")
                if b64:
                    mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
                    return f"data:{mime};base64,{b64}"
        return None

    async def generate(
        self,
        prompt: str,
        model: str,
        timeout: float = 120.0,
        **kwargs,
    ) -> GenerationResult:
        """Library-mode entry point — sync call to Google, return a data URI."""
        google_model = self._resolve_model(model)
        suffix, body = self._build_request(google_model, prompt, dict(kwargs))
        url = f"{GOOGLE_API_BASE}/{google_model}{suffix}"
        start = time.monotonic()

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    url,
                    headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
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

        image_uri = self._extract_image_data_uri(google_model, resp.json())
        if not image_uri:
            raise JobFailedError(
                "Google response had no image data",
                provider=self.name,
            )
        return GenerationResult(
            image_url=image_uri,
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
        google_model = self._resolve_model(model)
        provider_job_id = f"google-{uuid.uuid4().hex}"

        asyncio.create_task(
            self._run_and_callback(
                google_model=google_model,
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
        google_model: str,
        prompt: str,
        webhook_url: str,
        provider_job_id: str,
        kwargs: dict,
    ) -> None:
        suffix, body = self._build_request(google_model, prompt, kwargs)
        url = f"{GOOGLE_API_BASE}/{google_model}{suffix}"
        payload: Dict[str, str] = {"provider_job_id": provider_job_id}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers={
                        "x-goog-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=180.0,
                )
            if resp.status_code >= 400:
                payload["status"] = "failed"
                payload["error"] = f"Google HTTP {resp.status_code}: {resp.text[:300]}"
            else:
                image_uri = self._extract_image_data_uri(google_model, resp.json())
                if image_uri:
                    payload["status"] = "succeeded"
                    payload["image_url"] = image_uri
                else:
                    payload["status"] = "failed"
                    payload["error"] = "Google response had no image data"
        except httpx.TimeoutException:
            payload["status"] = "failed"
            payload["error"] = "Google request timed out"
        except Exception as e:
            logger.exception("Google background task raised")
            payload["status"] = "failed"
            payload["error"] = f"{type(e).__name__}: {e}"

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
            error=data.get("error") or "Google provider reported failure",
            raw=data,
        )


def _to_inline_data(input_image: str) -> Optional[Dict[str, str]]:
    """Convert an input_image value (URL or data URI) to Gemini's inline_data shape.

    For data URIs we extract mime + base64 directly. For HTTP URLs we'd need to
    fetch and encode — deferred to a follow-up; for now those callers should
    pre-encode and pass a data URI. Logged warning on URL inputs."""
    if input_image.startswith("data:"):
        try:
            header, b64 = input_image.split(",", 1)
            mime = header.split(";")[0][len("data:"):] or "image/png"
            return {"mime_type": mime, "data": b64}
        except Exception:
            return None
    logger.warning(
        "Google Gemini input_image must be a data URI for now; URL fetching is on the roadmap"
    )
    return None
