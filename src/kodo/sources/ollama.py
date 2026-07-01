"""Ollama source adapter.

Ollama stores models under ``~/.ollama/models`` as OCI-style manifests
(``manifests/<registry>/<namespace>/<model>/<tag>``) that reference content-
addressed blobs (``blobs/sha256-...``). Listing reads the manifests; backing
up copies a manifest together with the blobs it references.
"""

import json
import shutil
from pathlib import Path

from kodo import cards
from kodo.config import get_settings
from kodo.models import ModelEntry, ModelFormat, ModelSource, PullResult

# Text layers worth extracting as instructions, in display order.
_CARD_LAYERS = {
    "application/vnd.ollama.image.system": "System prompt",
    "application/vnd.ollama.image.template": "Template",
    "application/vnd.ollama.image.params": "Parameters",
    "application/vnd.ollama.image.license": "License",
}


def _manifest_name(manifest_path: Path, manifests_root: Path) -> str:
    """Derive a ``model:tag`` name from a manifest's path."""
    rel = manifest_path.relative_to(manifests_root)
    # rel == <registry>/<namespace>/<model>/<tag>
    parts = rel.parts
    if len(parts) < 4:
        return rel.as_posix()
    namespace, model, tag = parts[-3], parts[-2], parts[-1]
    base = model if namespace == "library" else f"{namespace}/{model}"
    return f"{base}:{tag}"


def _blob_digests(manifest_path: Path) -> list[str]:
    """Return the blob digests referenced by a manifest (config + layers)."""
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    digests: list[str] = []
    config = data.get("config")
    if isinstance(config, dict) and "digest" in config:
        digests.append(str(config["digest"]))
    for layer in data.get("layers", []):
        if isinstance(layer, dict) and "digest" in layer:
            digests.append(str(layer["digest"]))
    return digests


def _blob_path(models_dir: Path, digest: str) -> Path:
    """Map a ``sha256:...`` digest to its on-disk blob path."""
    return models_dir / "blobs" / digest.replace(":", "-")


def _safe_name(name: str) -> str:
    """Make a ``model:tag`` name safe to use as a directory name."""
    return name.replace(":", "_").replace("/", "_")


def weight_blobs(manifest_path: Path, models_dir: Path) -> tuple[Path | None, Path | None]:
    """Return ``(model_gguf_blob, mmproj_blob|None)`` for an Ollama manifest.

    Ollama stores GGUF weights as a content-addressed blob (the ``...image.model``
    layer); a multimodal projector, if any, is the ``...image.projector`` layer.
    Both are valid GGUF files despite lacking a ``.gguf`` extension.
    """
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return (None, None)

    model_blob: Path | None = None
    mmproj_blob: Path | None = None
    for layer in data.get("layers", []):
        if not isinstance(layer, dict):
            continue
        media = str(layer.get("mediaType", ""))
        digest = str(layer.get("digest", ""))
        if not digest:
            continue
        if media.endswith(".model"):
            model_blob = _blob_path(models_dir, digest)
        elif media.endswith(".projector"):
            mmproj_blob = _blob_path(models_dir, digest)
    return (model_blob, mmproj_blob)


def manifest_names(models_dir: Path) -> list[tuple[str, Path]]:
    """Return ``(model:tag, manifest_path)`` for every manifest in the store."""
    manifests_root = models_dir / "manifests"
    if not manifests_root.is_dir():
        return []
    return [(_manifest_name(m, manifests_root), m) for m in sorted(manifests_root.rglob("*")) if m.is_file()]


def build_card(name: str, manifest_path: Path, models_dir: Path) -> str:
    """Build a Modelfile-style model card from an Ollama manifest's text layers.

    Args:
        name: The ``model:tag`` name, used as the card heading.
        manifest_path: Path to the manifest file.
        models_dir: The Ollama models directory (to resolve blob paths).

    Returns:
        Markdown describing the model's system prompt, template, parameters, and
        license — i.e. the instructions needed to run it.
    """
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return f"# {name}\n\n_No manifest metadata available._\n"

    sections: list[str] = [f"# {name}\n"]
    for layer in data.get("layers", []):
        if not isinstance(layer, dict):
            continue
        title = _CARD_LAYERS.get(str(layer.get("mediaType")))
        if title is None:
            continue
        blob = _blob_path(models_dir, str(layer.get("digest", "")))
        if not blob.is_file():
            continue
        try:
            text = blob.read_text().strip()
        except OSError:
            continue
        sections.append(f"## {title}\n\n```\n{text}\n```\n")

    if len(sections) == 1:
        sections.append("_No text layers (system/template/params/license) found._\n")
    return "\n".join(sections)


def _find_manifest(name: str, manifests_root: Path) -> Path | None:
    """Return the manifest file whose ``model:tag`` name equals ``name``."""
    for candidate in manifests_root.rglob("*"):
        if candidate.is_file() and _manifest_name(candidate, manifests_root) == name:
            return candidate
    return None


def remove(name: str, models_dir: Path | None = None) -> None:
    """Delete a local Ollama model: its manifest and any now-orphaned blobs.

    A blob is removed only if no *other* manifest still references it, so shared
    layers (templates, params, license) belonging to other models are preserved.

    Args:
        name: The ``model:tag`` name to remove.
        models_dir: Override for the Ollama models directory.

    Raises:
        FileNotFoundError: If no manifest matches ``name``.
    """
    root = models_dir or get_settings().ollama_models_dir
    manifests_root = root / "manifests"
    manifest_path = _find_manifest(name, manifests_root)
    if manifest_path is None:
        raise FileNotFoundError(f"No Ollama manifest for {name!r}")

    own = set(_blob_digests(manifest_path))
    still_used: set[str] = set()
    for other in manifests_root.rglob("*"):
        if other.is_file() and other != manifest_path:
            still_used |= set(_blob_digests(other))

    for digest in own - still_used:
        _blob_path(root, digest).unlink(missing_ok=True)
    manifest_path.unlink()

    # Prune now-empty parent directories up to (but not including) manifests_root.
    parent = manifest_path.parent
    while parent != manifests_root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def list_models(models_dir: Path | None = None) -> list[ModelEntry]:
    """List models stored by Ollama.

    Args:
        models_dir: Override for the Ollama models directory. Defaults to the
            configured ``ollama_models_dir``.

    Returns:
        One :class:`ModelEntry` per ``model:tag`` manifest found.
    """
    root = models_dir or get_settings().ollama_models_dir
    manifests_root = root / "manifests"
    if not manifests_root.is_dir():
        return []

    entries: list[ModelEntry] = []
    for manifest_path in manifests_root.rglob("*"):
        if not manifest_path.is_file():
            continue
        size_bytes = 0
        digests = _blob_digests(manifest_path)
        for digest in digests:
            blob = _blob_path(root, digest)
            if blob.is_file():
                size_bytes += blob.stat().st_size
        entries.append(
            ModelEntry(
                source=ModelSource.ollama,
                name=_manifest_name(manifest_path, manifests_root),
                model_format=ModelFormat.gguf,
                path=manifest_path,
                size_bytes=size_bytes,
                file_count=len(digests),
            )
        )
    return entries


def pull(name: str, backup_root: Path, models_dir: Path | None = None, move: bool = False) -> PullResult:
    """Copy an Ollama model (manifest + referenced blobs) into the backup root.

    The layout under ``backup_root/ollama`` mirrors Ollama's own store so it can
    be restored by copying back.

    Args:
        name: The ``model:tag`` name as reported by :func:`list_models`.
        backup_root: Destination root.
        models_dir: Override for the Ollama models directory.
        move: If true, remove the local model (manifest + orphaned blobs) after
            verifying every referenced blob copied with a matching size.

    Returns:
        A :class:`PullResult` describing what was copied.

    Raises:
        FileNotFoundError: If no manifest matches ``name``.
    """
    root = models_dir or get_settings().ollama_models_dir
    manifests_root = root / "manifests"

    manifest_path = _find_manifest(name, manifests_root)
    if manifest_path is None:
        raise FileNotFoundError(f"No Ollama manifest for {name!r}")

    dest_root = backup_root / ModelSource.ollama.value
    rel_manifest = manifest_path.relative_to(root)
    dest_manifest = dest_root / rel_manifest
    dest_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, dest_manifest)

    size_bytes = dest_manifest.stat().st_size
    file_count = 1
    verified = True
    for digest in _blob_digests(manifest_path):
        blob = _blob_path(root, digest)
        if not blob.is_file():
            raise FileNotFoundError(
                f"Ollama blob {digest} referenced by {name!r} is missing from the store "
                f"({blob}); the source is incomplete, refusing to write a partial backup"
            )
        dest_blob = dest_root / "blobs" / blob.name
        dest_blob.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(blob, dest_blob)
        size_bytes += dest_blob.stat().st_size
        file_count += 1
        verified = verified and dest_blob.stat().st_size == blob.stat().st_size

    # The native layout above stays restorable; write a browsable sidecar with
    # the generated model card and metadata under ollama/.library/<name>/.
    sidecar_dir = dest_root / ".library" / _safe_name(name)
    card_path = cards.write_card(sidecar_dir, build_card(name, manifest_path, root))
    metadata_path = cards.write_metadata(
        sidecar_dir,
        {
            "source": ModelSource.ollama.value,
            "name": name,
            "size_bytes": size_bytes,
            "file_count": file_count,
            "manifest": dest_manifest.relative_to(backup_root).as_posix(),
        },
    )

    # Only remove the local model once every blob copied with a matching size.
    if move and verified:
        remove(name, root)

    return PullResult(
        source=ModelSource.ollama,
        name=name,
        model_format=ModelFormat.gguf,
        destination=dest_manifest,
        size_bytes=size_bytes,
        file_count=file_count,
        card_path=card_path,
        metadata_path=metadata_path,
    )
