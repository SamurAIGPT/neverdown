# Pixelrelay

**Automatic failover across generative media APIs. Pixelrelay hands off your image and video jobs to the next provider when Fal or Replicate goes down.**

When Fal is down, Pixelrelay automatically switches to Replicate. Your users never see an error.

```python
from pixelrelay import generate

result = await generate(
    prompt="cinematic portrait of a woman in paris",
    model="flux-dev",
    providers=["fal", "replicate"],  # tries fal first, auto-switches on failure
)

print(result.image_url)   # the image
print(result.provider)    # which provider served it
print(result.latency_ms)  # how long it took
```

## The problem

Your app depends on generative media providers that go down — Fal, Replicate, RunPod. When they do, your app breaks. You either wait it out or manually switch providers. Neither is acceptable in production.

## How it works

1. Submit job to first provider
2. Poll asynchronously — no blocking
3. If provider fails or times out → automatically retry on next provider
4. Failed providers enter a 60s cooldown so they're not hammered while down
5. Return result + which provider served it

## Installation

```bash
pip install pixelrelay
```

## Setup

Set your API keys as environment variables:

```bash
export FAL_KEY=your_fal_key
export REPLICATE_API_TOKEN=your_replicate_token
```

## Usage

### Basic

```python
import asyncio
from pixelrelay import generate

async def main():
    result = await generate(
        prompt="cinematic portrait of a woman in paris, golden hour",
        model="flux-dev",
    )
    print(result.image_url)

asyncio.run(main())
```

### Custom provider order

```python
result = await generate(
    prompt="...",
    model="flux-schnell",
    providers=["replicate", "fal"],  # try replicate first
)
```

### Concurrent generations

```python
import asyncio
from pixelrelay import generate

results = await asyncio.gather(
    generate(prompt="photo 1", model="flux-dev"),
    generate(prompt="photo 2", model="flux-dev"),
    generate(prompt="photo 3", model="flux-dev"),
)
```

### Error handling

```python
from pixelrelay import generate
from pixelrelay.exceptions import AllProvidersFailedError

try:
    result = await generate(prompt="...", model="flux-dev")
except AllProvidersFailedError as e:
    print(e.errors)  # {provider: exception} for each failure
```

## Supported models

| Model | Key |
|-------|-----|
| FLUX.1 Dev | `flux-dev` |
| FLUX.1 Schnell | `flux-schnell` |
| FLUX.1 Pro | `flux-pro` |

## Supported providers

| Provider | Env var |
|----------|---------|
| [Fal.ai](https://fal.ai) | `FAL_KEY` |
| [Replicate](https://replicate.com) | `REPLICATE_API_TOKEN` |

## GenerationResult

```python
result.image_url    # str   — URL of generated image
result.provider     # str   — "fal" or "replicate"
result.model        # str   — model used
result.latency_ms   # float — end-to-end latency in milliseconds
```

## Exceptions

| Exception | When |
|-----------|------|
| `AllProvidersFailedError` | Every provider in the list failed |
| `ProviderUnavailableError` | Provider is down or unreachable (triggers cooldown) |
| `JobFailedError` | Job was submitted but failed on provider side |
| `JobTimeoutError` | Job exceeded the timeout (triggers cooldown) |

## Contributing

Pull requests are welcome. To add a new provider, implement `BaseProvider` in `pixelrelay/providers/` and register it in `core.py`.

## License

Apache 2.0 — see [LICENSE](LICENSE)
