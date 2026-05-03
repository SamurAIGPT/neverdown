# Pixelrelay

**The open-source, self-hosted gateway for generative media APIs. One webhook-native endpoint across Fal, Replicate, and more — with your own keys, in your own infra.**

Pixelrelay unifies image and video generation across providers behind a single async API. You bring the keys, it handles the job lifecycle: persisted state in your own database, webhook callbacks instead of polling, and automatic provider failover when one goes down.

```bash
docker run -p 8000:8000 \
  -e PIXELRELAY_GATEWAY_KEY=$(openssl rand -hex 32) \
  -e PIXELRELAY_PUBLIC_URL=https://gateway.example.com \
  -e FAL_KEY=$FAL_KEY \
  -e REPLICATE_API_TOKEN=$REPLICATE_API_TOKEN \
  -v pixelrelay_data:/data \
  ghcr.io/samuraigpt/pixelrelay
```

```bash
curl -X POST http://localhost:8000/v1/generate \
  -H "Authorization: Bearer $PIXELRELAY_GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "cinematic portrait of a woman in paris",
    "model": "flux-dev",
    "providers": ["fal", "replicate"],
    "webhook_url": "https://your-app.example.com/pixelrelay-webhook"
  }'
# → { "job_id": "...", "status": "submitted", "provider": "fal", ... }
```

When the job finishes, your `webhook_url` receives a signed POST with the result.

---

## What it gives you

| | |
|---|---|
| **Unified API** | One endpoint, one auth header — talks to Fal, Replicate, and (soon) RunPod, Stability, Runway. Same shape across providers. |
| **Webhooks-first** | Provider POSTs result asynchronously; gateway forwards to your `webhook_url`. No client-side polling, no held connections. |
| **Persistent jobs** | Job state in SQLite (default) or Postgres. Survives restarts, shared across replicas, full audit trail at `GET /v1/jobs`. |
| **Automatic failover** | Provider down or hung past deadline? The gateway cools it down and resubmits to the next one — your app never sees the error. |
| **BYOK, self-hosted** | You run it, you own the data, you keep your provider rate limits. No billing layer, no markup, no lock-in. |

## Why a gateway, not just an SDK

A polling SDK breaks at production scale because:

1. **Process lifecycle** — your request handler holds open for the entire job duration (10–300s for video). One restart loses state. Serverless function timeouts kill long polls.
2. **State doesn't share** — cooldown lives in one Python process. 50 workers = 50 independent cooldown trackers.
3. **Failover wastes the request budget** — if Fal hangs for 4 minutes before failover, your user already lost 4 minutes.

The gateway fixes all three by:
- Persisting job state in a database (SQLite by default, Postgres for production)
- Receiving webhooks from providers asynchronously instead of polling
- Sharing cooldown across all instances of the user's app
- Forwarding the result to *your* webhook URL when the job completes

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Your app                                                      │
│   POST /v1/generate { prompt, webhook_url }   → fire & forget │
└────────────────────────┬─────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ Pixelrelay Gateway (self-hosted, BYOK)                        │
│                                                               │
│  1. Persist job in DB                                         │
│  2. Submit to fal w/ webhook=<gateway>/v1/callback/fal/{id}   │
│  3. Return { job_id, status: "submitted" } immediately        │
│  4. fal POSTs result → gateway forwards to your webhook       │
│                                                               │
│  Failover (job-level deadline OR provider 5xx):               │
│   → mark current provider in cooldown (DB-backed, shared)     │
│   → resubmit to next provider with new callback URL           │
│   → all transparent to your app                               │
└──────────────────────────────────────────────────────────────┘
```

---

## Install

```bash
# Library + gateway
pip install "pixelrelay[gateway]"

# Library only (in-process polling, no gateway server)
pip install pixelrelay
```

---

## Run the gateway

### Docker (recommended)

```bash
cd docker
cp .env.example .env
# Edit .env: set PIXELRELAY_GATEWAY_KEY, PIXELRELAY_PUBLIC_URL, FAL_KEY, REPLICATE_API_TOKEN
docker compose up -d
```

### From source

```bash
pip install "pixelrelay[gateway]"
export PIXELRELAY_GATEWAY_KEY=$(openssl rand -hex 32)
export PIXELRELAY_PUBLIC_URL=https://gateway.example.com
export FAL_KEY=...
export REPLICATE_API_TOKEN=...
python -m pixelrelay.gateway
```

### Configuration

| Env var | Default | Notes |
|---|---|---|
| `PIXELRELAY_GATEWAY_KEY` | (required) | Bearer token clients use to authenticate |
| `PIXELRELAY_AUTH` | (unset) | Set to `none` to disable auth (local dev only) |
| `PIXELRELAY_PUBLIC_URL` | `http://localhost:8000` | URL fal/replicate POST callbacks back to. Must be reachable from the public internet. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./pixelrelay.db` | `postgresql+asyncpg://...` for production |
| `FAL_KEY` | — | Provider key (BYOK) |
| `REPLICATE_API_TOKEN` | — | Provider key (BYOK) |
| `PIXELRELAY_PROVIDERS` | `fal,replicate` | Default provider order |
| `FAL_WEBHOOK_PUBLIC_KEY` | (unset) | Hex-encoded ed25519 public key. If unset, signature verification is skipped (warning logged). |
| `REPLICATE_WEBHOOK_SECRET` | (unset) | `whsec_...`, fetched from `GET /v1/webhooks/default/secret` |
| `PIXELRELAY_WEBHOOK_SECRET` | `change-me-in-production` | HMAC secret for signing webhooks the gateway sends to your app |
| `PIXELRELAY_JOB_DEADLINE` | `180` | Seconds before a submitted job is considered stale and failed-over |
| `PIXELRELAY_COOLDOWN` | `60` | Seconds a failed provider stays out of rotation |

**SQLite is fine for a single container.** For horizontally scaled deploys (multiple gateway replicas behind a load balancer), use Postgres so cooldown and job state are shared.

---

## API

### `POST /v1/generate`

Submit a generation job. Asynchronous by default.

```json
{
  "prompt": "cinematic portrait of a woman in paris",
  "model": "flux-dev",
  "providers": ["fal", "replicate"],
  "webhook_url": "https://your-app.example.com/pixelrelay-webhook",
  "extra": { "seed": 42 }
}
```

Add `?wait=true` for a synchronous response (blocks until terminal or `PIXELRELAY_JOB_DEADLINE`).

Response:
```json
{
  "job_id": "f3a2...",
  "status": "submitted",
  "provider": "fal",
  "model": "flux-dev",
  "prompt": "...",
  "image_url": null,
  "attempts": [],
  "webhook_url": "...",
  "created_at": "2026-05-03T01:23:45Z",
  "completed_at": null
}
```

### `GET /v1/jobs/{job_id}`

Fetch current state of a job.

### `GET /v1/jobs?limit=50`

List recent jobs (audit log).

### `POST /v1/callback/{provider}/{job_id}`

Provider webhook receiver. Not called by users — fal and replicate POST here when jobs complete. Verified per-provider (ed25519 for fal, HMAC-SHA256 for replicate).

### `GET /health`

Liveness probe. Returns `{ "status": "ok" }`.

---

## Receiving webhooks in your app

When a job completes, the gateway POSTs to your `webhook_url` with two headers:

- `X-Pixelrelay-Timestamp`: unix seconds
- `X-Pixelrelay-Signature`: hex HMAC-SHA256 of `{timestamp}.{body}` using `PIXELRELAY_WEBHOOK_SECRET`

Verify in Python:

```python
import hashlib, hmac
def verify(body: bytes, ts: str, sig: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
```

Payload:
```json
{
  "job_id": "f3a2...",
  "status": "succeeded",
  "provider": "fal",
  "model": "flux-dev",
  "image_url": "https://fal.media/...",
  "error": null,
  "attempts": [{"provider": "fal", "cooldown": false, ...}]
}
```

---

## Library mode (no gateway)

For quick scripts and notebooks where a gateway is overkill, the library still works in-process via polling. Not recommended for production.

```python
import asyncio
from pixelrelay import generate

async def main():
    result = await generate(prompt="...", model="flux-dev", providers=["fal", "replicate"])
    print(result.image_url, result.provider, result.latency_ms)

asyncio.run(main())
```

Library mode runs a polling loop in your own process — see the "Why a gateway" section above for why you probably don't want this in production.

---

## Supported providers (v0.2.0)

| Provider | Webhook support | Env var |
|---|---|---|
| [Fal.ai](https://fal.ai) | Native (ed25519-signed) | `FAL_KEY` |
| [Replicate](https://replicate.com) | Native (HMAC-SHA256-signed) | `REPLICATE_API_TOKEN` |

## Supported models

Every slug in the registry is **verified against the provider's live model page** (fal.ai / replicate.com). Unknown canonical names pass through verbatim — devs can hit a private Fal deployment with `fal-ai/your-org/your-model` directly, no registration required.

| Family | Canonical names | Providers |
|---|---|---|
| **FLUX (Black Forest Labs)** | `flux-dev`, `flux-schnell`, `flux-pro`, `flux-1.1-pro`, `flux-1.1-pro-ultra`, `flux-realism` | Fal + Replicate (`flux-realism` Fal-only) |
| **FLUX Redux** (img2img variations) | `flux-redux-dev`, `flux-redux-schnell` | Replicate-only |
| **FLUX Kontext** (text-driven edits) | `flux-kontext-pro`, `flux-kontext-max` | Fal + Replicate |
| **Stable Diffusion** | `sd3`, `sd3.5-large`, `sd3.5-large-turbo`, `sd3.5-medium`, `sdxl` | Fal + Replicate |
| **Ideogram** (text in images) | `ideogram-v2`, `ideogram-v2-turbo`, `ideogram-v3` (Fal+Replicate); `ideogram-v3-quality`, `ideogram-v3-turbo` (Replicate-only — Fal v3 modes are parameters) | mixed |
| **Recraft** (logos / SVG) | `recraft-v3`, `recraft-v3-svg`, `recraft-v4`, `recraft-v4-pro`, `recraft-v4-svg` | mixed (SVG variants Replicate-only) |
| **Imagen (Google)** | `imagen-3`, `imagen-3-fast`, `imagen-4` (Fal+Replicate); `imagen-4-fast`, `imagen-4-ultra` (Fal-only) | mixed |
| **Nano Banana (Google)** | `nano-banana`, `nano-banana-edit`, `nano-banana-2`, `nano-banana-2-edit`, `nano-banana-pro`, `nano-banana-pro-edit` | Fal-only |
| **Luma Photon** | `luma-photon`, `luma-photon-flash` | Fal-only |
| **Bria** (commercial-safe) | `bria` | Fal-only |

When you request a single-provider model with `providers=["fal", "replicate"]`, the gateway automatically drops the unsupported provider from the failover chain and logs the reason in the job's `attempts`. To add a model, edit [`pixelrelay/models.py`](pixelrelay/models.py) — one row per model. **Always verify the slug against the provider's live model page before merging.**

---

## Roadmap

- **v0.2.1** — Replicate-compatible API (`POST /v1/predictions`) for drop-in migration from Replicate-only setups
- **v0.3.0** — Dashboard UI, structured logs, Alembic migrations, more providers (RunPod, Together, Stability)
- **v0.4.0** — Strategy modes (cheapest/fastest), per-provider cooldown config, health-check pre-flight
- **v0.6.0** — Video generation (Runway, Kling, Pika)

---

## Contributing

To add a new provider, implement `BaseProvider` in `pixelrelay/providers/` (both `generate` for library mode and `submit_async` + `parse_callback` for the gateway). Register it in `pixelrelay/gateway/server.py::_build_provider_registry`.

## License

Apache 2.0 — see [LICENSE](LICENSE)
