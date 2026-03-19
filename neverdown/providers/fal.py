import asyncio
import time
import httpx

from .base import BaseProvider, GenerationResult
from ..exceptions import ProviderUnavailableError, JobFailedError, JobTimeoutError

# fal model slug mapping
FAL_MODEL_MAP = {
    "flux-dev": "fal-ai/flux/dev",
    "flux-schnell": "fal-ai/flux/schnell",
    "flux-pro": "fal-ai/flux-pro",
}

POLL_INTERVAL = 2.0  # seconds between polls


class FalProvider(BaseProvider):
    name = "fal"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def generate(
        self,
        prompt: str,
        model: str,
        timeout: float = 120.0,
        **kwargs,
    ) -> GenerationResult:
        fal_model = FAL_MODEL_MAP.get(model, model)
        start = time.monotonic()

        async with httpx.AsyncClient() as client:
            # Submit job
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
            except httpx.TimeoutException as e:
                raise ProviderUnavailableError("Connection timeout", provider=self.name)

            if submit_resp.status_code >= 500:
                raise ProviderUnavailableError(
                    f"HTTP {submit_resp.status_code}", provider=self.name, status_code=submit_resp.status_code
                )
            if submit_resp.status_code >= 400:
                raise JobFailedError(
                    submit_resp.text, provider=self.name, status_code=submit_resp.status_code
                )

            job = submit_resp.json()
            request_id = job.get("request_id")
            status_url = job.get("status_url") or f"https://queue.fal.run/{fal_model}/requests/{request_id}/status"
            result_url = f"https://queue.fal.run/{fal_model}/requests/{request_id}"

            # Poll for completion
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    raise JobTimeoutError(
                        f"Exceeded {timeout}s timeout", provider=self.name
                    )

                await asyncio.sleep(POLL_INTERVAL)

                try:
                    status_resp = await client.get(
                        status_url,
                        headers={"Authorization": f"Key {self.api_key}"},
                        timeout=10.0,
                    )
                except httpx.TimeoutException:
                    continue  # transient, keep polling

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

                # IN_QUEUE or IN_PROGRESS — keep polling
