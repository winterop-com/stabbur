"""Aggregate listing and pulling across every source store."""

from pathlib import Path

from local_llm.config import get_settings
from local_llm.models import Catalog, ModelEntry, ModelSource, PullResult
from local_llm.sources import huggingface, lmstudio, ollama


def list_models(source: ModelSource | None = None) -> Catalog:
    """Build a catalog of models across one or all sources.

    Args:
        source: Limit the scan to a single source, or ``None`` for all sources.

    Returns:
        A :class:`Catalog` of every discovered model.
    """
    entries: list[ModelEntry] = []
    if source in (None, ModelSource.huggingface):
        entries.extend(huggingface.list_models())
    if source in (None, ModelSource.ollama):
        entries.extend(ollama.list_models())
    if source in (None, ModelSource.lmstudio):
        entries.extend(lmstudio.list_models())
    return Catalog(entries=entries)


def pull(source: ModelSource, name: str, backup_root: Path | None = None, move: bool = False) -> PullResult:
    """Pull a single model from the given source into the library.

    Args:
        source: Which source the model belongs to.
        name: The model identifier as reported by :func:`list_models`.
        backup_root: Override for the destination root; defaults to the
            configured ``backup_root``.
        move: If true, delete the local source after a verified copy. Currently
            supported for LM Studio only.

    Returns:
        A :class:`PullResult` describing what was written.

    Raises:
        NotImplementedError: If ``move`` is requested for a source that does not
            support it yet.
    """
    root = backup_root or get_settings().backup_root
    match source:
        case ModelSource.huggingface:
            if move:
                raise NotImplementedError("--move is not supported for Hugging Face yet")
            return huggingface.pull(name, root, token=get_settings().hf_token)
        case ModelSource.ollama:
            return ollama.pull(name, root, move=move)
        case ModelSource.lmstudio:
            return lmstudio.pull(name, root, move=move)
