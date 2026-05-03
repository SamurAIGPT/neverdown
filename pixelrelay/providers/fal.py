import asyncio
import json
import time
from typing import Dict

import httpx

from .base import BaseProvider, CallbackPayload, GenerationResult, SubmitResult
from ..exceptions import JobFailedError, JobTimeoutError, ProviderUnavailableError
from ..models import resolve_for_provider

POLL_INTERVAL = 2.0


class FalProvider(BaseProvider):
    name = "fal"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _resolve_model(self, model: str) -> str:
        return resolve_for_provider(model, self.name)

    def _build_input(self, model: str, prompt: str, kwargs: dict) -> dict:
        """Map our normalized request to fal's per-model input schema.

        Verified against fal model docs:
        - FLUX Kontext (fal-ai/flux-pro/kontext, .../kontext/max): field is `image_url` (str)
        - Nano Banana edit (fal-ai/nano-banana/edit, /-2/edit, /-pro/edit): field is `image_urls` (list)
        Other image-edit models default to `image_url`.
        """
        body = {"prompt": prompt}
        input_image = kwargs.pop("input_image", None)
        if input_image:
            if "nano-banana" in model and "edit" in model:
                body["image_urls"] = [input_image]
            else:
                # FLUX Kontext, FLUX Redux, generic edit
                body["image_url"] = input_image
        body.update(kwargs)
        return body

    async def generate(
        self,
        prompt: str,
        model: str,
        timeout: float = 120.0,
        **kwargs,
    ) -> GenerationResult:
        fal_model = self._resolve_model(model)
        start = time.monotonic()

        async with httpx.AsyncClient() as client:
            try:
                submit_resp = await client.post(
                    f"https://queue.fal.run/{fal_model}",
                    headers={
                        "Authorization": f"Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"prompt": prompt, **kwargs},
                    timeout=30.0,
                )
            except httpx.ConnectError as e:
                raise ProviderUnavailableError(str(e), provider=self.name)
            except httpx.TimeoutException:
                raise ProviderUnavailableError("Connection timeout", provider=self.name)

            if submit_resp.status_code >= 500:
                raise ProviderUnavailableError(
                    f"HTTP {submit_resp.status_code}",
                    provider=self.name,
                    status_code=submit_resp.status_code,
                )
            if submit_resp.status_code >= 400:
                raise JobFailedError(
                    submit_resp.text, provider=self.name, status_code=submit_resp.status_code
                )

            job = submit_resp.json()
            request_id = job.get("request_id")
            status_url = job.get("status_url") or f"https://queue.fal.run/{fal_model}/requests/{request_id}/status"
            result_url = f"https://queue.fal.run/{fal_model}/requests/{request_id}"

            while True:
                if time.monotonic() - start >= timeout:
                    raise JobTimeoutError(f"Exceeded {timeout}s timeout", provider=self.name)

                await asyncio.sleep(POLL_INTERVAL)

                try:
                    status_resp = await client.get(
                        status_url,
                        headers={"Authorization": f"Key {self.api_key}"},
                        timeout=10.0,
                    )
                except httpx.TimeoutException:
                    continue

                if status_resp.status_code >= 500:
                    raise ProviderUnavailableError(
                        f"Status check HTTP {status_resp.status_code}", provider=self.name
                    )

                status_data = status_resp.json()
                status = status_data.get("status", "")

                if status == "COMPLETED":
                    result_resp = await client.get(
                        result_url,
                        headers={"Authorization": f"Key {self.api_key}"},
                        timeout=10.0,
                    )
                    result = result_resp.json()
                    image_url = result["images"][0]["url"]
                    return GenerationResult(
                        image_url=image_url,
                        provider=self.name,
                        model=model,
                        latency_ms=(time.monotonic() - start) * 1000,
                    )
                elif status == "FAILED":
                    raise JobFailedError(
                        status_data.get("error", "Job failed"), provider=self.name
                    )

    async def submit_async(
        self,
        prompt: str,
        model: str,
        webhook_url: str,
        **kwargs,
    ) -> SubmitResult:
        fal_model = self._resolve_model(model)
        body = self._build_input(fal_model, prompt, dict(kwargs))

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"https://queue.fal.run/{fal_model}",
                    params={"fal_webhook": webhook_url},
                    headers={
                        "Authorization": f"Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=30.0,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                raise ProviderUnavailableError(str(e), provider=self.name)

        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"HTTP {resp.status_code}", provider=self.name, status_code=resp.status_code
            )
        if resp.status_code >= 400:
            raise JobFailedError(
                resp.text, provider=self.name, status_code=resp.status_code
            )

        rbody = resp.json()
        request_id = rbody.get("request_id")
        if not request_id:
            raise JobFailedError("fal response missing request_id", provider=self.name)
        return SubmitResult(provider_job_id=request_id, raw=rbody)

    @staticmethod
    def parse_callback(headers: Dict[str, str], body: bytes) -> CallbackPayload:
        data = json.loads(body)
        request_id = data.get("request_id") or data.get("gateway_request_id")
        status = data.get("status")
        if status == "OK":
            payload = data.get("payload") or {}
            images = payload.get("images") or []
            image_url = images[0]["url"] if images else None
            return CallbackPayload(
                provider_job_id=request_id,
                status="succeeded",
                image_url=image_url,
                raw=data,
            )
        return CallbackPayload(
            provider_job_id=request_id,
            status="failed",
            error=str(data.get("payload") or data.get("error") or "fal returned ERROR"),
            raw=data,
        )
