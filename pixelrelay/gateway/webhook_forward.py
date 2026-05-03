"""Forward a completed job to the user's webhook URL with HMAC-SHA256 signature."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)


async def forward_to_user_webhook(
    user_webhook_url: str,
    payload: Dict[str, Any],
    secret: str,
) -> None:
    """POST payload to the user's webhook with X-Pixelrelay-Signature header.

    Signature scheme: HMAC-SHA256 over `{timestamp}.{body}`, headers:
      - X-Pixelrelay-Timestamp: unix timestamp seconds
      - X-Pixelrelay-Signature: hex-encoded HMAC-SHA256
    """
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ts = str(int(time.time()))
    signed = f"{ts}.".encode() + body
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Pixelrelay-Timestamp": ts,
        "X-Pixelrelay-Signature": sig,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(user_webhook_url, content=body, headers=headers)
            if resp.status_code >= 400:
                logger.warning("User webhook returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Failed to deliver user webhook %s: %s", user_webhook_url, e)
