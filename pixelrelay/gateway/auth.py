"""Bearer-token auth for the gateway. Required by default; PIXELRELAY_AUTH=none disables."""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, status

from .config import GatewayConfig


def make_auth_dependency(config: GatewayConfig):
    """Returns a FastAPI dependency that enforces Authorization: Bearer <key>."""

    async def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
        if config.auth_disabled:
            return
        expected = config.gateway_api_key or ""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid Authorization header",
            )
        token = authorization.split(" ", 1)[1].strip()
        if not hmac.compare_digest(token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )

    return require_auth
