# Pixelrelay — Full Project Context

> This document is the canonical reference for the Pixelrelay project. Read this at the start of any new session before making changes.

> **Renamed from `neverdown` → `pixelrelay` in v0.1.0.** The new name signals media-API specialization (pixel) and the failover handoff (relay), avoiding LLM-gateway connotations.

> **v0.2.0 is the gateway-first release.** The product is a self-hosted gateway with webhooks + persistent state. The Python library still works in-process via polling, but is recommended only for scripts.

---

## What is Pixelrelay?

A **self-hosted failover gateway for generative media APIs** (Fal.ai, Replicate, future: RunPod, Runway, Kling). Bring your own provider keys, run the gateway next to your app, and never lose a job to a provider outage.

**Core insight:** A polling-only SDK fails at production scale because it holds a process open for the whole job duration (10–300s for video), loses state on restart, can't share cooldown across instances, and wastes the request budget waiting for a hung provider. A gateway with **webhooks + persistent job state** fixes all of this.

**Architecture (one paragraph):** User's app POSTs to gateway with optional `webhook_url`. Gateway persists the job in DB (SQLite default, Postgres for prod), submits to first available provider with `webhook=<gateway>/v1/callback/<provider>/<job_id>`, returns `{job_id, status: "submitted"}` immediately. When provider completes, it POSTs back; gateway verifies signature (ed25519 for fal, HMAC-SHA256 for replicate), updates DB, forwards to user's webhook with our own HMAC-SHA256 signature. A background worker scans for jobs past deadline and triggers failover transparently.

**GitHub:** https://github.com/SamurAIGPT/pixelrelay
**License:** Apache 2.0
**Language:** Python 3.9+
**Local path:** `/Users/anilchandranaidumatcha/Downloads/pixelrelay/`

---

## Current state (v0.2.0)

```python
# Library mode (in-process polling, scripts only)
from pixelrelay import generate
result = await generate(prompt="...", model="flux-dev", providers=["fal", "replicate"])

# Gateway mode (recommended for production)
# 1. Run: docker run pixelrelay
# 2. Call:
curl -X POST http://localhost:8000/v1/generate \
  -H "Authorization: Bearer $PIXELRELAY_GATEWAY_KEY" \
  -d '{"prompt": "...", "model": "flux-dev", "webhook_url": "https://your-app.example.com/cb"}'
```

---

## Project Structure

```
pixelrelay/
├── pixelrelay/                   # core library
│   ├── core.py                   # generate() — SDK library mode
│   ├── cooldown.py               # CooldownTracker (library mode only)
│   ├── exceptions.py             # PixelrelayError + ProviderError hierarchy
│   ├── providers/
│   │   ├── base.py               # BaseProvider, GenerationResult, SubmitResult, CallbackPayload
│   │   ├── fal.py                # generate() + submit_async() + parse_callback()
│   │   └── replicate.py          # generate() + submit_async() + parse_callback()
│   └── gateway/                  # OPTIONAL EXTRA — pip install "pixelrelay[gateway]"
│       ├── __main__.py           # `python -m pixelrelay.gateway` (uvicorn entry)
│       ├── server.py             # FastAPI app factory, lifespan, provider registry
│       ├── config.py             # GatewayConfig.from_env()
│       ├── auth.py               # Bearer-token dependency
│       ├── db.py                 # SQLAlchemy async engine + session factory + create_all
│       ├── models.py             # Job, ProviderCooldown ORM models
│       ├── schemas.py            # Pydantic request/response shapes
│       ├── dispatcher.py         # submit-then-failover orchestration; shared by routes + worker
│       ├── worker.py             # background failover loop (scan stale → cooldown → resubmit)
│       ├── webhook_verify.py     # verify_fal (ed25519), verify_replicate (HMAC-SHA256)
│       ├── webhook_forward.py    # POST to user's webhook with X-Pixelrelay-Signature
│       ├── stores/
│       │   ├── base.py           # JobStore + CooldownStore ABCs
│       │   └── sql.py            # Sql{Job,Cooldown}Store — works on SQLite or Postgres
│       └── routes/
│           ├── generate.py       # POST /v1/generate (?wait=), GET /v1/jobs/{id}, GET /v1/jobs
│           ├── callbacks.py      # POST /v1/callback/{provider}/{job_id}
│           └── health.py         # GET /health
├── docker/
│   ├── Dockerfile                # python:3.12-slim + pixelrelay[gateway], EXPOSE 8000
│   ├── docker-compose.yml        # gateway + commented-out postgres service
│   └── .env.example
├── tests/
│   ├── test_core.py              # SDK library mode (3 mocked tests)
│   └── test_gateway.py           # gateway end-to-end (5 tests, FastAPI TestClient)
├── pyproject.toml                # hatchling, [gateway] extras, pixelrelay-gateway entrypoint
└── README.md                     # gateway-first positioning
```

---

## How the gateway works

### Submit flow (`POST /v1/generate`)
1. Auth check (bearer token)
2. Create `Job` row in DB with `status=queued`, `providers_remaining=[fal, replicate]`
3. `Dispatcher.submit_next_provider(job)`:
   - Iterate `providers_remaining`, skip those in cooldown (DB lookup)
   - Call `provider.submit_async(prompt, model, webhook_url=callback_url)`
   - On `ProviderUnavailableError` → mark cooldown in DB, log attempt, continue
   - On `JobFailedError` → log attempt, continue
   - On success → `mark_submitted(provider, provider_job_id, deadline=now+180s)`, return job
4. If `?wait=true`, poll DB until terminal or deadline; else return immediately
5. Response: `JobResponse` with `job_id`, `status`, `provider`, `attempts`, etc.

### Callback flow (`POST /v1/callback/{provider}/{job_id}`)
1. Read raw body; verify signature per-provider (ed25519 for fal, HMAC-SHA256 for replicate)
2. `provider.parse_callback(headers, body)` → `CallbackPayload(status, image_url, error)`
3. If `status=succeeded`: `mark_succeeded(image_url)`; if user webhook: forward with HMAC sig
4. If `status=failed`: log attempt, `submit_next_provider(job)` (treat as JobFailedError, no cooldown)

### Failover worker (background task)
- Every `PIXELRELAY_SCAN_INTERVAL` (default 5s):
  - `list_stale_submitted(now)` → jobs with `status=submitted, deadline_at < now`
  - For each: mark current provider in cooldown (treat as ProviderUnavailableError), call `submit_next_provider`
  - If exhausted → `mark_failed`, forward webhook

### Persistence
- **JobStore + CooldownStore ABCs** — pluggable
- **`SqlJobStore` + `SqlCooldownStore`** — SQLAlchemy 2.0 async, works for both SQLite (`+aiosqlite`) and Postgres (`+asyncpg`)
- **Schema** — `jobs(id PK, status, provider, provider_job_id, model, prompt, extra JSON, providers_remaining JSON, webhook_url, image_url, error, attempts JSON, deadline_at, created_at, completed_at)` + index `(status, deadline_at)`. `provider_cooldowns(provider PK, expires_at)`.
- **Migrations** — for v0.2.0, `Base.metadata.create_all()` runs at startup. Alembic deferred to v0.3.

---

## Provider details

### Fal.ai (`providers/fal.py`)
- **Library `generate()`** — POST `queue.fal.run/{model}` then poll `status_url`
- **Gateway `submit_async()`** — POST `queue.fal.run/{model}?fal_webhook=<callback_url>`, returns `request_id`
- **Webhook verification** — ed25519. Header `x-fal-webhook-signature` (hex). Public key configured via `FAL_WEBHOOK_PUBLIC_KEY` env var (TODO: auto-fetch from fal JWKS in v0.3).
- **Callback payload** — `{request_id, status: "OK"|"ERROR", payload: {images: [{url}]}}`

### Replicate (`providers/replicate.py`)
- **Library `generate()`** — POST `api.replicate.com/v1/models/{model}/predictions` with `Prefer: respond-async`, then poll `urls.get`
- **Gateway `submit_async()`** — POST `.../predictions` with `{webhook, webhook_events_filter: ["completed"], input}`, returns `id`
- **Webhook verification** — HMAC-SHA256 (Standard Webhooks spec). Headers `webhook-id`, `webhook-timestamp`, `webhook-signature`. Signed payload `{id}.{timestamp}.{body}`. Secret via `REPLICATE_WEBHOOK_SECRET` env (`whsec_...` from `GET /v1/webhooks/default/secret`).
- **Callback payload** — `{id, status: "succeeded"|"failed", output: [url], ...}`

---

## Configuration matrix

See README "Configuration" section for the full env-var table. Key ones:

| Env | Purpose |
|---|---|
| `PIXELRELAY_GATEWAY_KEY` | Bearer token for client → gateway auth |
| `PIXELRELAY_PUBLIC_URL` | Where providers POST callbacks; must be public |
| `DATABASE_URL` | `sqlite+aiosqlite:///...` or `postgresql+asyncpg://...` |
| `FAL_KEY` / `REPLICATE_API_TOKEN` | BYOK provider keys |
| `FAL_WEBHOOK_PUBLIC_KEY` / `REPLICATE_WEBHOOK_SECRET` | Sig verification |
| `PIXELRELAY_WEBHOOK_SECRET` | HMAC for gateway → user webhook |
| `PIXELRELAY_JOB_DEADLINE` | Seconds before failover (default 180) |
| `PIXELRELAY_COOLDOWN` | Seconds to keep failed provider out (default 60) |

---

## Design decisions & rationale

| Decision | Rationale |
|---|---|
| Gateway-first, not SDK-first | Polling alone breaks at production scale (lifecycle, state sharing, request budget). The gateway IS the product. |
| BYOK self-hosted | No billing complexity for us; no compliance/data concerns for users; clean differentiation from Lumenfall/closed gateways. |
| Webhooks primary, polling as fallback | Both fal and replicate support webhooks natively. Polling stays for future providers that don't. |
| FastAPI + SQLAlchemy 2.0 async + httpx | All async-native, fits the polling+webhook pattern. |
| SQLite default, Postgres first-class via DATABASE_URL | Solo dev: zero config. Production: change one env var. Pluggable JobStore avoids storage rewrites later. |
| Auth required by default | Self-hosted services exposed to internet need auth. Disable explicitly via `PIXELRELAY_AUTH=none` for dev. |
| Pixelrelay-native API only in v0.2.0 | OpenAI-compat is a half-fit for media (no video, sync-only, no async knobs). Replicate-compatible API planned for v0.2.1 — that's the actual lingua franca for async media. |
| ed25519 (fal) + HMAC-SHA256 (replicate) verification | Match each provider's actual scheme; verify_webhooks abstraction handles both. |
| Per-job in-DB cooldown | Survives restarts, shared across replicas; the SDK's in-memory cooldown defeats horizontal scale. |
| `attempts` as JSON array on Job row | Cheap audit trail; full per-provider history visible in `GET /v1/jobs/{id}`. |

---

## What we explicitly skipped (and why)

- **OpenAI-compatible endpoint** — designed for DALL-E sync image gen. Doesn't fit video, async, or provider-specific knobs. Replicate-compat in v0.2.1 instead.
- **Alembic migrations** — `create_all()` is fine for SQLite; Alembic moves to v0.3.0 when we have schema changes to migrate.
- **Pillow / output format conversion** — Pushed to v0.3.0 with the dashboard.
- **In-memory request log endpoint** — DB IS the log; `GET /v1/jobs?limit=N` covers it.
- **Multi-tenancy** — Single org per gateway. BYOK = one set of provider keys. Multi-tenant is a different product.
- **Rate limiting** — Self-hosted users add a reverse proxy if needed.

---

## Roadmap

### v0.2.1 — Replicate-compatible API
- `POST /v1/predictions` matching Replicate's spec exactly (drop-in `replicate` SDK migration target)
- `GET /v1/predictions/{id}` mirroring Replicate's prediction object shape
- Strategy: use the same Job table, just expose a different request/response shape

### v0.3 — Observability + dashboard
- Static SPA at `/` (jobs list, provider health, attempt timeline)
- Structured JSON logs per request
- Alembic migrations
- Auto-fetch fal webhook public key from JWKS

### v0.4 — More providers + reliability knobs
- RunPod, Together, Stability adapters
- Per-provider cooldown config
- Configurable retry strategy (exponential backoff with jitter)
- Health-check pre-flight before submitting

### v0.5 — Cost & latency routing
- `strategy="cheapest"` / `"fastest"`
- In-DB latency stats per provider/model
- Cost table

### v0.6 — Video
- Runway, Kling, Pika adapters
- `POST /v1/videos/generations` route
- Longer default timeouts, different polling cadence

### v1.0 — Hosted optional control plane
- Optional cloud dashboard for users who don't want to run their own
- Self-hosted stays the recommended path

---

## Adding a new provider

1. Create `pixelrelay/providers/{name}.py` with a class extending `BaseProvider`
2. Implement three methods:
   - `async generate(prompt, model, timeout, **kwargs) -> GenerationResult` (library mode polling)
   - `async submit_async(prompt, model, webhook_url, **kwargs) -> SubmitResult` (gateway)
   - `@staticmethod parse_callback(headers, body) -> CallbackPayload` (gateway)
3. Register in `gateway/server.py::_build_provider_registry`
4. Add a webhook verifier to `gateway/webhook_verify.py` if the provider signs webhooks
5. Add a callback route in `gateway/routes/callbacks.py` (one per provider)
6. Add tests in `tests/test_gateway.py`

---

## Running tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

8 tests pass: 3 SDK library-mode tests + 5 gateway end-to-end tests (FastAPI TestClient with tempfile SQLite).
