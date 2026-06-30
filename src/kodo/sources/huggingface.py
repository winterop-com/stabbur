"""Hugging Face source adapter.

Listing reads the local HF cache via :func:`huggingface_hub.scan_cache_dir`.
Backing up uses :func:`huggingface_hub.snapshot_download`, which is resumable,
parallel, and checksum-verified, writing directly into the backup root.
"""

from pathlib import Path
from typing import Any

from huggingface_hub import scan_cache_dir, snapshot_download

from kodo import arch, cards
from kodo.models import ModelEntry, ModelFormat, ModelSource, PullResult
from kodo.sources.base import dir_stats


def _classify(repo: Any) -> ModelFormat:
    """Classify a cached repo by the weight file types it contains.

    GGUF wins if present; otherwise ``*.safetensors`` is MLX for ``mlx-community``
    repos and plain safetensors elsewhere. Repos with neither (datasets, ``.bin``
    only, vision/embedding configs) are left ``unknown``.
    """
    suffixes = {Path(file.file_name).suffix for rev in repo.revisions for file in rev.files}
    if ".gguf" in suffixes:
        return ModelFormat.gguf
    if ".safetensors" in suffixes:
        return ModelFormat.mlx if repo.repo_id.startswith("mlx-community/") else ModelFormat.safetensors
    return ModelFormat.unknown


def list_models() -> list[ModelEntry]:
    """List models present in the local Hugging Face cache.

    Returns:
        One :class:`ModelEntry` per cached repo. Returns an empty list if the
        cache directory does not exist yet.
    """
    try:
        cache = scan_cache_dir()
    except Exception:  # noqa: BLE001 - cache may be absent or unreadable
        return []

    entries: list[ModelEntry] = []
    for repo in cache.repos:
        if repo.repo_type != "model":
            continue  # skip datasets (e.g. mnist) and spaces — not models
        model_format = _classify(repo)
        snapshot = next((Path(rev.snapshot_path) for rev in repo.revisions), Path(repo.repo_path))
        entries.append(
            ModelEntry(
                source=ModelSource.huggingface,
                name=repo.repo_id,
                model_format=model_format,
                generative=arch.is_generative(model_format, snapshot),
                path=Path(repo.repo_path),
                size_bytes=repo.size_on_disk,
                file_count=sum(len(rev.files) for rev in repo.revisions),
            )
        )
    return entries


def pull(repo_id: str, backup_root: Path, token: str | None = None) -> PullResult:
    """Download ``repo_id`` into the backup root.

    Args:
        repo_id: The Hugging Face repo to download (e.g. ``"meta-llama/Llama-3.2-1B"``).
        backup_root: Destination root; the model lands in
            ``backup_root/huggingface/<repo_id>``.
        token: Optional access token for gated or private repos.

    Returns:
        A :class:`PullResult` describing what was written.
    """
    dest = backup_root / ModelSource.huggingface.value / repo_id
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=dest,
        token=token,
    )
    size_bytes, file_count = dir_stats(dest)

    # The snapshot already ships the model card as README.md; record it and a
    # metadata sidecar without re-downloading anything.
    card_path = cards.find_card(dest)
    metadata_path = cards.write_metadata(
        dest / cards.SIDECAR_DIR,
        {
            "source": ModelSource.huggingface.value,
            "name": repo_id,
            "size_bytes": size_bytes,
            "file_count": file_count,
            "card": card_path.name if card_path else None,
        },
    )
    return PullResult(
        source=ModelSource.huggingface,
        name=repo_id,
        destination=dest,
        size_bytes=size_bytes,
        file_count=file_count,
        card_path=card_path,
        metadata_path=metadata_path,
    )
