"""Model-card and metadata sidecars for the backup library.

Each backed-up model gets a ``.kodo/`` sidecar holding:

* ``metadata.json`` — structured info (source, name, size, files, extras).
* ``model-card.md`` — human-readable instructions: the upstream model card for
  Hugging Face / LM Studio, or a generated Modelfile-style card for Ollama.

The sidecar lives inside the model directory for Hugging Face and LM Studio. For
Ollama, whose store is content-addressed and must keep its native layout to stay
restorable, the sidecar goes under ``<backup_root>/ollama/.library/<name>/``.
"""

import json
from pathlib import Path
from typing import Any

SIDECAR_DIR = ".kodo"
"""Name of the per-model sidecar directory."""

_CARD_CANDIDATES = ("README.md", "model_card.md", "MODEL_CARD.md", "modelcard.md")


def write_metadata(sidecar_dir: Path, data: dict[str, Any]) -> Path:
    """Write ``metadata.json`` into ``sidecar_dir`` and return its path."""
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / "metadata.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    return path


def write_card(sidecar_dir: Path, markdown: str) -> Path:
    """Write a generated ``model-card.md`` into ``sidecar_dir`` and return its path."""
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / "model-card.md"
    path.write_text(markdown)
    return path


def find_card(model_dir: Path) -> Path | None:
    """Return an existing model card inside ``model_dir``, if any.

    Looks for common card filenames (``README.md`` etc.) at the top level; this
    is how Hugging Face and most LM Studio downloads ship their instructions.
    """
    for name in _CARD_CANDIDATES:
        candidate = model_dir / name
        if candidate.is_file():
            return candidate
    return None
