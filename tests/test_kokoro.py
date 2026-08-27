"""Tests for the Kokoro TTS engine helpers (pure metadata; no model download).

The concurrency tests stub the network and the engine build, so nothing here fetches the real
(~310 MB) model: they pin down the *serialization*, which is what a first-use race breaks.
"""

import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stabbur.voice import kokoro


def test_voices_are_the_full_v1_set() -> None:
    vs = kokoro.voices()
    assert len(vs) == 54
    ids = [v.id for v in vs]
    assert len(set(ids)) == 54  # all unique
    assert "af_heart" in ids and "zm_yunyang" in ids


def test_voice_metadata_derived_from_prefix() -> None:
    by_id = {v.id: v for v in kokoro.voices()}
    heart = by_id["af_heart"]
    assert (heart.name, heart.language, heart.gender) == ("Heart", "American English", "female")
    george = by_id["bm_george"]
    assert (george.name, george.language, george.gender) == ("George", "British English", "male")
    xiao = by_id["zf_xiaoxiao"]
    assert (xiao.language, xiao.gender) == ("Mandarin Chinese", "female")


def test_lang_code_maps_prefix_to_espeak() -> None:
    assert kokoro.lang_code("af_heart") == "en-us"
    assert kokoro.lang_code("bm_george") == "en-gb"
    assert kokoro.lang_code("zf_xiaoxiao") == "cmn"
    assert kokoro.lang_code("jf_alpha") == "ja"
    assert kokoro.lang_code("pf_dora") == "pt-br"


def test_available_is_bool() -> None:
    assert isinstance(kokoro.available(), bool)


def test_synthesize_rejects_unknown_voice() -> None:
    # Fails fast on a bad voice id without touching the model (when the extra is
    # installed; when it isn't, it fails earlier on availability — both are fine).
    with pytest.raises(RuntimeError):
        kokoro.synthesize("hello", "not_a_voice")


def test_synthesize_rejects_a_speed_the_engine_would_reject() -> None:
    # kokoro-onnx raises a bare ValueError outside 0.5-2.0 mid-synthesis; the range is checked
    # here so the failure is this function's documented RuntimeError, not a surprise type.
    with pytest.raises(RuntimeError):
        kokoro.synthesize("hello", "af_heart", speed=9.0)
    with pytest.raises(RuntimeError):
        kokoro.synthesize("hello", "af_heart", speed=0.3)


# ---------------------------------------------------------------------------
# First-use concurrency: the download + engine build
# ---------------------------------------------------------------------------


class _FakeStream:
    """A stand-in for an ``httpx.stream`` response, handing out ``payload`` one byte at a time.

    Yielding slowly is the point: it holds the download open long enough for a second thread
    to be inside its own download at the same time, which is the situation that used to write
    two interleaved byte streams into one shared ``.part`` file.
    """

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, size: int | None = None) -> Iterator[bytes]:
        for i in range(len(self._payload)):
            time.sleep(0.001)
            yield self._payload[i : i + 1]


def _run_together(fn: Any, count: int = 2) -> None:
    """Run ``fn(i)`` in ``count`` threads, wait for all of them, and re-surface any failure.

    A thread that raises would otherwise die quietly and leave the test passing, which is
    exactly how the shared-temp race hid (the loser raised ``FileNotFoundError`` on its rename).
    """
    errors: list[Exception] = []

    def wrapped(i: int) -> None:
        try:
            fn(i)
        except Exception as exc:  # noqa: BLE001 - re-raised as a test failure below
            errors.append(exc)

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert not errors, f"thread raised: {errors!r}"


def test_concurrent_downloads_never_interleave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two downloads racing to the same destination must each stage into their own temp file:
    # the winner's bytes land whole. A shared fixed ".part" produced an A/B-mixed (so corrupt)
    # model here — the ~310 MB version of that is a model file that never loads.
    dest = tmp_path / "model.onnx"
    payloads = {"https://example.invalid/a": b"A" * 40, "https://example.invalid/b": b"B" * 40}
    urls = list(payloads)

    def fake_stream(method: str, url: str, **kwargs: Any) -> _FakeStream:
        return _FakeStream(payloads[url])

    monkeypatch.setattr(kokoro.httpx, "stream", fake_stream)
    _run_together(lambda i: kokoro._download(urls[i], dest))

    assert dest.read_bytes() in payloads.values()  # one writer's bytes, whole and unmixed
    assert list(tmp_path.iterdir()) == [dest]  # and no staging file left behind


def test_failed_download_leaves_no_staging_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A download that dies mid-stream must clean up its temp rather than leaving a partial
    # file next to the model for the next reader to trip over.
    dest = tmp_path / "model.onnx"

    def boom(method: str, url: str, **kwargs: Any) -> _FakeStream:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(kokoro.httpx, "stream", boom)
    with pytest.raises(RuntimeError):
        kokoro._download("https://example.invalid/a", dest)
    assert list(tmp_path.iterdir()) == []


def test_ensure_assets_downloads_each_file_once_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two threads hitting a cold library must fetch the model and the voices file once each,
    # not once per thread: the check and the fetch happen under one lock.
    monkeypatch.setattr(kokoro, "_assets_dir", lambda: tmp_path)
    fetched: list[str] = []

    def fake_download(url: str, dest: Path) -> None:
        fetched.append(url)
        time.sleep(0.02)  # long enough for the other thread to reach its own presence check
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"weights")

    monkeypatch.setattr(kokoro, "_download", fake_download)
    _run_together(lambda i: kokoro.ensure_assets())

    assert len(fetched) == 2  # one model + one voices file, total, across both threads
    assert kokoro.assets_present()


def test_get_engine_builds_once_under_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    # The cached engine is built exactly once even when several worker threads ask at the same
    # moment: two builds would mean two asset fetches and two ONNX sessions in RAM.
    monkeypatch.setattr(kokoro, "_engine", None)  # restored after the test, so no stub leaks
    builds: list[int] = []
    engines: list[Any] = []

    def fake_build() -> object:
        builds.append(1)
        time.sleep(0.02)
        return object()

    monkeypatch.setattr(kokoro, "_build_engine", fake_build)
    _run_together(lambda i: engines.append(kokoro._get_engine()), count=4)

    assert len(builds) == 1
    assert len(engines) == 4
    assert all(e is engines[0] for e in engines)  # every caller got the one engine


# ---------------------------------------------------------------------------
# Temp-WAV hygiene
# ---------------------------------------------------------------------------


def test_synthesize_leaves_no_temp_when_the_engine_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Engine load (and the first-use download inside it) can fail. The output file is created
    # only after generation succeeds, so a failure here leaks nothing.
    monkeypatch.setattr(kokoro, "available", lambda: True)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def boom() -> Any:
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(kokoro, "_get_engine", boom)
    with pytest.raises(RuntimeError):
        kokoro.synthesize("hello", "af_heart")
    assert list(tmp_path.iterdir()) == []


def test_synthesize_cleans_up_its_temp_when_the_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # If writing/validating the WAV fails after the temp exists (e.g. the engine produced no
    # audio), the temp is removed — one leaked file per failed request would otherwise pile up.
    monkeypatch.setattr(kokoro, "available", lambda: True)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(kokoro, "_get_engine", lambda: SimpleNamespace(create=lambda *a, **k: (b"samples", 24_000)))

    def boom(out: Path, samples: Any, sample_rate: int) -> Path:
        raise RuntimeError("Kokoro synthesis produced no audio")

    monkeypatch.setattr(kokoro, "_write_wav", boom)
    with pytest.raises(RuntimeError):
        kokoro.synthesize("hello", "af_heart")
    assert list(tmp_path.iterdir()) == []


def test_synthesize_returns_the_written_wav(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The happy path still yields a temp WAV the caller owns (and must clean up itself).
    monkeypatch.setattr(kokoro, "available", lambda: True)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(kokoro, "_get_engine", lambda: SimpleNamespace(create=lambda *a, **k: (b"samples", 24_000)))

    def write(out: Path, samples: Any, sample_rate: int) -> Path:
        out.write_bytes(b"RIFF-fake")
        return out

    monkeypatch.setattr(kokoro, "_write_wav", write)
    result = kokoro.synthesize("hello", "af_heart")
    assert result.read_bytes() == b"RIFF-fake"
    assert list(tmp_path.iterdir()) == [result]
