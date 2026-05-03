"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    prompt: str
    model: str = "flux-dev"
    providers: Optional[List[str]] = None
    webhook_url: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    job_id: str
    status: str
    provider: Optional[str] = None
    model: str
    prompt: str
    image_url: Optional[str] = None
    error: Optional[str] = None
    attempts: List[Dict[str, Any]] = Field(default_factory=list)
    webhook_url: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
