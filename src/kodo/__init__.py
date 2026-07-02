"""Backup and browse local LLM models from Hugging Face, Ollama, and LM Studio."""

import os

# Ask huggingface_hub for high-performance Xet transfer (the fast, parallel path
# in hub >=1.21, backed by the hf_xet dependency) — big model pulls otherwise fall
# back to slow single-stream HTTP. Set before huggingface_hub is imported anywhere.
# (The old HF_HUB_ENABLE_HF_TRANSFER flag is deprecated/removed in 1.21.)
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

__version__ = "0.1.0"
