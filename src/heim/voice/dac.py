"""Seed mlx-audio's DAC codec into the HF cache so Dia is self-contained.

Dia decodes audio through ``mlx-community/descript-audio-codec-44khz``, which mlx-audio
fetches by repo id from the HF cache at synth time — separately from Dia's own weights in
the library. With the cache redirected onto the drive (:mod:`heim.hfcache`), pre-seeding
this once makes Dia fully portable and offline-capable: the codec then lives on the drive
alongside the model instead of only in the machine's ``~/.cache/huggingface``.
"""

from __future__ import annotations

from pathlib import Path

from heim.voice.catalog import hf_hub_cache

# The codec Dia loads via ``DAC.from_pretrained`` (~293 MB).
DAC_REPO = "mlx-community/descript-audio-codec-44khz"


def codec_dir() -> Path:
    """Where the DAC codec lives in the (possibly drive-redirected) HF cache."""
    return hf_hub_cache() / f"models--{DAC_REPO.replace('/', '--')}"


def codec_present() -> bool:
    """Whether the DAC codec is already cached (a snapshot with real files)."""
    snapshots = codec_dir() / "snapshots"
    if not snapshots.is_dir():
        return False
    return any(p.is_file() for p in snapshots.rglob("*") if not p.name.startswith("._"))


def seed_codec(token: str | None = None) -> Path:
    """Download the DAC codec into the HF cache (idempotent) and return its snapshot path.

    Uses the same ``snapshot_download`` mlx-audio uses (weights + config only), so once
    seeded, Dia's ``from_pretrained`` finds it locally — no network needed at synth time.
    """
    from huggingface_hub import snapshot_download  # noqa: PLC0415 - only needed on the seed path

    return Path(snapshot_download(DAC_REPO, allow_patterns=["*.safetensors", "*.json"], token=token))
