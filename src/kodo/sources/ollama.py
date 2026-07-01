"""Ollama source adapter.

Ollama stores models under ``~/.ollama/models`` as OCI-style manifests
(``manifests/<registry>/<namespace>/<model>/<tag>``) that reference content-
addressed blobs (``blobs/sha256-...``). Listing reads the manifests; backing
up copies a manifest together with the blobs it references.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

from kodo import cards
from kodo.config import get_settings
from kodo.models import ModelEntry, ModelFormat, ModelSource, PullResult

# Ollama blob digests are content-addressed: "sha256:" + the sha256 hex of the blob.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _expected_hex(digest: str, name: str) -> str:
    """Return the hex of a well-formed ``sha256:<hex>`` digest, or raise if malformed."""
    if not _DIGEST_RE.match(digest):
        raise ValueError(f"Ollama manifest for {name!r} has a malformed blob digest: {digest!r}")
    return digest.split(":", 1)[1]


def _sha256_hex(path: Path) -> str:
    """Stream the sha256 hex of a file in bounded memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def pull(name: str, library_root: Path, models_dir: Path | None = None, move: bool = False) -> PullResult:
    """Copy an Ollama model (manifest + referenced blobs) into the backup root.

    The layout under ``library_root/ollama`` mirrors Ollama's own store so it can
    be restored by copying back.

    Args:
        name: The ``model:tag`` name as reported by :func:`list_models`.
        library_root: Destination root.
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

    dest_root = library_root / ModelSource.ollama.value
    rel_manifest = manifest_path.relative_to(root)
    dest_manifest = dest_root / rel_manifest

    # A corrupt/unreadable manifest, or one referencing no blobs, is not a
    # restorable model — fail rather than record it as a successful backup (which
    # with --move would also delete the only source copy).
    try:
        json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Ollama manifest for {name!r} is unreadable ({manifest_path}): {exc}") from exc
    digests = _blob_digests(manifest_path)
    if not digests:
        raise ValueError(
            f"Ollama manifest for {name!r} references no blobs ({manifest_path}); "
            "refusing to back up a corrupt manifest"
        )

    # Validate digest format and presence BEFORE writing anything: a malformed
    # digest must be rejected before it reaches _blob_path(), and a missing blob
    # must never leave a manifest in the backup pointing at absent content.
    for digest in digests:
        _expected_hex(digest, name)
        blob = _blob_path(root, digest)
        if not blob.is_file():
            raise FileNotFoundError(
                f"Ollama blob {digest} referenced by {name!r} is missing from the store "
                f"({blob}); the source is incomplete, refusing to write a partial backup"
            )

    # Copy blobs FIRST, each atomically (temp file + rename) and content-verified,
    # so a partial or corrupt blob is never published and the manifest — the file
    # that references them — is only written after every blob is present and its
    # sha256 matches its digest. A failure mid-copy (disk full, drive unplugged)
    # thus can't leave a manifest pointing at missing/corrupt content.
    blobs_dir = dest_root / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    size_bytes = 0
    file_count = 0
    for digest in digests:
        expected = _expected_hex(digest, name)
        blob = _blob_path(root, digest)
        dest_blob = blobs_dir / blob.name
        # Content-addressed: reuse an existing dest only if its sha256 matches (size
        # alone can't catch a same-size corrupt backup); verify fresh copies too,
        # which also catches a corrupt source blob.
        if not (dest_blob.is_file() and _sha256_hex(dest_blob) == expected):
            tmp_blob = blobs_dir / f".{blob.name}.partial"
            try:
                shutil.copy2(blob, tmp_blob)
                if _sha256_hex(tmp_blob) != expected:
                    raise OSError(f"checksum mismatch for Ollama blob {digest} of {name!r} (corrupt source or copy)")
                tmp_blob.replace(dest_blob)  # atomic publish
            except OSError:
                tmp_blob.unlink(missing_ok=True)
                raise
        size_bytes += dest_blob.stat().st_size
        file_count += 1

    # Manifest last, also atomically — its presence marks the backup as complete.
    dest_manifest.parent.mkdir(parents=True, exist_ok=True)
    tmp_manifest = dest_manifest.parent / f".{dest_manifest.name}.partial"
    try:
        shutil.copy2(manifest_path, tmp_manifest)
        tmp_manifest.replace(dest_manifest)
    except OSError:
        tmp_manifest.unlink(missing_ok=True)
        raise
    size_bytes += dest_manifest.stat().st_size
    file_count += 1
    verified = True  # every blob was size-checked above

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
            "manifest": dest_manifest.relative_to(library_root).as_posix(),
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
