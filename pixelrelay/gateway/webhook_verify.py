"""Webhook signature verification — one function per provider.

Both providers can be configured to skip verification by leaving the relevant secret/key
unset, but the gateway logs a warning at startup. Production deployments should always
configure verification.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Mapping, Optional

logger = logging.getLogger(__name__)


def verify_replicate(
    headers: Mapping[str, str],
    body: bytes,
    signing_secret: Optional[str],
) -> bool:
    """Verify a Replicate webhook using the Standard Webhooks scheme (HMAC-SHA256).

    Replicate sends three headers: webhook-id, webhook-timestamp, webhook-signature.
    The signed content is `{id}.{timestamp}.{body}`. The secret is fetched once from
    https://api.replicate.com/v1/webhooks/default/secret and looks like `whsec_...`.
    """
    if not signing_secret:
        logger.warning("Replicate webhook signature verification disabled (no secret configured)")
        return True

    msg_id = _header(headers, "webhook-id")
    msg_ts = _header(headers, "webhook-timestamp")
    msg_sig = _header(headers, "webhook-signature")
    if not (msg_id and msg_ts and msg_sig):
        return False

    try:
        raw_secret = base64.b64decode(signing_secret.removeprefix("whsec_"))
    except Exception:
        return False

    signed = f"{msg_id}.{msg_ts}.".encode() + body
    expected = base64.b64encode(hmac.new(raw_secret, signed, hashlib.sha256).digest()).decode()

    # webhook-signature looks like "v1,abc123 v1,def456" — any one matching is enough.
    for part in msg_sig.split():
        _, _, sig = part.partition(",")
        if hmac.compare_digest(sig, expected):
            return True
    return False


def verify_fal(
    headers: Mapping[str, str],
    body: bytes,
    public_key_hex: Optional[str],
) -> bool:
    """Verify a fal.ai webhook (ed25519).

    fal sends signature in `x-fal-webhook-signature` (hex). The public key can be obtained
    from fal's JWKS endpoint and configured via the FAL_WEBHOOK_PUBLIC_KEY env var.

    NOTE: in v0.2.0 we require the operator to configure the trusted public key out of band.
    A future version will fetch and cache fal's public key automatically.
    """
    if not public_key_hex:
        logger.warning("Fal webhook signature verification disabled (no public key configured)")
        return True

    sig_hex = _header(headers, "x-fal-webhook-signature")
    if not sig_hex:
        return False

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        try:
            pubkey.verify(bytes.fromhex(sig_hex), body)
            return True
        except InvalidSignature:
            return False
    except Exception as e:
        logger.exception("Fal signature verification raised: %s", e)
        return False


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup."""
    if name in headers:
        return headers[name]
    lower = name.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v
    return None
