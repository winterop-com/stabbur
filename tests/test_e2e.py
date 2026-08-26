"""Live end-to-end tests: real runtime, real streaming. Opt-in via -m slow.

Skipped automatically unless ``llama-server`` is installed and the configured
library contains a GGUF chat model.
"""

import json
import shutil
import subprocess
import time

import httpx
import pytest

from stabbur import library, runtime
from stabbur.models import ModelFormat

pytestmark = pytest.mark.slow


def _smallest_gguf() -> library.LibraryModel | None:
    gens = [m for m in library.scan() if m.generative and m.model_format is ModelFormat.gguf]
    return min(gens, key=lambda m: m.size_bytes) if gens else None


@pytest.fixture(scope="module")
def model() -> library.LibraryModel:
    if shutil.which("llama-server") is None:
        pytest.skip("llama-server not installed")
    m = _smallest_gguf()
    if m is None:
        pytest.skip("no GGUF chat model in the library")
    return m


def test_one_shot_generate_returns_text(model: library.LibraryModel) -> None:
    out = runtime.generate(model, "Reply with the single word: ready", max_tokens=8)
    assert out.strip(), "empty generation"


def test_streaming_delivers_incremental_chunks(model: library.LibraryModel) -> None:
    port = 8099
    cmd = runtime.build_command(model, "127.0.0.1", port)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/v1/models", timeout=2).status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.4)
        else:
            pytest.fail("runtime never became ready")

        body = {"stream": True, "max_tokens": 32, "messages": [{"role": "user", "content": "Count to five"}]}
        deltas: list[str] = []
        done = False
        with httpx.stream("POST", f"{base}/v1/chat/completions", json=body, timeout=60) as r:
            assert "event-stream" in r.headers.get("content-type", "")
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    done = True
                    break
                content = json.loads(payload)["choices"][0]["delta"].get("content")
                if content:
                    deltas.append(content)

        # Streaming means the reply arrived as many incremental SSE delta events,
        # not one buffered blob. (Timing span is unreliable: a tiny model can
        # finish in a few ms, so we assert on chunk count, not wall-clock.)
        assert len(deltas) >= 3, f"reply came in {len(deltas)} chunk(s) — not streamed token-by-token"
        assert "".join(deltas).strip(), "empty streamed reply"
        assert done, "missing [DONE] terminator"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
