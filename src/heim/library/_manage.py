"""Library mutation: remove a model, plan/apply the layout migration, and verify integrity."""

import json
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from heim import cards, locking
from heim.library._model import LibraryModel, _classify_dir
from heim.models import ModelFormat
from heim.sources import ollama
from heim.sources.base import copy_verified, dir_stats


def remove(model: LibraryModel) -> tuple[int, int]:
    """Delete a library model's files from disk. Returns ``(files_removed, bytes_freed)``.

    Directory-based models (gguf/mlx/…) are removed by deleting their directory.
    Ollama models delegate to :func:`heim.sources.ollama.remove`, which preserves
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
    from heim import tags  # noqa: PLC0415 - lazy to keep import order simple
    from heim.library._scan import scan  # noqa: PLC0415 - lazy to keep import order simple

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


def verify(model: LibraryModel, deep: bool = False) -> VerifyResult:
    """Check a model on disk is intact.

    Ollama models are content-addressed, so their blobs are checked for existence and — with
    ``deep`` — re-hashed against their sha256 digests (true integrity). Format-bucket models
    (GGUF/MLX/safetensors) carry no per-file checksums, so they're checked structurally: the
    declared weights (and projector) exist and are non-empty, and the recorded card is present.
    """
    if model.is_ollama:
        models_dir = model.library_root / "ollama"
        issues, n = ollama.verify_manifest(model.path, models_dir, deep=deep)
        return VerifyResult(
            name=model.name, ok=not issues, checked=f"blobs+sha256 ({n})" if deep else f"blobs ({n})", issues=issues
        )

    issues = _weights_issues(model)
    meta_path = model.path / cards.SIDECAR_DIR / "metadata.json"
    if meta_path.is_file():
        try:
            card = json.loads(meta_path.read_text()).get("card")
            if isinstance(card, str) and not (model.path / card).is_file():
                issues.append(f"card {card!r} missing")
        except (OSError, ValueError):
            issues.append("unreadable .heim/metadata.json")
    return VerifyResult(name=model.name, ok=not issues, checked="weights+card", issues=issues)
