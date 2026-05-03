"""SQLAlchemy-backed JobStore + CooldownStore. Works on SQLite or Postgres."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from ..models import Job, ProviderCooldown
from .base import CooldownStore, JobStore


class SqlJobStore(JobStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session = session_factory

    async def create_job(
        self,
        *,
        model: str,
        prompt: str,
        extra: Dict[str, Any],
        webhook_url: Optional[str],
        providers: List[str],
    ) -> Job:
        async with self._session() as session:
            job = Job(
                model=model,
                prompt=prompt,
                extra=extra,
                webhook_url=webhook_url,
                providers_remaining=providers,
                attempts=[],
            )
            session.add(job)
            await session.commit()
            return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self._session() as session:
            return await session.get(Job, job_id)

    async def find_by_provider_job_id(
        self, provider: str, provider_job_id: str
    ) -> Optional[Job]:
        async with self._session() as session:
            stmt = select(Job).where(
                Job.provider == provider, Job.provider_job_id == provider_job_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def mark_submitted(
        self,
        job_id: str,
        *,
        provider: str,
        provider_job_id: str,
        deadline_at: datetime,
    ) -> None:
        async with self._session() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status="submitted",
                    provider=provider,
                    provider_job_id=provider_job_id,
                    deadline_at=deadline_at,
                )
            )
            await session.commit()

    async def mark_succeeded(self, job_id: str, *, image_url: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_(("queued", "submitted")))
                .values(
                    status="succeeded",
                    image_url=image_url,
                    completed_at=_utcnow(),
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def mark_failed(self, job_id: str, *, error: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_(("queued", "submitted")))
                .values(
                    status="failed",
                    error=error,
                    completed_at=_utcnow(),
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def add_attempt(self, job_id: str, attempt: Dict[str, Any]) -> None:
        async with self._session() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            attempts = list(job.attempts or [])
            attempts.append(attempt)
            job.attempts = attempts
            flag_modified(job, "attempts")
            # Pop the just-tried provider from providers_remaining
            remaining = list(job.providers_remaining or [])
            if remaining and remaining[0] == attempt.get("provider"):
                remaining.pop(0)
                job.providers_remaining = remaining
                flag_modified(job, "providers_remaining")
            await session.commit()

    async def list_stale_submitted(self, *, now: datetime) -> List[Job]:
        async with self._session() as session:
            stmt = select(Job).where(
                Job.status == "submitted", Job.deadline_at < now
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_recent(self, *, limit: int = 50) -> List[Job]:
        async with self._session() as session:
            stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())


class SqlCooldownStore(CooldownStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session = session_factory

    async def mark_cooldown(self, provider: str, *, expires_at: datetime) -> None:
        async with self._session() as session:
            existing = await session.get(ProviderCooldown, provider)
            if existing is None:
                session.add(ProviderCooldown(provider=provider, expires_at=expires_at))
            else:
                existing.expires_at = expires_at
            await session.commit()

    async def is_in_cooldown(self, provider: str, *, now: datetime) -> bool:
        async with self._session() as session:
            row = await session.get(ProviderCooldown, provider)
            if row is None:
                return False
            if row.expires_at <= now:
                await session.execute(
                    delete(ProviderCooldown).where(ProviderCooldown.provider == provider)
                )
                await session.commit()
                return False
            return True

    async def cooled_providers(self, *, now: datetime) -> List[str]:
        async with self._session() as session:
            stmt = select(ProviderCooldown).where(ProviderCooldown.expires_at > now)
            result = await session.execute(stmt)
            return [row.provider for row in result.scalars().all()]


def _utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
