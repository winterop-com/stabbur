"""User-defined model tags (a small library-level index).

Tags are *user* metadata (e.g. ``tested``, ``favorite``, ``coding``, ``broken``) —
distinct from the auto-detected ``vision``/``audio``/``tools`` capabilities. They
live *inside the library itself* (``<library_root>/.heim/tags.json``, a
``{model_name: [tags]}`` map), so they travel with the library — move the drive to
another machine and the tags come along. Each library owns its own tags.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from heim import fsatomic, locking

_FILENAME = "tags.json"
_REGISTRY_FILENAME = "tag-registry.json"
# A tag is a short lowercase slug: letters, digits, dash, underscore.
_ALLOWED = re.compile(r"[^a-z0-9_-]+")
_MAX_LEN = 32
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class TagMeta(BaseModel):
    """First-class style for a tag (separate from the ``{model: [tags]}`` assignments).

    A normalized registry keyed by tag name, so a tag's color/icon is defined once — not
    duplicated per model. All fields optional; the UI falls back to a name-derived color.
    """

    model_config = ConfigDict(frozen=True)

    color: str | None = None  # hex, e.g. "#22c55e"
    icon: str | None = None  # a short glyph/emoji
    description: str | None = None


def valid_color(color: str) -> bool:
    """Whether ``color`` is a 3- or 6-digit hex color (``#rgb`` / ``#rrggbb``)."""
    return bool(_HEX_COLOR.match(color))


def normalize(tag: str) -> str:
    """Canonicalize a tag: lowercase, trim, collapse invalid chars to dashes."""
    slug = _ALLOWED.sub("-", tag.strip().lower()).strip("-")
    return slug[:_MAX_LEN]


def _path(library_root: Path) -> Path:
    return library_root / ".heim" / _FILENAME


def load(library_root: Path) -> dict[str, list[str]]:
    """Read the whole ``{model_name: [tags]}`` map (empty if none/unreadable)."""
    try:
        data = json.loads(_path(library_root).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for name, tags in data.items():
        if isinstance(tags, list):
            cleaned = sorted({normalize(str(t)) for t in tags if normalize(str(t))})
            if cleaned:
                out[str(name)] = cleaned
    return out


def save(library_root: Path, mapping: dict[str, list[str]]) -> None:
    """Persist the map (dropping models with no tags), atomically + durably."""
    clean = {name: sorted(set(tags)) for name, tags in mapping.items() if tags}
    fsatomic.write_json(_path(library_root), clean)


def tags_for(library_root: Path, name: str) -> list[str]:
    """The tags on one model (empty list if none)."""
    return load(library_root).get(name, [])


def set_tags(library_root: Path, name: str, tags: list[str]) -> list[str]:
    """Replace a model's tags with ``tags`` (normalized, deduped). Returns them."""
    norm = sorted({normalize(t) for t in tags if normalize(t)})
    # Lock the read-modify-write so a concurrent CLI/serve tag edit can't clobber this one (A5).
    with locking.library_lock(library_root):
        mapping = load(library_root)
        if norm:
            mapping[name] = norm
        else:
            mapping.pop(name, None)
        save(library_root, mapping)
    return norm


def edit_tags(library_root: Path, name: str, add: list[str], remove: list[str]) -> list[str]:
    """Add and/or remove tags on a model. Returns the resulting tag list."""
    add_n = {normalize(t) for t in add if normalize(t)}
    remove_n = {normalize(t) for t in remove if normalize(t)}
    # Hold the lock across the whole read-modify-write (not just the save) so two concurrent
    # add/remove edits can't lose each other's change (A5).
    with locking.library_lock(library_root):
        mapping = load(library_root)
        current = sorted((set(mapping.get(name, [])) | add_n) - remove_n)
        if current:
            mapping[name] = current
        else:
            mapping.pop(name, None)
        save(library_root, mapping)
    return current


# --- tag registry (first-class color/icon per tag) -------------------------------------------


def _registry_path(library_root: Path) -> Path:
    return library_root / ".heim" / _REGISTRY_FILENAME


def load_registry(library_root: Path) -> dict[str, TagMeta]:
    """Read the ``{tag: TagMeta}`` registry for a library (empty if none/unreadable)."""
    try:
        data = json.loads(_registry_path(library_root).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, TagMeta] = {}
    for tag, meta in data.items():
        key = normalize(str(tag))
        if key and isinstance(meta, dict):
            try:
                out[key] = TagMeta.model_validate(meta)
            except ValueError:
                continue
    return out


def save_registry(library_root: Path, registry: dict[str, TagMeta]) -> None:
    """Persist the registry (dropping empty entries), atomically + durably."""
    clean = {tag: m.model_dump(exclude_none=True) for tag, m in registry.items() if m.model_dump(exclude_none=True)}
    fsatomic.write_json(_registry_path(library_root), clean)


def set_tag_meta(
    library_root: Path,
    tag: str,
    *,
    color: str | None = None,
    icon: str | None = None,
    description: str | None = None,
) -> TagMeta:
    """Merge style fields into a tag's registry entry (only provided fields change). Returns it."""
    key = normalize(tag)
    updates = {k: v for k, v in {"color": color, "icon": icon, "description": description}.items() if v is not None}
    # Lock the read-modify-write so a concurrent registry edit isn't lost (A5).
    with locking.library_lock(library_root):
        registry = load_registry(library_root)
        merged = registry.get(key, TagMeta()).model_copy(update=updates)
        registry[key] = merged
        save_registry(library_root, registry)
    return merged
