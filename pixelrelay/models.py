"""Canonical model registry — one place to add or update model entries.

Each entry maps a canonical Pixelrelay model name (e.g. ``flux-1.1-pro``) to the
provider-specific slugs that the corresponding adapters use under the hood.

Adding a model = adding one row here. The provider adapters do the rest.

Unknown model names pass through verbatim — devs can hit a custom Fal endpoint
or a pinned Replicate version slug without having to add it to this registry
first. The registry just lets us:

1. Translate canonical → provider slug consistently.
2. Filter the failover provider list to those that actually serve the model
   (so we don't waste an attempt on a provider that has never heard of it).
3. Surface the catalog in the README, dashboard, and any future model picker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Literal, Optional, Set

ModelKind = Literal["text-to-image", "image-edit", "text-to-video"]


@dataclass(frozen=True)
class ModelInfo:
    canonical: str
    kind: ModelKind = "text-to-image"
    family: str = ""
    description: str = ""
    # Provider name (e.g. "fal", "replicate") -> provider-specific model slug.
    slugs: Dict[str, str] = field(default_factory=dict)


def _e(canonical: str, *, family: str, description: str, kind: ModelKind = "text-to-image", **slugs: str) -> ModelInfo:
    return ModelInfo(
        canonical=canonical, family=family, description=description, kind=kind, slugs=slugs
    )


REGISTRY: Dict[str, ModelInfo] = {info.canonical: info for info in [
    # ── FLUX (Black Forest Labs) ────────────────────────────────────────────
    _e("flux-dev",
       family="flux", description="FLUX.1 [dev] — open-weight 12B, fast & high quality",
       fal="fal-ai/flux/dev", replicate="black-forest-labs/flux-dev"),
    _e("flux-schnell",
       family="flux", description="FLUX.1 [schnell] — 4-step distilled, very fast",
       fal="fal-ai/flux/schnell", replicate="black-forest-labs/flux-schnell"),
    _e("flux-pro",
       family="flux", description="FLUX.1 Pro — original BFL flagship",
       fal="fal-ai/flux-pro", replicate="black-forest-labs/flux-pro"),
    _e("flux-1.1-pro",
       family="flux", description="FLUX 1.1 Pro — improved aesthetics over flux-pro",
       fal="fal-ai/flux-pro/v1.1", replicate="black-forest-labs/flux-1.1-pro"),
    _e("flux-1.1-pro-ultra",
       family="flux", description="FLUX 1.1 Pro Ultra — up to 4MP output",
       fal="fal-ai/flux-pro/v1.1-ultra", replicate="black-forest-labs/flux-1.1-pro-ultra"),
    _e("flux-redux",
       family="flux", description="FLUX Redux — variations of an input image",
       kind="image-edit",
       fal="fal-ai/flux-pro/v1.1-ultra-finetuned"),  # canonical FLUX Redux endpoint varies
    _e("flux-realism",
       family="flux", description="FLUX trained for photorealism",
       fal="fal-ai/flux-realism"),
    _e("flux-kontext-pro",
       family="flux", description="FLUX Kontext Pro — text-driven image edits",
       kind="image-edit",
       fal="fal-ai/flux-pro/kontext", replicate="black-forest-labs/flux-kontext-pro"),
    _e("flux-kontext-max",
       family="flux", description="FLUX Kontext Max — highest-fidelity text-driven edits",
       kind="image-edit",
       fal="fal-ai/flux-pro/kontext/max", replicate="black-forest-labs/flux-kontext-max"),

    # ── Stable Diffusion family (Stability AI) ───────────────────────────────
    _e("sd3",
       family="stable-diffusion", description="Stable Diffusion 3 Medium",
       fal="fal-ai/stable-diffusion-v3-medium", replicate="stability-ai/stable-diffusion-3"),
    _e("sd3.5-large",
       family="stable-diffusion", description="Stable Diffusion 3.5 Large — 8B flagship",
       fal="fal-ai/stable-diffusion-v35-large",
       replicate="stability-ai/stable-diffusion-3.5-large"),
    _e("sd3.5-large-turbo",
       family="stable-diffusion", description="SD 3.5 Large Turbo — 4-step distilled",
       fal="fal-ai/stable-diffusion-v35-large/turbo",
       replicate="stability-ai/stable-diffusion-3.5-large-turbo"),
    _e("sd3.5-medium",
       family="stable-diffusion", description="SD 3.5 Medium — 2.5B for consumer GPUs",
       fal="fal-ai/stable-diffusion-v35-medium",
       replicate="stability-ai/stable-diffusion-3.5-medium"),
    _e("sdxl",
       family="stable-diffusion", description="SDXL 1.0 — battle-tested baseline",
       fal="fal-ai/fast-sdxl", replicate="stability-ai/sdxl"),

    # ── Ideogram (best-in-class for text in images) ─────────────────────────
    _e("ideogram-v2",
       family="ideogram", description="Ideogram 2.0",
       fal="fal-ai/ideogram/v2", replicate="ideogram-ai/ideogram-v2"),
    _e("ideogram-v2-turbo",
       family="ideogram", description="Ideogram 2.0 Turbo — faster, cheaper",
       fal="fal-ai/ideogram/v2/turbo", replicate="ideogram-ai/ideogram-v2-turbo"),
    _e("ideogram-v3",
       family="ideogram", description="Ideogram 3.0 (balanced)",
       fal="fal-ai/ideogram/v3", replicate="ideogram-ai/ideogram-v3-balanced"),
    _e("ideogram-v3-quality",
       family="ideogram", description="Ideogram 3.0 Quality — slowest, best output",
       fal="fal-ai/ideogram/v3/quality", replicate="ideogram-ai/ideogram-v3-quality"),
    _e("ideogram-v3-turbo",
       family="ideogram", description="Ideogram 3.0 Turbo — fastest",
       fal="fal-ai/ideogram/v3/turbo", replicate="ideogram-ai/ideogram-v3-turbo"),

    # ── Recraft (brand/logo workflows, native SVG) ──────────────────────────
    _e("recraft-v3",
       family="recraft", description="Recraft V3 — strong design/illustration model",
       fal="fal-ai/recraft-v3", replicate="recraft-ai/recraft-v3"),
    _e("recraft-v3-svg",
       family="recraft", description="Recraft V3 SVG — vector output",
       fal="fal-ai/recraft-v3/create-style",  # closest Fal endpoint family
       replicate="recraft-ai/recraft-v3-svg"),

    # ── Imagen (Google, accessed via Fal/Replicate) ─────────────────────────
    _e("imagen-3",
       family="imagen", description="Google Imagen 3",
       fal="fal-ai/imagen3", replicate="google/imagen-3"),
    _e("imagen-3-fast",
       family="imagen", description="Google Imagen 3 Fast",
       fal="fal-ai/imagen3/fast", replicate="google/imagen-3-fast"),
    _e("imagen-4",
       family="imagen", description="Google Imagen 4 (preview)",
       fal="fal-ai/imagen4/preview", replicate="google/imagen-4"),

    # ── Nano Banana (Google's image-edit model, on Fal) ─────────────────────
    _e("nano-banana",
       family="nano-banana", description="Nano Banana — Google Gemini-based image gen",
       fal="fal-ai/nano-banana"),
    _e("nano-banana-edit",
       family="nano-banana", description="Nano Banana edit — img2img with Gemini",
       kind="image-edit",
       fal="fal-ai/nano-banana/edit"),
    _e("nano-banana-pro",
       family="nano-banana", description="Nano Banana Pro — higher quality",
       fal="fal-ai/nano-banana/pro"),

    # ── Luma Photon (image, not video) ──────────────────────────────────────
    _e("luma-photon",
       family="luma", description="Luma Photon — Luma's text-to-image",
       fal="fal-ai/luma-photon"),
    _e("luma-photon-flash",
       family="luma", description="Luma Photon Flash — faster variant",
       fal="fal-ai/luma-photon/flash"),

    # ── Bria (commercial-safe, licensed training data) ──────────────────────
    _e("bria-2.3",
       family="bria", description="Bria 2.3 — commercial-safe baseline",
       fal="fal-ai/bria/text-to-image/base"),
]}


def resolve_for_provider(canonical: str, provider: str) -> str:
    """Return the provider-specific slug for the canonical name.

    Falls back to ``canonical`` itself so devs can pass a raw provider slug
    (``fal-ai/some/private/deployment``) without registering it.
    """
    info = REGISTRY.get(canonical)
    if info is None:
        return canonical
    return info.slugs.get(provider, canonical)


def providers_for(canonical: str) -> Set[str]:
    """Set of providers that support the model. Empty set means 'unknown' —
    callers should fall back to letting all configured providers try."""
    info = REGISTRY.get(canonical)
    if info is None:
        return set()
    return set(info.slugs.keys())


def filter_supported(canonical: str, candidates: Iterable[str]) -> List[str]:
    """Return the subset of ``candidates`` that support the model. If the model
    is unknown to the registry, returns the candidates unchanged."""
    supported = providers_for(canonical)
    if not supported:
        return list(candidates)
    return [p for p in candidates if p in supported]


def model_info(canonical: str) -> Optional[ModelInfo]:
    return REGISTRY.get(canonical)


def is_image_edit(canonical: str) -> bool:
    info = REGISTRY.get(canonical)
    return info is not None and info.kind == "image-edit"
