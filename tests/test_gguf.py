"""Tests for the GGUF header reader against malformed input.

The bug these cover: ``_skip_value`` looped ``range(count)`` on an array count read straight from
the file, and for a fixed-width (or unrecognised) element type it only *seeked* past the data
instead of reading it. A seek past the end succeeds silently, so nothing ever raised: a
bit-flipped uint64 count — the corruption the no-journal exFAT drive invites — span for
effectively forever. Verified against the pre-fix parser: an array of uint32 with a count of
``2**63`` never returned, and neither did one with an unknown element type (skip width zero).

Only the variable-width branches happened to escape, and only on a small file, by reading their
way into EOF; on a multi-gigabyte file they grind through it eight bytes at a time. So both the
byte-range checks and the count caps are needed, not one or the other.

The library scanner isolates a bad model by catching its exception; it has no way to interrupt a
loop, so one corrupt file froze ``library ls``, ``library.scan()`` and capability detection
instead of being skipped.

Every case here asserts the same two things: the parse returns a dict, and it *finishes*.
"""

import io
import random
import struct
import time
from pathlib import Path

import pytest

from stabbur import gguf

# A parse of a few-hundred-byte header is microseconds; a second is a hang by any measure. The
# repo has no pytest-timeout, so a bounded parser plus an elapsed-time assertion is the guard.
_BUDGET_SECONDS = 5.0

_WANTED = {"general.architecture", "llama.context_length"}

_UINT32 = 4
_BOOL = 7


def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _header(kv_count: int, *, tensor_count: int = 0) -> bytes:
    return b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", tensor_count) + struct.pack("<Q", kv_count)


def _kv_string(key: str, value: str) -> bytes:
    return _string(key) + struct.pack("<I", gguf.GGUF_STRING) + _string(value)


def _kv_uint32(key: str, value: int) -> bytes:
    return _string(key) + struct.pack("<I", _UINT32) + struct.pack("<I", value)


def _kv_bool(key: str, value: bool) -> bytes:
    return _string(key) + struct.pack("<I", _BOOL) + struct.pack("<?", value)


def _array_head(key: str, elem_type: int, count: int) -> bytes:
    return _string(key) + struct.pack("<I", gguf.GGUF_ARRAY) + struct.pack("<I", elem_type) + struct.pack("<Q", count)


def _kv_string_array(key: str, values: list[str]) -> bytes:
    return _array_head(key, gguf.GGUF_STRING, len(values)) + b"".join(_string(v) for v in values)


def _kv_uint32_array(key: str, values: list[int]) -> bytes:
    return _array_head(key, _UINT32, len(values)) + b"".join(struct.pack("<I", v) for v in values)


def _valid_gguf() -> bytes:
    """A small but realistically shaped header: skipped arrays ahead of the keys we want."""
    entries = [
        _kv_string("general.name", "test-model"),
        _kv_string_array("tokenizer.ggml.tokens", ["a", "b", "c", "d"]),  # skipped, variable width
        _kv_uint32_array("tokenizer.ggml.token_type", [1, 1, 2, 3]),  # skipped, fixed width
        _kv_bool("general.some_flag", True),
        _kv_string("general.architecture", "llama"),
        _kv_uint32("llama.context_length", 32768),
    ]
    return _header(len(entries)) + b"".join(entries) + b"\0" * 64  # padding stands in for weights


def _written(tmp_path: Path, data: bytes, name: str = "model.gguf") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _parse(tmp_path: Path, data: bytes) -> dict[str, object]:
    """Parse ``data`` as a GGUF, failing the test if the parse does not finish promptly."""
    started = time.monotonic()
    out = gguf.read_metadata(_written(tmp_path, data), _WANTED)
    elapsed = time.monotonic() - started
    assert elapsed < _BUDGET_SECONDS, f"parse took {elapsed:.1f}s - the reader is unbounded again"
    assert isinstance(out, dict)
    return out


def test_valid_gguf_still_parses(tmp_path: Path) -> None:
    assert _parse(tmp_path, _valid_gguf()) == {"general.architecture": "llama", "llama.context_length": 32768}


def test_is_projector_still_reads_the_architecture(tmp_path: Path) -> None:
    projector = _written(tmp_path, _header(1) + _kv_string("general.architecture", "clip"), "mmproj.gguf")
    assert gguf.is_projector(projector) is True
    assert gguf.is_projector(_written(tmp_path, _valid_gguf())) is False


def test_a_partial_parse_keeps_what_it_read(tmp_path: Path) -> None:
    # The contract: stop where the file went wrong, return the keys found before that point.
    truncated = (
        _header(2)
        + _kv_string("general.architecture", "llama")
        + _string("llama.context_length")
        + struct.pack("<I", _UINT32)
        + b"\x01\x02"  # 2 of the 4 bytes the value needs
    )
    assert _parse(tmp_path, truncated) == {"general.architecture": "llama"}


def _nested_arrays(levels: int) -> bytes:
    """An array of an array of an array... deeper than any real metadata nests."""
    head = _string("general.nested") + struct.pack("<I", gguf.GGUF_ARRAY)
    body = (struct.pack("<I", gguf.GGUF_ARRAY) + struct.pack("<Q", 1)) * levels
    return head + body + struct.pack("<I", _UINT32) + struct.pack("<Q", 0)


# Each case must return a dict without hanging. The names describe the corruption, not an expected
# value: a corrupt file owes us nothing beyond a prompt, possibly empty, answer.
_MALFORMED: list[tuple[str, bytes]] = [
    ("zero-byte file", b""),
    ("magic only", b"GGUF"),
    ("wrong magic", b"NOPE" + _valid_gguf()[4:]),
    ("truncated header", b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0)),
    ("truncated mid-key", _header(1) + struct.pack("<Q", 32) + b"general."),
    ("truncated mid-value", _header(1) + _string("general.architecture") + struct.pack("<I", gguf.GGUF_STRING)),
    ("huge kv count", _valid_gguf()[:16] + struct.pack("<Q", 2**64 - 1) + _valid_gguf()[24:]),
    (
        # The hang: 2**60 iterations of a seek that could never fail.
        "huge scalar-array count",
        _header(1) + _array_head("tokenizer.ggml.token_type", _UINT32, 2**60) + struct.pack("<I", 1),
    ),
    (
        # The same hang with a skip width of zero, so the position never even moved.
        "huge array count of an unknown element type",
        _header(1) + _array_head("general.junk", 99, 2**60) + b"\0" * 8,
    ),
    (
        "huge string-array count",
        _header(1) + _array_head("tokenizer.ggml.tokens", gguf.GGUF_STRING, 2**63) + _string("a"),
    ),
    (
        "array count under the cap but past the end",
        _header(1) + _array_head("tokenizer.ggml.tokens", gguf.GGUF_STRING, 1_000_000) + _string("a"),
    ),
    (
        "huge length on a skipped string",
        _header(2)
        + _string("general.name")
        + struct.pack("<I", gguf.GGUF_STRING)
        + struct.pack("<Q", 2**62)
        + b"x" * 8,
    ),
    (
        "huge length on a wanted string",
        _header(1)
        + _string("general.architecture")
        + struct.pack("<I", gguf.GGUF_STRING)
        + struct.pack("<Q", 2**62)
        + b"llama",
    ),
    ("huge key length", _header(1) + struct.pack("<Q", 2**61) + b"general.architecture"),
    (
        "unknown value type",
        _header(2)
        + _string("general.mystery")
        + struct.pack("<I", 99)
        + b"\0" * 8
        + _kv_string("general.architecture", "llama"),
    ),
    ("deeply nested arrays", _header(1) + _nested_arrays(40)),
    (
        "huge tensor and kv counts",
        b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 2**64 - 1) + struct.pack("<Q", 2**64 - 1),
    ),
]


@pytest.mark.parametrize("data", [d for _, d in _MALFORMED], ids=[n for n, _ in _MALFORMED])
def test_malformed_gguf_returns_a_dict_without_hanging(tmp_path: Path, data: bytes) -> None:
    _parse(tmp_path, data)


def test_every_truncation_of_a_valid_file_is_survivable(tmp_path: Path) -> None:
    # Fuzz-shaped: an interrupted copy leaves a prefix. Every prefix parses or gives up, fast.
    full = _valid_gguf()
    started = time.monotonic()
    for cut in range(len(full)):
        assert isinstance(gguf.read_metadata(_written(tmp_path, full[:cut], "fuzz.gguf"), _WANTED), dict)
    assert time.monotonic() - started < _BUDGET_SECONDS


def test_single_byte_corruption_is_survivable(tmp_path: Path) -> None:
    # Fuzz-shaped: flip one byte at a time (seeded, so a failure reproduces). A flip inside a
    # length or a count is exactly the bit-rot case, and must not become an unbounded loop.
    full = _valid_gguf()
    rng = random.Random(20260828)
    started = time.monotonic()
    for _ in range(400):
        mutated = bytearray(full)
        mutated[rng.randrange(len(mutated))] = rng.randrange(256)
        assert isinstance(gguf.read_metadata(_written(tmp_path, bytes(mutated), "fuzz.gguf"), _WANTED), dict)
    assert time.monotonic() - started < _BUDGET_SECONDS


def test_a_capped_string_read_stays_aligned(monkeypatch: pytest.MonkeyPatch) -> None:
    # A string longer than the read cap is still stepped over in full, so the next key is found at
    # the right offset rather than somewhere inside the previous value.
    monkeypatch.setattr(gguf, "_MAX_GGUF_STRING", 4)
    data = _string("abcdefgh") + b"tail"
    fh = io.BytesIO(data)
    assert gguf._read_string(fh, len(data)) == "abcd"
    assert fh.read() == b"tail"


def test_seek_past_the_end_is_refused() -> None:
    fh = io.BytesIO(b"0123456789")
    gguf._advance(fh, 10, 10)  # exactly to the end is fine
    fh.seek(0)
    with pytest.raises(ValueError, match="past end of file"):
        gguf._advance(fh, 11, 10)
