"""Hugging Face source adapter.

Listing reads the local HF cache via :func:`huggingface_hub.scan_cache_dir`.
Backing up uses :func:`huggingface_hub.snapshot_download`, which is resumable,
parallel, and checksum-verified, writing directly into the backup root.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, scan_cache_dir, snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

from stabbur import arch, cards
from stabbur.models import HubModel, ModelEntry, ModelFormat, ModelSource, PullResult
from stabbur.sources.base import dir_stats, safe_join

# Preferred GGUF quant when a repo ships several (mirrors the library's pick), used
# to estimate what a pull would actually download rather than the whole repo.
_QUANT_PREFERENCE = ("Q4_K_M", "Q4_K_S", "Q5_K_M", "Q4_0", "Q8_0")

# The Hub answers "no such repo" and "you may not see this repo" identically — a 401 with
# "Invalid username or password" — because saying which would leak the existence of private
# repos. Raw, that reads as "stabbur has bad credentials"; it almost always means a typo.
_ACCESS_STATUSES = frozenset({401, 403, 404})


def _pull_size(repo: str) -> int:
    """Approximate bytes ``stabbur pull`` would download for ``repo``.

    A GGUF repo often ships many quant variants (the *whole* repo can be hundreds
    of GB), but a pull grabs one quant — so estimate the **preferred quant + any
    mmproj**. Non-GGUF repos (safetensors/MLX) report their full size. 0 on error.
    """
    try:
        info = HfApi().model_info(repo, files_metadata=True)
    except Exception:  # noqa: BLE001 - network/API errors → unknown size
        return 0
    files = [(s.rfilename, s.size or 0) for s in (info.siblings or [])]
    ggufs = [(n, sz) for n, sz in files if n.lower().endswith(".gguf")]
    if not ggufs:
        return sum(sz for _, sz in files)  # safetensors / MLX repo → full size
    mmproj = sum(sz for n, sz in ggufs if "mmproj" in n.lower())
    weights = [(n, sz) for n, sz in ggufs if "mmproj" not in n.lower()]
    for quant in _QUANT_PREFERENCE:
        match = [sz for n, sz in weights if quant.lower() in n.lower()]
        if match:  # a quant may be split across shards → sum them
            return sum(match) + mmproj
    return min((sz for _, sz in weights), default=0) + mmproj


def search(query: str, limit: int = 20, gguf: bool = False, sizes: bool = True) -> list[HubModel]:
    """Search the Hugging Face Hub for models to pull, by downloads.

    Args:
        query: Free-text search (name/description).
        limit: Max results.
        gguf: If true, restrict to GGUF repos (llama.cpp-ready).
        sizes: If true, also fetch each result's approximate pull size (concurrently).

    Returns:
        Matching models, most-downloaded first. Empty on any error (e.g. offline).
    """
    try:
        infos = HfApi().list_models(
            search=query,
            filter="gguf" if gguf else None,  # tag filter
            sort="downloads",
            limit=limit,
        )
    except Exception:  # noqa: BLE001 - network/API errors → no results
        return []
    results = [HubModel(id=i.id, downloads=i.downloads or 0, likes=i.likes or 0) for i in infos]
    if sizes and results:
        # Size needs a per-repo file listing; fetch them concurrently to stay snappy.
        with ThreadPoolExecutor(max_workers=8) as pool:
            for model, size in zip(results, pool.map(_pull_size, [m.id for m in results])):
                model.size_bytes = size
    return results


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


def hub_format(repo_id: str, token: str | None = None) -> ModelFormat:
    """Detect a repo's format from the Hub's file list, *before* downloading it.

    GGUF wins if present; otherwise ``*.safetensors`` is MLX for ``mlx-community`` repos or any
    repo whose name says ``mlx`` (e.g. ``lmstudio-community/…-MLX-4bit``), else plain safetensors.
    ``unknown`` (no weights, or the listing failed) falls back to the source bucket.
    """
    try:
        files = HfApi().list_repo_files(repo_id, token=token)
    except Exception:  # noqa: BLE001 - a missing/gated repo or network error → let the download report it
        return ModelFormat.unknown
    suffixes = {Path(f).suffix.lower() for f in files}
    if ".gguf" in suffixes:
        return ModelFormat.gguf
    if ".safetensors" in suffixes:
        is_mlx = repo_id.startswith("mlx-community/") or "mlx" in repo_id.rsplit("/", 1)[-1].lower()
        return ModelFormat.mlx if is_mlx else ModelFormat.safetensors
    return ModelFormat.unknown


def _access_error(repo_id: str, exc: Exception) -> Exception:
    """Map a Hub download failure to a one-line message, or hand back the original error.

    Only the "can't get at this repo" family is rewritten (missing, gated, or unauthorized); a disk
    error, a network drop or a checksum failure keeps its own message, which is the useful one.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, RepositoryNotFoundError | GatedRepoError) or (
        isinstance(exc, HfHubHTTPError) and status in _ACCESS_STATUSES
    ):
        return RuntimeError(f"repo not found or requires authentication: {repo_id}")
    return exc


def _prune_empty(leaf: Path, stop: Path) -> None:
    """Remove ``leaf`` and its now-empty parents up to (never including) ``stop``.

    Rolls back the directory tree a failed pull created: a bad repo id used to leave an empty
    ``<bucket>/<org>/<repo>/`` behind, which then showed up as litter in the library. Uses
    ``rmdir``, which refuses a directory with anything in it — so a pull that got *some* files
    down, or a dir that already held another model, is never removed.
    """
    current = leaf
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return  # not empty (or not removable) — stop, and leave everything above it alone
        current = current.parent


def pull(
    repo_id: str,
    library_root: Path,
    token: str | None = None,
    include: list[str] | None = None,
    model_format: ModelFormat | None = None,
) -> PullResult:
    """Download ``repo_id`` into the library, organized by format.

    Args:
        repo_id: The Hugging Face repo to download (e.g. ``"meta-llama/Llama-3.2-1B"``).
        library_root: Destination root; the model lands in the **format-centric** layout
            ``library_root/<format>/<repo_id>`` (``gguf/…``, ``mlx/…``, ``safetensors/…``) — the
            same buckets LM Studio pulls into, so a GGUF pulled from either source is one copy.
            A repo with no recognizable weights falls back to ``library_root/huggingface/<repo_id>``.
        token: Optional access token for gated or private repos.
        include: Optional filename globs to restrict the download (e.g.
            ``["*Q4_K_M*"]`` to pull one GGUF quant from a multi-quant repo).
            Small sidecars (``*.md`` / ``*.json`` / ``*.txt``) are always kept so
            the model card and configs come along.
        model_format: Skip Hub detection and force the destination bucket (used by the CLI when
            it already knows the format).

    Returns:
        A :class:`PullResult` describing what was written.

    Raises:
        RuntimeError: The repo doesn't exist, is gated, or needs a token stabbur doesn't have.
    """
    # Pick the format bucket from the Hub's file list; unknown → the source bucket (old layout).
    fmt = model_format or hub_format(repo_id, token)
    bucket = fmt.value if fmt is not ModelFormat.unknown else ModelSource.huggingface.value
    # Validate the (untrusted) repo id keeps the destination under the library root
    # *before* creating any directory — an absolute path or ``..`` would otherwise
    # mkdir outside the library even if the download later fails.
    dest = safe_join(library_root, f"{bucket}/{repo_id}")
    dest.mkdir(parents=True, exist_ok=True)
    allow_patterns = list(dict.fromkeys([*include, "*.md", "*.json", "*.txt"])) if include else None
    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=dest,
            token=token,
            allow_patterns=allow_patterns,
        )
    except Exception as exc:
        _prune_empty(dest, library_root)  # roll back the tree we just made, if nothing landed in it
        raise _access_error(repo_id, exc) from exc
    size_bytes, file_count = dir_stats(dest)

    # The snapshot already ships the model card as README.md; record it and a
    # metadata sidecar without re-downloading anything.
    card_path = cards.find_card(dest)
    metadata_path = cards.write_metadata(
        dest / cards.SIDECAR_DIR,
        {
            "source": ModelSource.huggingface.value,
            "name": repo_id,
            # What this pull actually fetched. Without it a want list can only say "the repo", so
            # `library sync` re-downloads every quant of a multi-quant repo to rebuild one of them.
            "include": list(include) if include else None,
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


def pull_tts(
    repo_id: str,
    vocoder_repo: str,
    library_root: Path,
    token: str | None = None,
    include: list[str] | None = None,
) -> PullResult:
    """Download a TTS model + its vocoder co-located under the ``tts/`` section.

    A TTS model needs a paired vocoder (e.g. WavTokenizer) to produce audio, so
    both land in one directory (``library_root/tts/<repo_id>/``) — the layout the
    library scan recognizes as a TTS model. ``include`` restricts the TTS model
    files (e.g. one GGUF quant); the vocoder's GGUF(s) are always fetched.
    """
    dest = safe_join(library_root, f"tts/{repo_id}")  # keep the write under the library root
    dest.mkdir(parents=True, exist_ok=True)
    allow = list(dict.fromkeys([*include, "*.md", "*.json", "*.txt"])) if include else None
    try:
        snapshot_download(repo_id=repo_id, local_dir=dest, token=token, allow_patterns=allow)
        # Co-locate the vocoder GGUF so the scan pairs them into one TTS model.
        snapshot_download(repo_id=vocoder_repo, local_dir=dest, token=token, allow_patterns=["*.gguf"])
    except Exception as exc:
        _prune_empty(dest, library_root)  # same rollback as `pull`: never leave an empty tree behind
        raise _access_error(repo_id, exc) from exc
    size_bytes, file_count = dir_stats(dest)
    card_path = cards.find_card(dest)
    metadata_path = cards.write_metadata(
        dest / cards.SIDECAR_DIR,
        {
            "source": ModelSource.huggingface.value,
            "name": repo_id,
            "vocoder": vocoder_repo,
            "include": list(include) if include else None,
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
