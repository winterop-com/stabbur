"""LM Studio source adapter.

LM Studio stores models under ``~/.lmstudio/models/<publisher>/<repo>/`` (the
files originate from Hugging Face). A model directory holds either GGUF files
(``*.gguf``) or MLX weights (``*.safetensors`` + ``config.json``). Each such
directory is treated as a model; backing up copies it into the library under a
top-level format directory (``gguf/`` or ``mlx/``).
"""

import shutil
from pathlib import Path

from heim import arch, cards
from heim.config import get_settings
from heim.models import ModelEntry, ModelFormat, ModelSource, PullResult
from heim.sources.base import copy_tree, copy_verified, dir_stats, safe_join


def _classify(model_dir: Path) -> ModelFormat:
    """Classify a model directory by the weight files it contains.

    GGUF wins if present; otherwise ``*.safetensors`` in an LM Studio store is
    MLX (LM Studio only runs safetensors weights via MLX on Apple Silicon).
    """
    if any(model_dir.glob("*.gguf")):
        return ModelFormat.gguf
    if any(model_dir.glob("*.safetensors")):
        return ModelFormat.mlx
    return ModelFormat.unknown


def _model_dirs(root: Path) -> list[Path]:
    """Return the distinct directories under ``root`` that hold weight files."""
    dirs = {weights.parent for weights in root.rglob("*.gguf")}
    dirs |= {weights.parent for weights in root.rglob("*.safetensors")}
    return sorted(dirs)


def list_models(models_dir: Path | None = None) -> list[ModelEntry]:
    """List GGUF and MLX models stored by LM Studio.

    Args:
        models_dir: Override for the LM Studio models directory. Defaults to the
            configured ``lmstudio_models_dir``.

    Returns:
        One :class:`ModelEntry` per model directory, tagged with its format.
    """
    root = models_dir or get_settings().lmstudio_models_dir
    if not root.is_dir():
        return []

    entries: list[ModelEntry] = []
    for model_dir in _model_dirs(root):
        size_bytes, file_count = dir_stats(model_dir)
        model_format = _classify(model_dir)
        entries.append(
            ModelEntry(
                source=ModelSource.lmstudio,
                name=model_dir.relative_to(root).as_posix(),
                model_format=model_format,
                generative=arch.is_generative(model_format, model_dir),
                path=model_dir,
                size_bytes=size_bytes,
                file_count=file_count,
            )
        )
    return entries


def pull(name: str, library_root: Path, models_dir: Path | None = None, move: bool = False) -> PullResult:
    """Copy an LM Studio model directory into the library, organized by format.

    Args:
        name: The ``publisher/repo`` name as reported by :func:`list_models`.
        library_root: Destination library root; the model lands in
            ``library_root/<format>/<name>`` (e.g. ``mlx/lmstudio-community/...``).
        models_dir: Override for the LM Studio models directory.
        move: If true, delete the local source after verifying the copy is
            byte-for-byte complete (frees local disk; leaves 0 local copies).

    Returns:
        A :class:`PullResult` describing what was copied.

    Raises:
        FileNotFoundError: If ``name`` does not resolve to a directory.
    """
    root = models_dir or get_settings().lmstudio_models_dir
    # ``name`` is untrusted (HTTP query / CLI arg); keep both the source read and the
    # library write strictly under their roots so an absolute path or ``..`` can't
    # copy (or, with --move, delete) arbitrary directories.
    src = safe_join(root, name)
    if not src.is_dir():
        raise FileNotFoundError(f"No LM Studio model at {src}")

    model_format = _classify(src)
    dest = safe_join(library_root, f"{model_format.value}/{name}")
    size_bytes, file_count = copy_tree(src, dest)

    # Only delete the source after a per-file verified copy (same paths + sizes), not a mere
    # aggregate-total match — a corrupt/short copy or a different file set must keep the source.
    source_removed = move and copy_verified(src, dest)
    if source_removed:
        shutil.rmtree(src)

    card_path = cards.find_card(dest)
    metadata_path = cards.write_metadata(
        dest / cards.SIDECAR_DIR,
        {
            "source": ModelSource.lmstudio.value,
            "name": name,
            "format": model_format.value,
            "size_bytes": size_bytes,
            "file_count": file_count,
            "card": card_path.name if card_path else None,
        },
    )
    return PullResult(
        source=ModelSource.lmstudio,
        name=name,
        model_format=model_format,
        destination=dest,
        size_bytes=size_bytes,
        file_count=file_count,
        card_path=card_path,
        metadata_path=metadata_path,
        source_removed=source_removed,
    )
