"""Atomic, durable file writes for stabbur's small config/index files.

One implementation of the write-temp-then-rename pattern used by every small file stabbur can't
afford to corrupt (tags, machine config, ``mcp.json``): stage into a **per-write unique** temp
file (so a concurrent CLI + ``stabbur serve`` writer can't clobber each other's staging), fsync it,
then :func:`os.replace` it over the target. The fsync *before* the rename is what makes it durable
on the no-journal exFAT drive — an unclean eject mid-write can't leave a truncated/empty file that
a reader would silently accept (e.g. reading an empty ``tags.json`` as ``{}``, losing every tag).
"""

import json
import os
import uuid
from pathlib import Path
from typing import Any


def write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically and durably (unique temp + fsync + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as pretty, key-sorted JSON (trailing newline) to ``path``, atomically."""
    write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
