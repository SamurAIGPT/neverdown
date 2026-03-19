import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from neverdown import generate
from neverdown.providers.base import GenerationResult
from neverdown.exceptions import AllProvidersFailedError, ProviderUnavailableError


MOCK_RESULT = GenerationResult(
    image_url="https://example.com/image.png",
    provider="fal",
    model="flux-dev",
    latency_ms=1200.0,
)


@pytest.mark.asyncio
async def test_generate_uses_first_provider():
    with patch("neverdown.core._build_provider") as mock_build:
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=MOCK_RESULT)
        mock_build.return_value = mock_provider

        result = await generate(
            prompt="a cat in space",
            providers=["fal", "replicate"],
        )
        assert result.image_url == "https://example.com/image.png"
        assert result.provider == "fal"
        mock_build.assert_called_once_with("fal")


@pytest.mark.asyncio
async def test_fallback_on_provider_unavailable():
    replicate_result = GenerationResult(
        image_url="https://example.com/image2.png",
        provider="replicate",
        model="flux-dev",
        latency_ms=2000.0,
    )

    call_count = {"n": 0}

    def build_side_effect(name):
        mock = AsyncMock()
        if name == "fal":
            mock.generate = AsyncMock(
                side_effect=ProviderUnavailableError("down", provider="fal")
            )
        else:
            mock.generate = AsyncMock(return_value=replicate_result)
        return mock

    with patch("neverdown.core._build_provider", side_effect=build_side_effect):
        with patch("neverdown.core._cooldown") as mock_cooldown:
            mock_cooldown.is_available.return_value = True
            mock_cooldown.mark_failed = lambda p: None
            mock_cooldown.cooldown_remaining.return_value = 0.0

            result = await generate(
                prompt="a cat in space",
                providers=["fal", "replicate"],
            )
            assert result.provider == "replicate"


@pytest.mark.asyncio
async def test_all_providers_fail_raises():
    def build_side_effect(name):
        mock = AsyncMock()
        mock.generate = AsyncMock(
            side_effect=ProviderUnavailableError("down", provider=name)
        )
        return mock

    with patch("neverdown.core._build_provider", side_effect=build_side_effect):
        with patch("neverdown.core._cooldown") as mock_cooldown:
            mock_cooldown.is_available.return_value = True
            mock_cooldown.mark_failed = lambda p: None
            mock_cooldown.cooldown_remaining.return_value = 0.0

            with pytest.raises(AllProvidersFailedError):
                await generate(
                    prompt="a cat in space",
                    providers=["fal", "replicate"],
                )
