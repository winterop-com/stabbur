"""Model-card and metadata sidecars for the backup library.

Each backed-up model gets a ``.stabbur/`` sidecar holding:

* ``metadata.json`` — structured info (source, name, size, files, extras).
* ``model-card.md`` — human-readable instructions: the upstream model card for
  Hugging Face / LM Studio, or a generated Modelfile-style card for Ollama.

The sidecar lives inside the model directory for Hugging Face and LM Studio. For
Ollama, whose store is content-addressed and must keep its native layout to stay
restorable, the sidecar goes under ``<library_root>/ollama/.library/<name>/``.
"""

import json
from pathlib import Path
from typing import Any

from stabbur import fsatomic

SIDECAR_DIR = ".stabbur"
"""Name of the per-model sidecar directory (what a new pull writes)."""


def sidecar_dir(model_dir: Path) -> Path:
    """The sidecar directory for ``model_dir``."""
    return model_dir / SIDECAR_DIR


_CARD_CANDIDATES = ("README.md", "model_card.md", "MODEL_CARD.md", "modelcard.md")


def write_metadata(sidecar_dir: Path, data: dict[str, Any]) -> Path:
    """Write ``metadata.json`` into ``sidecar_dir`` and return its path."""
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / "metadata.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def read_metadata(sidecar_dir: Path) -> dict[str, Any]:
    """Read a sidecar's ``metadata.json``, or ``{}`` when it's absent, unreadable, or not an object.

    The single reader for the sidecar: verification, the want list and the consumer bookkeeping all
    go through it, so a hand-edited or half-written file degrades to "nothing recorded" everywhere
    rather than raising in one caller and silently passing in another.
    """
    try:
        data = json.loads((sidecar_dir / "metadata.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_metadata(sidecar_dir: Path, updates: dict[str, Any]) -> Path:
    """Merge ``updates`` into a sidecar's ``metadata.json`` and return its path.

    Read-modify-write, so recording one new fact (e.g. the name a model was installed into a
    runtime under) keeps everything the pull recorded. Written atomically — the sidecar is the only
    record of a model's provenance, and an unclean eject mid-write must not truncate it.
    """
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / "metadata.json"
    merged = read_metadata(sidecar_dir) | updates
    fsatomic.write_text(path, json.dumps(merged, indent=2, default=str))
    return path


def write_card(sidecar_dir: Path, markdown: str) -> Path:
    """Write a generated ``model-card.md`` into ``sidecar_dir`` and return its path."""
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / "model-card.md"
    path.write_text(markdown, encoding="utf-8")
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


def has_card(model_dir: Path) -> bool:
    """Whether ``model_dir`` already has a card — a top-level README/… or a written sidecar."""
    return find_card(model_dir) is not None or (sidecar_dir(model_dir) / "model-card.md").is_file()


def fetch_hf_readme(repo_id: str, token: str | None = None) -> str | None:
    """Fetch a repo's ``README.md`` (its model card) from the Hugging Face Hub, or ``None``.

    Backfills a card for a library model that shipped without one — some LM Studio downloads and
    older pulls have no README. The library model's ``<publisher>/<repo>`` name is the HF repo id.
    Best-effort: a repo that isn't on HF, has no README, or being offline all yield ``None`` rather
    than raising.
    """
    try:
        from huggingface_hub import hf_hub_download  # noqa: PLC0415 - heavy import; only when fetching

        path = hf_hub_download(repo_id=repo_id, filename="README.md", token=token)
        return Path(path).read_text(errors="replace")
    except Exception:  # noqa: BLE001 - missing repo/README, network error, etc. → no card
        return None
