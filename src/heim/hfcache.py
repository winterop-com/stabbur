"""Redirect the Hugging Face hub cache onto the library drive.

Some runtimes fetch assets by repo id straight from the HF cache rather than from
heim's library — e.g. an mlx-audio model that fetches a companion codec
(``mlx-community/descript-audio-codec-44khz``) via ``snapshot_download`` with no
``cache_dir``. Left alone that lands in ``~/.cache/huggingface`` on the machine, so
such a model breaks offline or on another machine even though it is on the drive.

Pointing ``HF_HOME`` at ``<library_root>/.cache/huggingface`` makes those assets live
on the drive and travel with it. huggingface_hub freezes its cache path from
``HF_HOME``/``HF_HUB_CACHE`` **at import time**, so this must run before hf_hub is first
imported — hence :func:`configure` is called from :mod:`heim` (the package ``__init__``)
before any submodule pulls hf_hub in.
"""

from __future__ import annotations

import os
from pathlib import Path


def drive_cache_dir() -> Path | None:
    """The on-drive HF cache dir, or ``None`` if there's no real configured library.

    Only a configured library that exists on disk (a mounted drive) is used; an unconfigured
    library (``library_root is None``) and a missing path fall back to the machine cache.
    """
    try:
        from heim.config import get_settings  # noqa: PLC0415 - avoid import cycle at module load

        root = get_settings().library_root
    except Exception:  # noqa: BLE001 - never let config errors break importing heim
        return None
    if root is None or not root.is_dir():
        return None
    return root / ".cache" / "huggingface"


def configure() -> Path | None:
    """Point ``HF_HOME`` at the drive cache, unless the user already set an HF cache env.

    Idempotent and best-effort: returns the cache dir it set (for callers/tests), or
    ``None`` if it left the environment untouched.
    """
    if os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE"):
        return None
    target = drive_cache_dir()
    if target is None:
        return None
    os.environ["HF_HOME"] = str(target)
    return target
