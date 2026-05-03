"""SQLAlchemy models for jobs and provider cooldown state."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | submitted | succeeded | failed

    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    provider_job_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )

    model: Mapped[str] = mapped_column(String(100))
    prompt: Mapped[str] = mapped_column(String())
    extra: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    providers_remaining: Mapped[List[str]] = mapped_column(JSON, default=list)

    webhook_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)

    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(), nullable=True)
    attempts: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)

    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_jobs_status_deadline", "status", "deadline_at"),
    )


class ProviderCooldown(Base):
    __tablename__ = "provider_cooldowns"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
