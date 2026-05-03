"""Abstract job & cooldown stores. Pluggable so SQLite/Postgres are interchangeable."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models import Job


class JobStore(ABC):
    @abstractmethod
    async def create_job(
        self,
        *,
        model: str,
        prompt: str,
        extra: Dict[str, Any],
        webhook_url: Optional[str],
        providers: List[str],
    ) -> Job: ...

    @abstractmethod
    async def get_job(self, job_id: str) -> Optional[Job]: ...

    @abstractmethod
    async def find_by_provider_job_id(
        self, provider: str, provider_job_id: str
    ) -> Optional[Job]: ...

    @abstractmethod
    async def mark_submitted(
        self,
        job_id: str,
        *,
        provider: str,
        provider_job_id: str,
        deadline_at: datetime,
    ) -> None: ...

    @abstractmethod
    async def mark_succeeded(
        self,
        job_id: str,
        *,
        image_url: str,
    ) -> bool:
        """Returns True if this call transitioned the job; False if already terminal (idempotency)."""
        ...

    @abstractmethod
    async def mark_failed(self, job_id: str, *, error: str) -> bool: ...

    @abstractmethod
    async def add_attempt(self, job_id: str, attempt: Dict[str, Any]) -> None: ...

    @abstractmethod
    async def list_stale_submitted(self, *, now: datetime) -> List[Job]: ...

    @abstractmethod
    async def list_recent(self, *, limit: int = 50) -> List[Job]: ...


class CooldownStore(ABC):
    @abstractmethod
    async def mark_cooldown(self, provider: str, *, expires_at: datetime) -> None: ...

    @abstractmethod
    async def is_in_cooldown(self, provider: str, *, now: datetime) -> bool: ...

    @abstractmethod
    async def cooled_providers(self, *, now: datetime) -> List[str]: ...
