import asyncio
import json
import time
from typing import Dict

import httpx

from .base import BaseProvider, CallbackPayload, GenerationResult, SubmitResult
from ..exceptions import JobFailedError, JobTimeoutError, ProviderUnavailableError
from ..models import resolve_for_provider

POLL_INTERVAL = 2.0


class ReplicateProvider(BaseProvider):
    name = "replicate"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _resolve_model(self, model: str) -> str:
        return resolve_for_provider(model, self.name)

    async def generate(
        self,
        prompt: str,
        model: str,
        timeout: float = 120.0,
        **kwargs,
    ) -> GenerationResult:
        replicate_model = self._resolve_model(model)
        start = time.monotonic()

        async with httpx.AsyncClient() as client:
            try:
                submit_resp = await client.post(
                    f"https://api.replicate.com/v1/models/{replicate_model}/predictions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Prefer": "respond-async",
                    },
                    json={"input": {"prompt": prompt, **kwargs}},
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
            prediction_url = job["urls"]["get"]

            while True:
                if time.monotonic() - start >= timeout:
                    raise JobTimeoutError(f"Exceeded {timeout}s timeout", provider=self.name)

                await asyncio.sleep(POLL_INTERVAL)

                try:
                    poll_resp = await client.get(
                        prediction_url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=10.0,
                    )
                except httpx.TimeoutException:
                    continue

                if poll_resp.status_code >= 500:
                    raise ProviderUnavailableError(
                        f"Poll HTTP {poll_resp.status_code}", provider=self.name
                    )

                data = poll_resp.json()
                status = data.get("status", "")

                if status == "succeeded":
                    output = data.get("output", [])
                    image_url = output[0] if isinstance(output, list) else output
                    return GenerationResult(
                        image_url=image_url,
                        provider=self.name,
                        model=model,
                        latency_ms=(time.monotonic() - start) * 1000,
                    )
                elif status == "failed":
                    raise JobFailedError(
                        data.get("error", "Job failed"), provider=self.name
                    )

    async def submit_async(
        self,
        prompt: str,
        model: str,
        webhook_url: str,
        **kwargs,
    ) -> SubmitResult:
        replicate_model = self._resolve_model(model)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"https://api.replicate.com/v1/models/{replicate_model}/predictions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "input": {"prompt": prompt, **kwargs},
                        "webhook": webhook_url,
                        "webhook_events_filter": ["completed"],
                    },
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

        body = resp.json()
        prediction_id = body.get("id")
        if not prediction_id:
            raise JobFailedError("replicate response missing id", provider=self.name)
        return SubmitResult(provider_job_id=prediction_id, raw=body)

    @staticmethod
    def parse_callback(headers: Dict[str, str], body: bytes) -> CallbackPayload:
        data = json.loads(body)
        prediction_id = data.get("id")
        status = data.get("status")
        if status == "succeeded":
            output = data.get("output") or []
            image_url = output[0] if isinstance(output, list) and output else (output if isinstance(output, str) else None)
            return CallbackPayload(
                provider_job_id=prediction_id,
                status="succeeded",
                image_url=image_url,
                raw=data,
            )
        return CallbackPayload(
            provider_job_id=prediction_id,
            status="failed",
            error=str(data.get("error") or f"replicate status: {status}"),
            raw=data,
        )
