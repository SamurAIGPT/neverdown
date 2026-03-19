import os
from typing import List, Optional

from .providers.base import BaseProvider, GenerationResult
from .providers.fal import FalProvider
from .providers.replicate import ReplicateProvider
from .exceptions import (
    AllProvidersFailedError,
    ProviderUnavailableError,
    JobFailedError,
    JobTimeoutError,
)
from .cooldown import CooldownTracker

# Shared cooldown state across calls
_cooldown = CooldownTracker(cooldown_seconds=60)

# Provider registry
_PROVIDER_CLASSES = {
    "fal": FalProvider,
    "replicate": ReplicateProvider,
}


def _build_provider(name: str) -> BaseProvider:
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ValueError(f"Unknown provider: '{name}'. Available: {list(_PROVIDER_CLASSES)}")

    env_keys = {
        "fal": "FAL_KEY",
        "replicate": "REPLICATE_API_TOKEN",
    }
    key = os.environ.get(env_keys[name])
    if not key:
        raise EnvironmentError(
            f"Missing API key for '{name}'. Set the {env_keys[name]} environment variable."
        )
    return cls(api_key=key)


async def generate(
    prompt: str,
    model: str = "flux-dev",
    providers: Optional[List[str]] = None,
    timeout: float = 120.0,
    **kwargs,
) -> GenerationResult:
    """
    Generate an image with automatic failover across providers.

    Args:
        prompt:    The generation prompt.
        model:     Model name (e.g. "flux-dev", "flux-schnell").
        providers: Ordered list of providers to try. Defaults to ["fal", "replicate"].
        timeout:   Per-provider timeout in seconds.
        **kwargs:  Extra params forwarded to the provider (e.g. image_size, seed).

    Returns:
        GenerationResult with image_url, provider used, model, and latency_ms.

    Raises:
        AllProvidersFailedError if every provider fails.
    """
    if providers is None:
        providers = ["fal", "replicate"]

    errors = {}

    for provider_name in providers:
        if not _cooldown.is_available(provider_name):
            remaining = _cooldown.cooldown_remaining(provider_name)
            errors[provider_name] = f"In cooldown for {remaining:.0f}s more"
            continue

        try:
            provider = _build_provider(provider_name)
            result = await provider.generate(
                prompt=prompt, model=model, timeout=timeout, **kwargs
            )
            return result

        except (ProviderUnavailableError, JobTimeoutError) as e:
            # Provider-level failures → trigger cooldown
            _cooldown.mark_failed(provider_name)
            errors[provider_name] = e
            continue

        except JobFailedError as e:
            # Job itself failed (bad prompt, model error) → no cooldown, still try next
            errors[provider_name] = e
            continue

        except Exception as e:
            errors[provider_name] = e
            continue

    raise AllProvidersFailedError(errors)
