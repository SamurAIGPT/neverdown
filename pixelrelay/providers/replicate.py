import asyncio
import time
import httpx

from .base import BaseProvider, GenerationResult
from ..exceptions import ProviderUnavailableError, JobFailedError, JobTimeoutError

# Replicate model version map
REPLICATE_MODEL_MAP = {
    "flux-dev": "black-forest-labs/flux-dev",
    "flux-schnell": "black-forest-labs/flux-schnell",
    "flux-pro": "black-forest-labs/flux-pro",
}

POLL_INTERVAL = 2.0


class ReplicateProvider(BaseProvider):
    name = "replicate"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def generate(
        self,
        prompt: str,
        model: str,
        timeout: float = 120.0,
        **kwargs,
    ) -> GenerationResult:
        replicate_model = REPLICATE_MODEL_MAP.get(model, model)
        start = time.monotonic()

        async with httpx.AsyncClient() as client:
            # Submit job
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
                    f"HTTP {submit_resp.status_code}", provider=self.name, status_code=submit_resp.status_code
                )
            if submit_resp.status_code >= 400:
                raise JobFailedError(
                    submit_resp.text, provider=self.name, status_code=submit_resp.status_code
                )

            job = submit_resp.json()
            prediction_url = job["urls"]["get"]

            # Poll for completion
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    raise JobTimeoutError(
                        f"Exceeded {timeout}s timeout", provider=self.name
                    )

                await asyncio.sleep(POLL_INTERVAL)

                try:
                    poll_resp = await client.get(
                        prediction_url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=10.0,
                    )
                except httpx.TimeoutException:
                    continue  # transient, keep polling

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

                # starting or processing — keep polling
