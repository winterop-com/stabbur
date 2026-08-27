"""Minimal GGUF header reader.

The KV metadata block sits at the start of a GGUF file, so everything here reads a small prefix
rather than the weights. Kept as its own low-level module (no library/model imports) so both
capability detection and the library scanner can use it without a cycle.

Every reader is failure-tolerant by design: a truncated or corrupt file yields whatever was read
before the error rather than raising. These values are hints, and a model that cannot be parsed
must still be listable.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

# GGUF metadata value type codes (ggml spec).
GGUF_STRING = 8
GGUF_ARRAY = 9
# Fixed-width scalar type → struct size in bytes.
GGUF_SCALAR_SIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

# Cap a single GGUF metadata string read: chat templates are large but bounded, so a bit-flipped
# uint64 length (corruption on the no-journal exFAT drive) can't slurp gigabytes into RAM. A
# misaligned read past this just yields garbage the (guarded) capability parse discards.
_MAX_GGUF_STRING = 16 * 1024 * 1024

# What `general.architecture` reads on a multimodal projector rather than model weights.
_PROJECTOR_ARCH = "clip"


def _read_string(fh: Any) -> str:
    """Read a GGUF string (uint64 length + UTF-8 bytes) from an open file (length-capped)."""
    (length,) = struct.unpack("<Q", fh.read(8))
    return bytes(fh.read(min(length, _MAX_GGUF_STRING))).decode("utf-8", errors="replace")


def _skip_value(fh: Any, vtype: int) -> None:
    """Advance past a GGUF value of ``vtype`` we don't need to keep."""
    if vtype == GGUF_STRING:
        (length,) = struct.unpack("<Q", fh.read(8))
        fh.seek(length, 1)
    elif vtype == GGUF_ARRAY:
        (elem_type,) = struct.unpack("<I", fh.read(4))
        (count,) = struct.unpack("<Q", fh.read(8))
        for _ in range(count):
            _skip_value(fh, elem_type)
    else:
        fh.seek(GGUF_SCALAR_SIZE.get(vtype, 0), 1)


def _read_scalar(fh: Any, vtype: int) -> Any:
    """Read a fixed-width GGUF scalar value of ``vtype``."""
    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    code = fmt.get(vtype)
    if code is None:
        return None
    size = GGUF_SCALAR_SIZE[vtype]
    (value,) = struct.unpack(code, fh.read(size))
    return value


def read_metadata(path: Path, wanted: set[str]) -> dict[str, Any]:
    """Read requested scalar/string metadata keys from a GGUF file.

    Only scalar and string values are captured (arrays are skipped); parsing stops once every
    wanted key is found. Returns ``{}`` on any read/format error.
    """
    out: dict[str, Any] = {}
    try:
        with path.open("rb") as fh:
            if fh.read(4) != b"GGUF":
                return {}
            struct.unpack("<I", fh.read(4))  # version
            struct.unpack("<Q", fh.read(8))  # tensor count
            (kv_count,) = struct.unpack("<Q", fh.read(8))
            for _ in range(kv_count):
                key = _read_string(fh)
                (vtype,) = struct.unpack("<I", fh.read(4))
                if key in wanted and vtype == GGUF_STRING:
                    out[key] = _read_string(fh)
                elif key in wanted and vtype in GGUF_SCALAR_SIZE:
                    out[key] = _read_scalar(fh, vtype)
                else:
                    _skip_value(fh, vtype)
                if wanted <= out.keys():
                    break
    except (OSError, struct.error, ValueError):
        return out
    return out


def is_projector(path: Path) -> bool:
    """Whether a GGUF file is a multimodal projector rather than model weights.

    Asks the file, because filenames do not answer this reliably. The convention is a name
    starting with ``mmproj``, but real repos also ship ``<model>-mmproj-f16.gguf`` and other
    orderings; treating those as weights leaves a multimodal model with no ``--mmproj``, so it
    silently loses vision/audio — and the projector can even be picked as the model itself.

    ``general.architecture`` is the first or second key in practice, so this costs a few hundred
    bytes rather than a full parse.
    """
    return read_metadata(path, {"general.architecture"}).get("general.architecture") == _PROJECTOR_ARCH
