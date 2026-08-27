"""Library mutation: remove a model, plan/apply the layout migration, and verify integrity."""

import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from stabbur import cards, locking
from stabbur.library._model import LibraryModel, _classify_dir
from stabbur.models import ModelFormat, _human_size
from stabbur.sources import ollama
from stabbur.sources.base import copy_verified, dir_stats


def remove(model: LibraryModel) -> tuple[int, int]:
    """Delete a library model's files from disk. Returns ``(files_removed, bytes_freed)``.

    Directory-based models (gguf/mlx/…) are removed by deleting their directory.
    Ollama models delegate to :func:`stabbur.sources.ollama.remove`, which preserves
    blobs still shared with other installed Ollama models.
    """
    freed, count = model.size_bytes, model.file_count
    # Serialize the whole delete-and-drop-tags against a concurrent CLI/serve mutation on the
    # same library (A5). We hold the lock across ``_drop_tags``, so it writes unlocked (below)
    # rather than call the self-locking ``tags.set_tags`` (which would deadlock a second flock).
    with locking.library_lock(model.library_root):
        if model.is_ollama:
            # The Ollama store root is the manifest's ancestor before ``manifests/``;
            # ollama.remove handles shared-blob safety (it may keep shared layers, so
            # ``freed`` is the model's own weight size — a close upper bound).
            parts = model.path.parts
            models_dir = Path(*parts[: parts.index("manifests")])
            ollama.remove(model.name, models_dir)
            _drop_tags(model)
            return count, freed

        shutil.rmtree(model.path, ignore_errors=True)
        if model.path.exists():
            return 0, 0  # removal failed (read-only drive, files held open by a running runtime)
        _drop_tags(model)
        return count, freed


def _drop_tags(model: LibraryModel) -> None:
    """Drop a removed model's tag assignments so a later re-pull doesn't silently inherit them.

    Tags are keyed by name alone while model identity is ``(name, format)``: when another
    format copy of the same name survives in this library (e.g. removing the GGUF while the
    safetensors copy stays), the tags are still that copy's and must be kept.

    Writes without acquiring the library lock — callers (``remove``) already hold it.
    """
    from stabbur import tags  # noqa: PLC0415 - lazy to keep import order simple
    from stabbur.library._scan import scan  # noqa: PLC0415 - lazy to keep import order simple

    if any(m.name == model.name for m in scan(model.library_root)):
        return
    mapping = tags.load(model.library_root)
    if mapping.pop(model.name, None) is not None:
        tags.save(model.library_root, mapping)


class MigrationAction(BaseModel):
    """One planned step of reorganizing an old ``huggingface/`` pull into a format bucket."""

    model_config = ConfigDict(frozen=True)

    repo_id: str  # e.g. "unsloth/Llama-3.2-1B-Instruct-GGUF"
    src: Path  # the current huggingface/<repo_id> directory
    dest: Path  # the target <format>/<repo_id> directory
    model_format: ModelFormat
    kind: str  # "move" (relocate to the bucket) or "dedup" (bucket copy exists → remove src)
    size_bytes: int = 0


def _hf_model_dirs(hf_dir: Path) -> list[Path]:
    """Directories directly holding weights under ``huggingface/`` (each an old-layout model)."""
    dirs: set[Path] = set()
    for pattern in ("*.gguf", "*.safetensors"):
        for weights in hf_dir.rglob(pattern):
            if not weights.name.startswith("._"):
                dirs.add(weights.parent)
    return sorted(dirs)


def plan_migration(root: Path) -> list[MigrationAction]:
    """Plan moving each old ``huggingface/<repo>`` pull into its format bucket (``gguf/`` etc.).

    A repo whose format is classifiable becomes a ``move`` — or a ``dedup`` if the same
    ``<format>/<repo>`` already exists (the ``huggingface/`` copy is then redundant). Repos with
    no recognizable weights are left in ``huggingface/`` (its legitimate fallback role). Nothing
    is touched here — see :func:`apply_migration`.
    """
    hf_dir = root / "huggingface"
    if not hf_dir.is_dir():
        return []
    actions: list[MigrationAction] = []
    for src in _hf_model_dirs(hf_dir):
        fmt = _classify_dir(src)
        if fmt is ModelFormat.unknown:
            continue
        repo_id = str(src.relative_to(hf_dir))
        dest = root / fmt.value / repo_id
        if dest.exists():
            # Only call it a dedup when the bucket copy is a *verified* duplicate of the
            # huggingface/ one — a partial/empty/different dir there must NOT license deleting
            # the complete huggingface/ copy. An unverified conflict is left untouched.
            if not copy_verified(src, dest):
                continue
            kind = "dedup"
        else:
            kind = "move"
        actions.append(
            MigrationAction(
                repo_id=repo_id, src=src, dest=dest, model_format=fmt, kind=kind, size_bytes=dir_stats(src)[0]
            )
        )
    return actions


def apply_migration(actions: list[MigrationAction]) -> tuple[int, int, int]:
    """Execute a plan: relocate ``move`` dirs, delete redundant ``dedup`` dirs.

    Moves are same-drive renames (instant, no copy). Returns ``(moved, deduped, bytes_freed)``.
    Prunes any ``huggingface/`` subdirectories left empty afterwards.
    """
    moved = deduped = freed = 0
    hf_roots: set[Path] = set()
    for action in actions:
        parts = action.src.parts
        if "huggingface" in parts:
            hf_roots.add(Path(*parts[: parts.index("huggingface") + 1]))
        if action.kind == "move":
            if action.dest.exists():
                # A dest appeared between plan and apply (concurrent pull). Never move-into it
                # (that would nest src as gguf/pub/Repo/Repo). If it's a verified dup, drop src;
                # otherwise leave both untouched.
                if copy_verified(action.src, action.dest):
                    shutil.rmtree(action.src, ignore_errors=True)
                    deduped += 1
                    freed += action.size_bytes
                continue
            action.dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(action.src), str(action.dest))
            moved += 1
        else:
            # dedup: the bucket had a verified copy at plan time — re-verify it's still complete
            # before deleting src (a concurrent remove/pull may have deleted or truncated it
            # between plan and apply; same guard as the move branch's dest-appeared case).
            if copy_verified(action.src, action.dest):
                shutil.rmtree(action.src, ignore_errors=True)
                deduped += 1
                freed += action.size_bytes
    # Tidy up now-empty publisher dirs (and huggingface/ itself) left behind by the moves.
    for hf_root in hf_roots:
        for empty in sorted((p for p in hf_root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            if not any(empty.iterdir()):
                empty.rmdir()
        if hf_root.is_dir() and not any(hf_root.iterdir()):
            hf_root.rmdir()
    return moved, deduped, freed


class VerifyResult(BaseModel):
    """Integrity check of one library model against its recorded metadata."""

    model_config = ConfigDict(frozen=True)

    name: str
    ok: bool
    checked: str  # what was verified: "size+files+card", "blobs (N)", "blobs+sha256 (N)", or "—"
    issues: list[str] = []

    notes: list[str] = []
    """Things worth saying that are **not** damage — the model still counts as ok.

    Kept apart from ``issues`` on purpose: a note must never turn a healthy drive into a non-zero
    exit. See :func:`_stats_issues` for the one case that produces them today.
    """


# A sidecar written before ``dir_stats`` learned to exclude bookkeeping (huggingface_hub's
# ``.cache/``, stabbur's own ``.stabbur/``, macOS ``._`` AppleDouble files on exFAT) recorded those
# files in its totals, so a perfectly healthy old pull now measures a handful of files short and a
# few dozen KB light. Two bounds have to hold before a difference is written off that way, because
# either alone can be talked into excusing real loss: the missing bytes must average small **per
# missing file entry** (a weight file is orders of magnitude bigger than any bookkeeping file), and
# they must be a rounding error **against the model as a whole** (so a small model losing most of
# itself is still reported, however few file entries that took).
_BOOKKEEPING_MAX_BYTES = 1 << 20  # 1 MiB per missing file entry
_BOOKKEEPING_MAX_FRACTION = 0.05  # and at most this share of the recorded total


def _weights_issues(model: LibraryModel) -> list[str]:
    """Problems with a format model's on-disk weights: missing, empty, or (MLX) no ``.safetensors``."""
    issues: list[str] = []
    target = model.load_target
    if not target.exists():
        return [f"weights missing ({target.name})"]
    if target.is_file():
        if target.stat().st_size == 0:
            issues.append(f"weight file is empty ({target.name})")
    else:  # MLX / safetensors: load_target is the model directory
        weights = [w for w in target.glob("*.safetensors") if not w.name.startswith("._")]
        if not weights:
            issues.append("no .safetensors in the model directory")
        elif any(w.stat().st_size == 0 for w in weights):
            issues.append("an empty .safetensors file")
    if model.mmproj is not None and not model.mmproj.exists():
        issues.append(f"projector missing ({model.mmproj.name})")
    return issues


def _size_mismatch(size: int, recorded: int) -> str:
    """Phrase a size mismatch, falling back to exact byte counts when both round to the same words.

    ``_human_size`` keeps one decimal, so a 45 KB gap in a 16 GB model renders as
    "size 16.3 GB != recorded 16.3 GB" — a line that reads as a bug in stabbur rather than a
    finding about the model. When the two sides look identical, say the actual numbers.
    """
    on_disk, was = _human_size(size), _human_size(recorded)
    if on_disk == was:
        return f"size {size:,} bytes != recorded {recorded:,} bytes"
    return f"size {on_disk} != recorded {was}"


def _stats_issues(path: Path, meta: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Compare the sidecar's recorded size and file count against what is on disk now.

    This is what catches a *truncated* pull: the weights file still exists and is non-empty, so
    the structural checks pass, but it is short. Both sides are measured with :func:`dir_stats`,
    which skips bookkeeping and the sidecar itself, so they count the same files. A sidecar that
    never recorded the numbers (older pulls) is skipped rather than reported as damaged.

    Returns ``(issues, notes)``. A model can also be short of *file entries* — that happens when the
    sidecar predates the bookkeeping exclusions (see :data:`_BOOKKEEPING_MAX_BYTES`) and never when
    a download stops partway, which shortens a file rather than removing one. When the missing bytes
    are small enough on both bounds to be exactly those entries, that is reported as a note, not an
    issue: the recorded numbers are stale, the model is not damaged.
    """
    recorded_size, recorded_files = meta.get("size_bytes"), meta.get("file_count")
    if not isinstance(recorded_size, int) or recorded_size <= 0:
        return [], []
    size, files = dir_stats(path)
    counted: int | None = recorded_files if isinstance(recorded_files, int) and recorded_files > 0 else None
    missing_files = counted - files if counted is not None else 0
    shortfall = recorded_size - size
    bookkeeping_sized = 0 <= shortfall <= missing_files * _BOOKKEEPING_MAX_BYTES
    negligible = shortfall <= _BOOKKEEPING_MAX_FRACTION * recorded_size
    if missing_files > 0 and bookkeeping_sized and negligible:
        return [], [
            f"metadata counted differently — recorded {counted} files / {_human_size(recorded_size)}, "
            f"on disk {files} / {_human_size(size)}; weights intact"
        ]
    issues: list[str] = []
    if size != recorded_size:
        issues.append(_size_mismatch(size, recorded_size))
    if counted is not None and files != counted:
        issues.append(f"{files} files != recorded {counted}")
    return issues, []


def verify(model: LibraryModel, deep: bool = False) -> VerifyResult:
    """Check a model on disk is intact.

    Ollama models are content-addressed, so their blobs are checked for existence and — with
    ``deep`` — re-hashed against their sha256 digests (true integrity). Format-bucket models
    (GGUF/MLX/safetensors) carry no per-file checksums, so they're checked structurally — the
    declared weights (and projector) exist and are non-empty, and the recorded card is present —
    and against the size and file count their sidecar recorded at pull time, which is what
    catches a pull that stopped partway.

    A recorded count that predates the bookkeeping exclusions is reported through
    :attr:`VerifyResult.notes` and leaves the model ``ok`` — see :func:`_stats_issues`.
    """
    if model.is_ollama:
        models_dir = model.library_root / "ollama"
        issues, n = ollama.verify_manifest(model.path, models_dir, deep=deep)
        return VerifyResult(
            name=model.name, ok=not issues, checked=f"blobs+sha256 ({n})" if deep else f"blobs ({n})", issues=issues
        )

    issues = _weights_issues(model)
    notes: list[str] = []
    checked = "weights+card"
    sidecar = cards.sidecar_dir(model.path)
    if (sidecar / "metadata.json").is_file():
        meta = cards.read_metadata(sidecar)
        if not meta:
            issues.append("unreadable .stabbur/metadata.json")
        else:
            card = meta.get("card")
            if isinstance(card, str) and not (model.path / card).is_file():
                issues.append(f"card {card!r} missing")
            stats, notes = _stats_issues(model.path, meta)
            issues += stats
            if stats or notes or "size_bytes" in meta:
                checked = "weights+size+card"
    return VerifyResult(name=model.name, ok=not issues, checked=checked, issues=issues, notes=notes)
