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
    assert models.providers_for("luma-photon") == {"fal"}
    assert models.providers_for("bria-2.3") == {"fal"}


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
    # Standard text-to-image models should not be flagged
    assert models.is_image_edit("flux-dev") is False
    assert models.is_image_edit("sdxl") is False


def test_registry_size_is_meaningful():
    """Sanity check that we shipped a real catalog, not just a stub."""
    assert len(models.REGISTRY) >= 25, (
        f"Registry has only {len(models.REGISTRY)} models — expected at least 25"
    )


def test_every_registry_entry_has_at_least_one_slug():
    for canonical, info in models.REGISTRY.items():
        assert info.slugs, f"Model '{canonical}' has no provider slugs configured"
