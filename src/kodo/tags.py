"""User-defined model tags (a small library-level index).

Tags are *user* metadata (e.g. ``tested``, ``favorite``, ``coding``, ``broken``) —
distinct from the auto-detected ``vision``/``audio``/``tools`` capabilities. They
live *inside the library itself* (``<library_root>/.kodo/tags.json``, a
``{model_name: [tags]}`` map), so they travel with the library — move the drive to
another machine and the tags come along. Each library owns its own tags.
"""

import json
import re
from pathlib import Path

_FILENAME = "tags.json"
# A tag is a short lowercase slug: letters, digits, dash, underscore.
_ALLOWED = re.compile(r"[^a-z0-9_-]+")
_MAX_LEN = 32


def normalize(tag: str) -> str:
    """Canonicalize a tag: lowercase, trim, collapse invalid chars to dashes."""
    slug = _ALLOWED.sub("-", tag.strip().lower()).strip("-")
    return slug[:_MAX_LEN]


def _path(library_root: Path) -> Path:
    return library_root / ".kodo" / _FILENAME


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
    """Persist the map (dropping models with no tags), atomically."""
    path = _path(library_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {name: sorted(set(tags)) for name, tags in mapping.items() if tags}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def tags_for(library_root: Path, name: str) -> list[str]:
    """The tags on one model (empty list if none)."""
    return load(library_root).get(name, [])


def set_tags(library_root: Path, name: str, tags: list[str]) -> list[str]:
    """Replace a model's tags with ``tags`` (normalized, deduped). Returns them."""
    mapping = load(library_root)
    norm = sorted({normalize(t) for t in tags if normalize(t)})
    if norm:
        mapping[name] = norm
    else:
        mapping.pop(name, None)
    save(library_root, mapping)
    return norm


def edit_tags(library_root: Path, name: str, add: list[str], remove: list[str]) -> list[str]:
    """Add and/or remove tags on a model. Returns the resulting tag list."""
    current = set(tags_for(library_root, name))
    current |= {normalize(t) for t in add if normalize(t)}
    current -= {normalize(t) for t in remove if normalize(t)}
    return set_tags(library_root, name, sorted(current))
