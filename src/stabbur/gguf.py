"""Minimal GGUF header reader.

The KV metadata block sits at the start of a GGUF file, so everything here reads a small prefix
rather than the weights. Kept as its own low-level module (no library/model imports) so both
capability detection and the library scanner can use it without a cycle.

Every reader is failure-tolerant by design: a truncated or corrupt file yields whatever was read
before the error rather than raising. These values are hints, and a model that cannot be parsed
must still be listable.

Failure-tolerant means *bounded*, not just exception-free: every length, count and seek is checked
against the end of the file before it is used, and the loops carry sanity caps on top of that.
Nothing here may loop or allocate on a number the file itself supplies. A corrupt count that made
the parser spin is indistinguishable from a hang to the scanner, whose per-item fault isolation
catches exceptions but cannot interrupt a loop — one bad file would stall the whole listing.
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

# Smallest on-disk size of one value of each variable-width type: a string is its uint64 length,
# an array is its type code plus its uint64 count. Used to reject a count that could not possibly
# fit in the bytes that are left, before looping over it.
_MIN_VALUE_SIZE = {GGUF_STRING: 8, GGUF_ARRAY: 12}

# Cap a single GGUF metadata string read: chat templates are large but bounded, so a bit-flipped
# uint64 length (corruption on the no-journal exFAT drive) can't slurp gigabytes into RAM. A
# length past this is still honoured for *position* (we seek over the rest), so the parse stays
# aligned; the truncated text just yields garbage the (guarded) capability parse discards.
_MAX_GGUF_STRING = 16 * 1024 * 1024

# Sanity caps on the two counts the file supplies that we loop over. Real files carry tens of KV
# entries and vocab arrays in the low hundreds of thousands; these are orders of magnitude above
# anything genuine, so exceeding one means corruption rather than an unusual model. Bounding the
# byte range alone is not enough on a multi-gigabyte file, where "fits in the remaining bytes"
# still permits hundreds of millions of iterations.
_MAX_GGUF_KV = 1 << 16
_MAX_GGUF_ARRAY = 1 << 24

# How deep nested arrays may go before we call it corruption. Real metadata nests one level at
# most; without this a self-referential-looking chain of array type codes recurses until Python
# raises RecursionError, which is not the error type this module contracts to fail with.
_MAX_GGUF_DEPTH = 8

# What `general.architecture` reads on a multimodal projector rather than model weights.
_PROJECTOR_ARCH = "clip"


def _read_exact(fh: Any, size: int, end: int) -> bytes:
    """Read exactly ``size`` bytes, refusing to read past ``end`` and refusing a short read."""
    if size < 0 or size > end - fh.tell():
        raise ValueError("GGUF read past end of file")
    data = bytes(fh.read(size))
    if len(data) != size:
        raise ValueError("truncated GGUF file")
    return data


def _unpack(fh: Any, code: str, size: int, end: int) -> Any:
    """Read and unpack one fixed-width struct value, bounded by ``end``."""
    (value,) = struct.unpack(code, _read_exact(fh, size, end))
    return value


def _advance(fh: Any, count: int, end: int) -> None:
    """Seek ``count`` bytes forward, refusing to land past ``end``.

    A plain ``seek`` past the end succeeds silently and every later read returns nothing, so an
    unchecked skip turns corruption into a parse that never notices it has left the file.
    """
    target = fh.tell() + count
    if count < 0 or target > end:
        raise ValueError("GGUF seek past end of file")
    fh.seek(target)


def _read_string(fh: Any, end: int) -> str:
    """Read a GGUF string (uint64 length + UTF-8 bytes), bounded by ``end`` and length-capped."""
    length = _unpack(fh, "<Q", 8, end)
    if length > end - fh.tell():
        raise ValueError("GGUF string length past end of file")
    start = fh.tell()
    data = _read_exact(fh, min(length, _MAX_GGUF_STRING), end)
    fh.seek(start + length)  # stay aligned even when the read was capped
    return data.decode("utf-8", errors="replace")


def _skip_value(fh: Any, vtype: int, end: int, depth: int = 0) -> None:
    """Advance past a GGUF value of ``vtype`` we don't need to keep.

    Raises ``ValueError`` rather than skipping blindly when the file's own numbers don't fit the
    file: an unknown type has no known width, so there is no honest way to step over it.
    """
    if vtype == GGUF_STRING:
        _advance(fh, _unpack(fh, "<Q", 8, end), end)
        return
    if vtype != GGUF_ARRAY:
        if vtype not in GGUF_SCALAR_SIZE:
            raise ValueError(f"unknown GGUF value type {vtype}")
        _advance(fh, GGUF_SCALAR_SIZE[vtype], end)
        return
    if depth >= _MAX_GGUF_DEPTH:
        raise ValueError("GGUF arrays nested too deeply")
    elem_type = _unpack(fh, "<I", 4, end)
    count = _unpack(fh, "<Q", 8, end)
    if count > _MAX_GGUF_ARRAY:
        raise ValueError(f"implausible GGUF array count {count}")
    if elem_type in GGUF_SCALAR_SIZE:
        _advance(fh, count * GGUF_SCALAR_SIZE[elem_type], end)  # fixed width: one bounded seek
        return
    if elem_type not in _MIN_VALUE_SIZE:
        raise ValueError(f"unknown GGUF array element type {elem_type}")
    if count * _MIN_VALUE_SIZE[elem_type] > end - fh.tell():
        raise ValueError(f"GGUF array of {count} does not fit in the file")
    for _ in range(count):
        _skip_value(fh, elem_type, end, depth + 1)


def _read_scalar(fh: Any, vtype: int, end: int) -> Any:
    """Read a fixed-width GGUF scalar value of ``vtype``."""
    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    code = fmt.get(vtype)
    if code is None:
        return None
    return _unpack(fh, code, GGUF_SCALAR_SIZE[vtype], end)


def read_metadata(path: Path, wanted: set[str]) -> dict[str, Any]:
    """Read requested scalar/string metadata keys from a GGUF file.

    Only scalar and string values are captured (arrays are skipped); parsing stops once every
    wanted key is found. A truncated or corrupt file stops the parse where it went wrong and
    returns the keys read up to that point (``{}`` if that is none), never raising and never
    reading or looping beyond the end of the file.
    """
    out: dict[str, Any] = {}
    try:
        with path.open("rb") as fh:
            end = fh.seek(0, 2)  # every read below is bounded by the real size of the file
            fh.seek(0)
            if _read_exact(fh, 4, end) != b"GGUF":
                return {}
            _unpack(fh, "<I", 4, end)  # version
            _unpack(fh, "<Q", 8, end)  # tensor count
            kv_count = _unpack(fh, "<Q", 8, end)
            for _ in range(min(kv_count, _MAX_GGUF_KV)):
                key = _read_string(fh, end)
                vtype = _unpack(fh, "<I", 4, end)
                if key in wanted and vtype == GGUF_STRING:
                    out[key] = _read_string(fh, end)
                elif key in wanted and vtype in GGUF_SCALAR_SIZE:
                    out[key] = _read_scalar(fh, vtype, end)
                else:
                    _skip_value(fh, vtype, end)
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
