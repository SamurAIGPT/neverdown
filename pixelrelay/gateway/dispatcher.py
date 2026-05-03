"""Job submission + failover orchestration shared by the HTTP routes and the worker."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from ..exceptions import (
    JobFailedError,
    JobTimeoutError,
    ProviderUnavailableError,
)
from ..models import is_image_edit, providers_for
from ..providers.base import BaseProvider
from .config import GatewayConfig
from .models import Job
from .stores.base import CooldownStore, JobStore
from .webhook_forward import forward_to_user_webhook

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Dispatcher:
    """Owns the submit-then-failover logic. Used by routes (initial submit) and the
    background worker (failover after deadline)."""

    def __init__(
        self,
        *,
        config: GatewayConfig,
        providers: Dict[str, BaseProvider],
        jobs: JobStore,
        cooldowns: CooldownStore,
    ):
        self.config = config
        self.providers = providers
        self.jobs = jobs
        self.cooldowns = cooldowns

    def _callback_url(self, provider: str, job_id: str) -> str:
        return f"{self.config.public_url.rstrip('/')}/v1/callback/{provider}/{job_id}"

    async def submit_next_provider(self, job: Job) -> Job:
        """Try providers in `providers_remaining` order until one accepts the job
        or all have been tried. Updates the job in the store accordingly."""
        now = _utcnow()

        # Validate image-edit models have an input image. Fail fast so the user
        # gets a clear error instead of a provider 4xx after a wasted submit.
        if is_image_edit(job.model) and not (job.extra or {}).get("input_image"):
            await self.jobs.mark_failed(
                job.id,
                error=(
                    f"model '{job.model}' is an image-edit model and requires "
                    "'input_image' (URL or data URI) in the request"
                ),
            )
            await self._maybe_forward_webhook(job.id)
            updated = await self.jobs.get_job(job.id)
            assert updated is not None
            return updated

        # If the model is in the registry, only try providers that actually serve it.
        # Unknown models pass through (empty set means "no opinion, let them all try").
        supported = providers_for(job.model)
        for provider_name in list(job.providers_remaining or []):
            if provider_name not in self.providers:
                await self.jobs.add_attempt(
                    job.id,
                    {"provider": provider_name, "error": "provider not configured", "at": now.isoformat()},
                )
                continue

            if supported and provider_name not in supported:
                await self.jobs.add_attempt(
                    job.id,
                    {
                        "provider": provider_name,
                        "error": f"model '{job.model}' not available on {provider_name}",
                        "at": now.isoformat(),
                    },
                )
                continue

            if await self.cooldowns.is_in_cooldown(provider_name, now=now):
                await self.jobs.add_attempt(
                    job.id,
                    {"provider": provider_name, "error": "in cooldown", "at": now.isoformat()},
                )
                continue

            provider = self.providers[provider_name]
            callback_url = self._callback_url(provider_name, job.id)
            try:
                result = await provider.submit_async(
                    prompt=job.prompt,
                    model=job.model,
                    webhook_url=callback_url,
                    **(job.extra or {}),
                )
            except ProviderUnavailableError as e:
                await self.cooldowns.mark_cooldown(
                    provider_name,
                    expires_at=_utcnow() + timedelta(seconds=self.config.cooldown_seconds),
                )
                await self.jobs.add_attempt(
                    job.id,
                    {
                        "provider": provider_name,
                        "error": f"unavailable: {e}",
                        "cooldown": True,
                        "at": now.isoformat(),
                    },
                )
                continue
            except (JobFailedError, JobTimeoutError) as e:
                await self.jobs.add_attempt(
                    job.id,
                    {"provider": provider_name, "error": f"{type(e).__name__}: {e}", "at": now.isoformat()},
                )
                continue
            except Exception as e:
                logger.exception("Unexpected error submitting to %s", provider_name)
                await self.jobs.add_attempt(
                    job.id,
                    {"provider": provider_name, "error": f"unexpected: {e}", "at": now.isoformat()},
                )
                continue

            deadline = _utcnow() + timedelta(seconds=self.config.job_deadline_seconds)
            await self.jobs.mark_submitted(
                job.id,
                provider=provider_name,
                provider_job_id=result.provider_job_id,
                deadline_at=deadline,
            )
            # Re-fetch to return canonical state
            updated = await self.jobs.get_job(job.id)
            assert updated is not None
            return updated

        # All providers exhausted
        await self.jobs.mark_failed(
            job.id, error="all providers failed or in cooldown"
        )
        await self._maybe_forward_webhook(job.id)
        updated = await self.jobs.get_job(job.id)
        assert updated is not None
        return updated

    async def handle_callback_succeeded(
        self, job_id: str, image_url: str
    ) -> None:
        transitioned = await self.jobs.mark_succeeded(job_id, image_url=image_url)
        if transitioned:
            await self._maybe_forward_webhook(job_id)

    async def handle_callback_failed(self, job_id: str, error: str) -> None:
        """Provider says the job failed. Treat as JobFailedError — no cooldown,
        try next provider."""
        job = await self.jobs.get_job(job_id)
        if job is None or job.status in ("succeeded", "failed"):
            return
        await self.jobs.add_attempt(
            job_id,
            {
                "provider": job.provider,
                "error": f"provider reported: {error}",
                "at": _utcnow().isoformat(),
            },
        )
        # Re-fetch to get updated providers_remaining
        job = await self.jobs.get_job(job_id)
        assert job is not None
        await self.submit_next_provider(job)
        if job.status == "failed":
            await self._maybe_forward_webhook(job_id)

    async def handle_deadline_exceeded(self, job: Job) -> None:
        """Background worker calls this when a submitted job has passed its deadline.
        Treat as ProviderUnavailableError — cooldown current provider, try next."""
        if job.status != "submitted" or not job.provider:
            return
        await self.cooldowns.mark_cooldown(
            job.provider,
            expires_at=_utcnow() + timedelta(seconds=self.config.cooldown_seconds),
        )
        await self.jobs.add_attempt(
            job.id,
            {
                "provider": job.provider,
                "error": "deadline exceeded — provider may be down",
                "cooldown": True,
                "at": _utcnow().isoformat(),
            },
        )
        refreshed = await self.jobs.get_job(job.id)
        if refreshed is None:
            return
        await self.submit_next_provider(refreshed)
        final = await self.jobs.get_job(job.id)
        if final is not None and final.status == "failed":
            await self._maybe_forward_webhook(job.id)

    async def wait_for_terminal(self, job_id: str, *, timeout_s: float) -> Optional[Job]:
        """Poll the store until the job reaches a terminal state or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            job = await self.jobs.get_job(job_id)
            if job is None:
                return None
            if job.status in ("succeeded", "failed"):
                return job
            if asyncio.get_event_loop().time() >= deadline:
                return job
            await asyncio.sleep(0.5)

    async def _maybe_forward_webhook(self, job_id: str) -> None:
        job = await self.jobs.get_job(job_id)
        if job is None or not job.webhook_url:
            return
        payload = {
            "job_id": job.id,
            "status": job.status,
            "provider": job.provider,
            "model": job.model,
            "image_url": job.image_url,
            "error": job.error,
            "attempts": job.attempts,
        }
        await forward_to_user_webhook(
            job.webhook_url, payload, secret=self.config.user_webhook_secret
        )
