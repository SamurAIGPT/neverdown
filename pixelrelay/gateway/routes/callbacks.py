"""Provider webhook receivers. One route per provider so signature verification
can be specific to each."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..webhook_verify import verify_fal, verify_replicate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/callback/fal/{job_id}")
async def fal_callback(job_id: str, request: Request) -> dict:
    return await _handle_callback(request, job_id, provider_name="fal")


@router.post("/v1/callback/replicate/{job_id}")
async def replicate_callback(job_id: str, request: Request) -> dict:
    return await _handle_callback(request, job_id, provider_name="replicate")


@router.post("/v1/callback/openai/{job_id}")
async def openai_callback(job_id: str, request: Request) -> dict:
    """OpenAI is a sync API with no webhooks. The gateway's own background task
    POSTs here after calling OpenAI; we trust the loopback. Anyone who wanted
    to spoof a callback would need to guess an active UUID job_id, which is
    impractical. For multi-tenant deployments, restrict callback paths via a
    reverse proxy."""
    return await _handle_callback(request, job_id, provider_name="openai")


async def _handle_callback(request: Request, job_id: str, *, provider_name: str) -> dict:
    config = request.app.state.config
    dispatcher = request.app.state.dispatcher
    providers = request.app.state.providers

    body = await request.body()
    headers = dict(request.headers)

    if provider_name == "fal":
        if not verify_fal(headers, body, config.fal_webhook_public_key):
            raise HTTPException(status_code=401, detail="invalid fal signature")
    elif provider_name == "replicate":
        if not verify_replicate(headers, body, config.replicate_webhook_secret):
            raise HTTPException(status_code=401, detail="invalid replicate signature")
    elif provider_name == "openai":
        # Self-callback from the gateway's own background task. The unguessable
        # UUID job_id in the path is the auth boundary; no external signature
        # to verify since OpenAI doesn't sign anything.
        pass

    provider = providers.get(provider_name)
    if provider is None:
        raise HTTPException(status_code=400, detail=f"provider {provider_name} not configured")

    try:
        callback = provider.parse_callback(headers, body)
    except Exception as e:
        logger.exception("Failed to parse %s callback for job %s", provider_name, job_id)
        raise HTTPException(status_code=400, detail=f"unparseable callback: {e}")

    if callback.status == "succeeded" and callback.image_url:
        await dispatcher.handle_callback_succeeded(job_id, callback.image_url)
    else:
        await dispatcher.handle_callback_failed(job_id, callback.error or "unknown error")

    return {"ok": True}
