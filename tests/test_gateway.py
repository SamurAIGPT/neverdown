"""End-to-end smoke test for the gateway.

Spins the FastAPI app up against an in-memory SQLite store, mocks both providers'
submit_async + parse_callback, and walks through the happy path:

  POST /v1/generate -> returns job_id, status=submitted, provider=fal
  POST /v1/callback/fal/{job_id} (signed body) -> job becomes succeeded, image_url set
  GET  /v1/jobs/{job_id} -> reflects the terminal state

Failover path:

  Configure dispatcher to make fal raise ProviderUnavailableError -> next provider
  (replicate) is tried -> succeeds via callback.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from pixelrelay.exceptions import ProviderUnavailableError
from pixelrelay.gateway.config import GatewayConfig
from pixelrelay.gateway.server import create_app
from pixelrelay.providers.base import CallbackPayload, SubmitResult


@pytest.fixture
def tmp_db_path():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d) / "test.db"


def _make_config(db_path: Path, *, with_openai: bool = False) -> GatewayConfig:
    return GatewayConfig(
        gateway_api_key="test-key",
        auth_disabled=False,
        database_url=f"sqlite+aiosqlite:///{db_path}",
        public_url="http://test-gateway",
        fal_key="fake-fal-key",
        replicate_token="fake-replicate-token",
        openai_key="fake-openai-key" if with_openai else None,
        default_providers=["fal", "replicate"],
        fal_webhook_public_key=None,  # disable verification in tests
        replicate_webhook_secret=None,
        user_webhook_secret="test-webhook-secret",
        job_deadline_seconds=10.0,
        failover_scan_interval_seconds=60.0,
        cooldown_seconds=60.0,
    )


def _auth_headers() -> dict:
    return {"Authorization": "Bearer test-key"}


def test_health_endpoint_unauthenticated(tmp_db_path):
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_generate_then_callback_succeeds(tmp_db_path):
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        # Mock fal.submit_async to accept the job
        fal_provider = app.state.providers["fal"]
        fal_provider.submit_async = AsyncMock(
            return_value=SubmitResult(provider_job_id="fal-req-123", raw={})
        )

        resp = client.post(
            "/v1/generate",
            headers=_auth_headers(),
            json={"prompt": "a cat in space", "model": "flux-dev"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "submitted"
        assert data["provider"] == "fal"
        job_id = data["job_id"]

        # Simulate fal POSTing the success callback
        callback_body = json.dumps(
            {
                "request_id": "fal-req-123",
                "status": "OK",
                "payload": {"images": [{"url": "https://example.com/img.png"}]},
            }
        )
        cb_resp = client.post(
            f"/v1/callback/fal/{job_id}",
            content=callback_body,
            headers={"Content-Type": "application/json"},
        )
        assert cb_resp.status_code == 200
        assert cb_resp.json() == {"ok": True}

        # The job should now be terminal
        get_resp = client.get(f"/v1/jobs/{job_id}", headers=_auth_headers())
        assert get_resp.status_code == 200
        result = get_resp.json()
        assert result["status"] == "succeeded"
        assert result["image_url"] == "https://example.com/img.png"


def test_generate_falls_over_to_replicate_when_fal_unavailable(tmp_db_path):
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        fal_provider = app.state.providers["fal"]
        replicate_provider = app.state.providers["replicate"]
        fal_provider.submit_async = AsyncMock(
            side_effect=ProviderUnavailableError("fal is down", provider="fal")
        )
        replicate_provider.submit_async = AsyncMock(
            return_value=SubmitResult(provider_job_id="rp-pred-456", raw={})
        )

        resp = client.post(
            "/v1/generate",
            headers=_auth_headers(),
            json={"prompt": "a dog on mars", "model": "flux-dev"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "submitted"
        assert data["provider"] == "replicate"
        # fal cooldown should be recorded as an attempt
        attempts = data["attempts"]
        assert any(a["provider"] == "fal" and a.get("cooldown") for a in attempts)


def test_generate_all_providers_fail(tmp_db_path):
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        fal_provider = app.state.providers["fal"]
        replicate_provider = app.state.providers["replicate"]
        fal_provider.submit_async = AsyncMock(
            side_effect=ProviderUnavailableError("fal is down", provider="fal")
        )
        replicate_provider.submit_async = AsyncMock(
            side_effect=ProviderUnavailableError("replicate is down", provider="replicate")
        )

        resp = client.post(
            "/v1/generate",
            headers=_auth_headers(),
            json={"prompt": "test", "model": "flux-dev"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert "all providers failed" in (data["error"] or "")


def test_auth_required(tmp_db_path):
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        resp = client.post(
            "/v1/generate", json={"prompt": "x", "model": "flux-dev"}
        )
        assert resp.status_code == 401


def test_image_edit_model_requires_input_image(tmp_db_path):
    """Asking for an image-edit model without input_image fails fast with a clear error
    instead of wasting a provider submit on a malformed request."""
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        # No need to mock providers — we should never reach them
        resp = client.post(
            "/v1/generate",
            headers=_auth_headers(),
            json={"prompt": "make it look like a watercolor", "model": "flux-kontext-pro"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert "input_image" in (data["error"] or "")


def test_image_edit_model_with_input_image_submits(tmp_db_path):
    """When input_image is provided, the gateway forwards it to the provider as
    the field name that provider expects (image_url for Fal Kontext)."""
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        fal_provider = app.state.providers["fal"]
        captured = {}

        async def mock_submit(prompt, model, webhook_url, **kwargs):
            captured["prompt"] = prompt
            captured["model"] = model
            captured["kwargs"] = kwargs
            return SubmitResult(provider_job_id="kontext-req-1", raw={})

        fal_provider.submit_async = mock_submit

        resp = client.post(
            "/v1/generate",
            headers=_auth_headers(),
            json={
                "prompt": "make it look like a watercolor",
                "model": "flux-kontext-pro",
                "input_image": "https://example.com/source.png",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "submitted"
        assert data["provider"] == "fal"
        # The dispatcher folded input_image into Job.extra and passed it through;
        # the provider received it under the canonical "input_image" key.
        assert captured["kwargs"].get("input_image") == "https://example.com/source.png"


def test_openai_provider_registered_when_key_set(tmp_db_path):
    """OpenAI shows up in the provider registry when OPENAI_API_KEY is configured."""
    app = create_app(_make_config(tmp_db_path, with_openai=True))
    with TestClient(app) as client:
        assert "openai" in app.state.providers
        # Health should still work
        assert client.get("/health").status_code == 200


def test_openai_self_callback_completes_job(tmp_db_path):
    """End-to-end: OpenAI provider submits, simulates the self-callback, job
    becomes succeeded. We mock submit_async to skip the real OpenAI call but
    still drive the rest of the flow through the actual /v1/callback/openai/
    route, which is the codepath the background task hits in production."""
    app = create_app(_make_config(tmp_db_path, with_openai=True))
    with TestClient(app) as client:
        openai_provider = app.state.providers["openai"]
        openai_provider.submit_async = AsyncMock(
            return_value=SubmitResult(provider_job_id="openai-abc123", raw={})
        )

        resp = client.post(
            "/v1/generate",
            headers=_auth_headers(),
            json={
                "prompt": "a watercolor painting",
                "model": "gpt-image-1",
                "providers": ["openai"],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "submitted"
        assert data["provider"] == "openai"
        job_id = data["job_id"]

        # Simulate the background task POSTing the result back to ourselves.
        cb_resp = client.post(
            f"/v1/callback/openai/{job_id}",
            json={
                "provider_job_id": "openai-abc123",
                "status": "succeeded",
                "image_url": "https://oaidalleapiprodscus.blob.core.windows.net/result.png",
            },
        )
        assert cb_resp.status_code == 200

        get_resp = client.get(f"/v1/jobs/{job_id}", headers=_auth_headers())
        assert get_resp.json()["status"] == "succeeded"
        assert "oaidalleapiprodscus" in get_resp.json()["image_url"]


def test_text_to_image_model_ignores_input_image(tmp_db_path):
    """Non-edit models that happen to receive input_image shouldn't fail — the field
    just flows through to the provider, which will ignore it (or accept as img2img
    seed if the model happens to support that)."""
    app = create_app(_make_config(tmp_db_path))
    with TestClient(app) as client:
        fal_provider = app.state.providers["fal"]
        fal_provider.submit_async = AsyncMock(
            return_value=SubmitResult(provider_job_id="r1", raw={})
        )

        resp = client.post(
            "/v1/generate",
            headers=_auth_headers(),
            json={
                "prompt": "a cat in space",
                "model": "flux-dev",
                "input_image": "https://example.com/ignored.png",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should succeed — the model isn't image-edit, so no validation fires.
        assert data["status"] == "submitted"
