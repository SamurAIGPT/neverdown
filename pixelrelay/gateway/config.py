"""Gateway runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GatewayConfig:
    # Auth — required by default; set PIXELRELAY_AUTH=none to disable for local dev
    gateway_api_key: Optional[str]
    auth_disabled: bool

    # Persistence — sqlite default, postgres via DATABASE_URL
    database_url: str

    # Public URL where this gateway is reachable from the providers' perspective
    # (used to construct callback webhook URLs we hand to fal/replicate)
    public_url: str

    # Provider API keys (BYOK)
    fal_key: Optional[str]
    replicate_token: Optional[str]
    openai_key: Optional[str]
    google_key: Optional[str]

    # Default provider order
    default_providers: List[str]

    # Webhook signature config
    fal_webhook_public_key: Optional[str]
    replicate_webhook_secret: Optional[str]
    user_webhook_secret: str

    # Timeouts and retry behavior
    job_deadline_seconds: float
    failover_scan_interval_seconds: float
    cooldown_seconds: float

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        auth_value = os.environ.get("PIXELRELAY_AUTH", "").lower()
        return cls(
            gateway_api_key=os.environ.get("PIXELRELAY_GATEWAY_KEY"),
            auth_disabled=(auth_value == "none"),
            database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./pixelrelay.db"),
            public_url=os.environ.get("PIXELRELAY_PUBLIC_URL", "http://localhost:8000"),
            fal_key=os.environ.get("FAL_KEY"),
            replicate_token=os.environ.get("REPLICATE_API_TOKEN"),
            openai_key=os.environ.get("OPENAI_API_KEY"),
            google_key=os.environ.get("GOOGLE_API_KEY"),
            default_providers=[
                p.strip()
                for p in os.environ.get("PIXELRELAY_PROVIDERS", "fal,replicate").split(",")
                if p.strip()
            ],
            fal_webhook_public_key=os.environ.get("FAL_WEBHOOK_PUBLIC_KEY"),
            replicate_webhook_secret=os.environ.get("REPLICATE_WEBHOOK_SECRET"),
            user_webhook_secret=os.environ.get(
                "PIXELRELAY_WEBHOOK_SECRET", "change-me-in-production"
            ),
            job_deadline_seconds=float(os.environ.get("PIXELRELAY_JOB_DEADLINE", "180")),
            failover_scan_interval_seconds=float(
                os.environ.get("PIXELRELAY_SCAN_INTERVAL", "5")
            ),
            cooldown_seconds=float(os.environ.get("PIXELRELAY_COOLDOWN", "60")),
        )

    def validate(self) -> None:
        if not self.auth_disabled and not self.gateway_api_key:
            raise RuntimeError(
                "PIXELRELAY_GATEWAY_KEY is required. "
                "Generate one with `openssl rand -hex 32`, or set PIXELRELAY_AUTH=none for local dev."
            )
        if not (self.fal_key or self.replicate_token or self.openai_key or self.google_key):
            raise RuntimeError(
                "At least one provider key is required "
                "(FAL_KEY, REPLICATE_API_TOKEN, OPENAI_API_KEY, or GOOGLE_API_KEY)."
            )
