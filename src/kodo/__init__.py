"""Backup and browse local LLM models from Hugging Face, Ollama, and LM Studio."""

import os

# Ask huggingface_hub for high-performance Xet transfer (the fast, parallel path
# in hub >=1.21, backed by the hf_xet dependency) — big model pulls otherwise fall
# back to slow single-stream HTTP. Set before huggingface_hub is imported anywhere.
# (The old HF_HUB_ENABLE_HF_TRANSFER flag is deprecated/removed in 1.21.)
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

# Point the HF hub cache at the library drive (before huggingface_hub is imported), so
# assets some runtimes fetch by repo id — e.g. mlx-audio's Dia DAC codec — travel with
# the drive instead of living in ~/.cache/huggingface. No-op if the user set HF_HOME/
# HF_HUB_CACHE, or there's no real configured library. See kodo.hfcache.
from kodo import hfcache as _hfcache  # noqa: E402

_hfcache.configure()

__version__ = "0.1.0"
