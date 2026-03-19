# neverdown — Full Project Context

> This document is the canonical reference for the neverdown project. Read this at the start of any new session before making changes.

---

## What is neverdown?

A Python SDK that provides **automatic async failover across generative AI providers** (Fal.ai, Replicate, and future providers). When one provider goes down, it automatically switches to the next. Users never see an error.

**Core insight:** Unlike LLM gateways (Portkey, LiteLLM, OpenRouter) which route synchronous text requests, neverdown handles *async jobs* — submit → poll → result — which is fundamentally different and requires job lifecycle management, not just request routing.

**GitHub:** https://github.com/SamurAIGPT/neverdown
**License:** Apache 2.0
**Language:** Python 3.9+
**Local path:** `/Users/anilchandranaidumatcha/Downloads/neverdown/`

---

## Current API (v0.1.0)

```python
from neverdown import generate

result = await generate(
    prompt="cinematic portrait of a woman in paris",
    model="flux-dev",
    providers=["fal", "replicate"],  # ordered, tries fal first
    timeout=120.0,                   # per-provider timeout in seconds
)

result.image_url    # str   — URL of the generated image
result.provider     # str   — which provider actually served it
result.model        # str   — model used
result.latency_ms   # float — end-to-end latency
```

---

## Project Structure

```
neverdown/
├── neverdown/
│   ├── __init__.py          # public exports: generate, GenerationResult, exceptions
│   ├── core.py              # generate() — main entry point, fallback loop, cooldown logic
│   ├── cooldown.py          # CooldownTracker — marks failed providers, skips during cooldown
│   ├── exceptions.py        # normalized exception hierarchy
│   └── providers/
│       ├── __init__.py
│       ├── base.py          # BaseProvider ABC + GenerationResult dataclass
│       ├── fal.py           # Fal.ai adapter — async submit + polling
│       └── replicate.py     # Replicate adapter — async submit + polling
├── tests/
│   └── test_core.py         # unit tests (mocked, 3 passing)
├── pyproject.toml           # hatchling build, Apache-2.0, requires httpx>=0.27
├── CONTEXT.md               # this file
└── README.md
```

---

## How the Core Works

### Failover loop (`core.py`)
1. Iterate through `providers` list in order
2. Skip provider if in cooldown (60s after failure)
3. Call `provider.generate(prompt, model, timeout)`
4. On `ProviderUnavailableError` or `JobTimeoutError` → mark provider in cooldown, try next
5. On `JobFailedError` (bad prompt/model error) → try next, no cooldown
6. Return first successful `GenerationResult`
7. If all fail → raise `AllProvidersFailedError(errors)` where errors = `{provider: exception}`

### Async polling (both providers)
- Submit job via POST → get job ID / prediction URL
- Loop: `await asyncio.sleep(2)` → check status → continue or return
- Backoff: sleep increases slightly each poll (`POLL_DELAY + 2 * retry` in Fal)
- Timeout checked each iteration via `time.monotonic()`

### Cooldown (`cooldown.py`)
- In-memory dict: `{provider: expires_at_timestamp}`
- 60s default cooldown after `ProviderUnavailableError` or `JobTimeoutError`
- `JobFailedError` does NOT trigger cooldown (not provider's fault)
- Cooldown auto-expires — next call after expiry clears it

---

## Provider Details

### Fal.ai (`providers/fal.py`)
- **Env var:** `FAL_KEY`
- **Submit:** `POST https://queue.fal.run/{model}`
- **Status:** `GET {status_url}` — statuses: `IN_QUEUE`, `IN_PROGRESS`, `COMPLETED`, `FAILED`
- **Result:** `GET https://queue.fal.run/{model}/requests/{request_id}`
- **Image URL:** `result["images"][0]["url"]`
- **Model map:** `flux-dev` → `fal-ai/flux/dev`, `flux-schnell` → `fal-ai/flux/schnell`, `flux-pro` → `fal-ai/flux-pro`

### Replicate (`providers/replicate.py`)
- **Env var:** `REPLICATE_API_TOKEN`
- **Submit:** `POST https://api.replicate.com/v1/models/{model}/predictions` with `Prefer: respond-async`
- **Poll:** `GET {urls.get}` — statuses: `starting`, `processing`, `succeeded`, `failed`
- **Image URL:** `output[0]` (output is a list)
- **Model map:** `flux-dev` → `black-forest-labs/flux-dev`, etc.

---

## Exception Hierarchy

```
NeverDownError
├── ProviderError(provider, status_code)
│   ├── ProviderUnavailableError   — 5xx, connection error, unreachable → triggers cooldown
│   ├── JobFailedError             — job submitted but failed → no cooldown
│   └── JobTimeoutError            — polling exceeded timeout → triggers cooldown
└── AllProvidersFailedError(errors: dict)  — all providers exhausted
```

---

## Design Decisions & Rationale

| Decision | Rationale |
|----------|-----------|
| Async-first (`await generate()`) | Non-blocking polling; users can run concurrent generations |
| `asyncio.sleep` not `time.sleep` | Yields control during polling, doesn't block event loop |
| Cooldown on timeout/unavailable only | `JobFailedError` is not provider's fault, shouldn't penalize it |
| `httpx.AsyncClient` | Async HTTP, cleaner than aiohttp, well maintained |
| Per-call timeout, not global | Each provider gets full timeout budget independently |
| Open-core strategy | SDK is open, future hosted routing intelligence is paid |
| Apache 2.0 license | Patent protection vs MIT, enterprise-friendly, used by LiteLLM/Portkey |

---

## What We Learned from LiteLLM / Portkey Research

- **LiteLLM pattern:** `[primary] + fallbacks` list, iterate, catch, continue
- **Portkey pattern:** recursive `tryTargetsRecursively` with strategy modes (fallback, load-balance, conditional)
- **Error normalization:** always map provider errors to your own exception types — never leak raw provider errors
- **Cooldown:** LiteLLM uses 60s cooldown after N failures per minute
- **Retry vs fallback:** retries = transient errors on same provider; fallback = switch provider entirely
- **Our difference:** both are sync/LLM-first; we are async/job-first which requires polling loop as first-class concern

---

## GTM Strategy

- **Target:** Indie devs and small AI startups building on Fal/Replicate who feel outage pain
- **Distribution channels:** Fal Discord, Replicate Discord, X/Twitter, Reddit
- **Positioning:** "Never let your AI app go down when Fal or Replicate fails" — not "AI gateway"
- **Open-core:** SDK open, future hosted intelligence (smart routing, analytics, cost optimization) paid
- **Wedge feature:** 1-line failover. Sell reliability, not platform.
- **Growth loop:** Post in Discord communities where users already complain about outages

---

## Competitors

| Tool | What it does | Gap vs neverdown |
|------|-------------|------------------|
| LiteLLM | LLM gateway, basic Fal/Replicate support | Sync/LLM-first, no async job lifecycle |
| Portkey | LLM control plane | LLM-focused, not built for GPU async jobs |
| OpenRouter | Model aggregator | Request routing only, no job orchestration |
| DIY | Teams hardcode their own fallback | Fragile, no cooldown, no standardization |

---

## Roadmap

### v0.2 — More providers + models
- [ ] Add **RunPod** provider
- [ ] Add **Together AI** provider
- [ ] Add **Stability AI** (Stable Diffusion) models
- [ ] Add SDXL to model map
- [ ] Support `image_size`, `num_inference_steps`, `seed` as normalized params across providers

### v0.3 — Observability
- [ ] Logging: which provider was tried, which succeeded, latency per attempt
- [ ] `on_fallback` callback: `generate(..., on_fallback=lambda p, e: log(p, e))`
- [ ] Return full attempt history in `GenerationResult.attempts`
- [ ] Optional structured logging (JSON) for production use

### v0.4 — Reliability improvements
- [ ] Configurable cooldown per provider (not just global 60s)
- [ ] Max retries per provider before cooldown
- [ ] Health check endpoint support (pre-flight check before submitting)
- [ ] Configurable retry strategy (exponential backoff, jitter)

### v0.5 — Cost & latency routing
- [ ] `strategy="cheapest"` — route to lowest-cost provider
- [ ] `strategy="fastest"` — route to lowest-latency provider (based on recent history)
- [ ] In-memory latency tracking across calls
- [ ] Cost per provider per model (hardcoded initially, then dynamic)

### v0.6 — Video generation
- [ ] Add **Runway** provider (Gen-3, Gen-4)
- [ ] Add **Kling** provider
- [ ] Add **Pika** provider
- [ ] `generate_video()` function with same failover pattern
- [ ] Longer timeouts, different polling intervals for video

### v1.0 — Hosted intelligence layer (monetization)
- [ ] Cloud routing brain (decides which provider to use based on real-time data)
- [ ] Dashboard: success rates, latency, cost per provider
- [ ] Webhook support (instead of polling, receive callback when job done)
- [ ] Team API keys
- [ ] SLA guarantees

---

## Adding a New Provider (How-To)

1. Create `neverdown/providers/{name}.py`
2. Implement `BaseProvider`:
   ```python
   class NewProvider(BaseProvider):
       name = "newprovider"

       def __init__(self, api_key: str): ...

       async def generate(self, prompt, model, timeout, **kwargs) -> GenerationResult:
           # 1. Submit job (POST to provider API)
           # 2. Poll status with asyncio.sleep
           # 3. Return GenerationResult on success
           # 4. Raise ProviderUnavailableError on 5xx/connection issues
           # 5. Raise JobFailedError on job failure
           # 6. Raise JobTimeoutError on timeout
   ```
3. Add to `providers/__init__.py`
4. Register in `core.py`:
   ```python
   _PROVIDER_CLASSES = {
       "fal": FalProvider,
       "replicate": ReplicateProvider,
       "newprovider": NewProvider,   # add here
   }
   # Also add env key mapping in _build_provider()
   ```
5. Add model mappings if needed
6. Write tests in `tests/`

---

## Environment Variables

| Variable | Provider |
|----------|----------|
| `FAL_KEY` | Fal.ai |
| `REPLICATE_API_TOKEN` | Replicate |

---

## Running Tests

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

Tests are unit tests with mocked providers — no real API calls needed.
