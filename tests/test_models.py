"""Tests for the canonical model registry + cross-provider routing."""
from __future__ import annotations

import pytest

from pixelrelay import models


def test_known_model_resolves_to_provider_slug():
    assert models.resolve_for_provider("flux-dev", "fal") == "fal-ai/flux/dev"
    assert (
        models.resolve_for_provider("flux-dev", "replicate") == "black-forest-labs/flux-dev"
    )


def test_unknown_model_passes_through():
    assert models.resolve_for_provider("custom-org/private-model", "fal") == "custom-org/private-model"
    assert models.resolve_for_provider("anything-else", "replicate") == "anything-else"


def test_providers_for_known_multiprovider_model():
    assert models.providers_for("sdxl") == {"fal", "replicate"}
    assert models.providers_for("flux-1.1-pro") == {"fal", "replicate"}


def test_providers_for_fal_only_model():
    assert models.providers_for("nano-banana") == {"fal"}
    assert models.providers_for("nano-banana-2") == {"fal"}
    assert models.providers_for("nano-banana-pro") == {"fal"}
    assert models.providers_for("luma-photon") == {"fal"}
    assert models.providers_for("bria") == {"fal"}


def test_providers_for_replicate_only_model():
    """Some models are Replicate-only (FLUX Redux, Recraft v4 SVG, Ideogram v3 modes)."""
    assert models.providers_for("flux-redux-dev") == {"replicate"}
    assert models.providers_for("recraft-v4-svg") == {"replicate"}
    assert models.providers_for("ideogram-v3-quality") == {"replicate"}


def test_providers_for_openai_only_model():
    """OpenAI's models live only on OpenAI (no Fal/Replicate equivalent for gpt-image-1)."""
    assert models.providers_for("gpt-image-1") == {"openai"}
    assert models.providers_for("dall-e-3") == {"openai"}
    assert models.providers_for("dall-e-2") == {"openai"}
    assert models.resolve_for_provider("gpt-image-1", "openai") == "gpt-image-1"


def test_providers_for_unknown_returns_empty():
    """An empty set means 'unknown to registry' — caller should let all providers try."""
    assert models.providers_for("totally-made-up-model") == set()


def test_filter_supported_drops_unsupported():
    assert models.filter_supported("nano-banana", ["fal", "replicate"]) == ["fal"]


def test_filter_supported_unknown_keeps_all():
    """Unknown models shouldn't get filtered — they pass through."""
    assert models.filter_supported("unknown-model", ["fal", "replicate"]) == [
        "fal",
        "replicate",
    ]


def test_image_edit_models_are_flagged():
    assert models.is_image_edit("flux-kontext-pro") is True
    assert models.is_image_edit("flux-kontext-max") is True
    assert models.is_image_edit("nano-banana-edit") is True
    assert models.is_image_edit("nano-banana-2-edit") is True
    assert models.is_image_edit("nano-banana-pro-edit") is True
    assert models.is_image_edit("flux-redux-dev") is True
    # Standard text-to-image models should not be flagged
    assert models.is_image_edit("flux-dev") is False
    assert models.is_image_edit("sdxl") is False
    assert models.is_image_edit("nano-banana") is False  # base, not edit variant


def test_registry_size_is_meaningful():
    """Sanity check that we shipped a real catalog, not just a stub."""
    assert len(models.REGISTRY) >= 30, (
        f"Registry has only {len(models.REGISTRY)} models — expected at least 30"
    )


def test_all_slugs_use_correct_provider_path_format():
    """Fal slugs always start with 'fal-ai/'. Replicate slugs are 'owner/name' (no slash variation)."""
    for canonical, info in models.REGISTRY.items():
        if "fal" in info.slugs:
            assert info.slugs["fal"].startswith("fal-ai/"), (
                f"Fal slug for {canonical} is malformed: {info.slugs['fal']}"
            )
        if "replicate" in info.slugs:
            slug = info.slugs["replicate"]
            assert "/" in slug and not slug.startswith("/"), (
                f"Replicate slug for {canonical} is malformed: {slug}"
            )


def test_every_registry_entry_has_at_least_one_slug():
    for canonical, info in models.REGISTRY.items():
        assert info.slugs, f"Model '{canonical}' has no provider slugs configured"
