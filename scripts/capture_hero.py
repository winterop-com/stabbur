"""Recapture the docs hero screenshot (``docs/assets/web-ui.png``).

    make hero

The shot is a DERIVED ARTIFACT of the SPA, but nothing in the build ties the two together and
nothing fails when it drifts, so it silently showed a UI that no longer existed - twice. This
script exists so refreshing it is one command rather than a procedure someone has to remember.

Two things it does that a plain screenshot of a running server does not:

* It serves the UI against a MOCK OpenAI ``/v1`` advertising a public example model, so the
  composer's model chip never leaks whatever is actually loaded on the machine taking the shot.
* It attaches the real library, so the voice controls and health state are the app's genuine
  ones rather than an empty-install subset. Only the model identity is substituted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "assets" / "web-ui.png"
# The public example the README already uses. Never a name from this machine's library.
MODEL = "gemma-4-12B-it-QAT-GGUF"
VIEWPORT = (1440, 900)
SCALE = 2  # retina; the committed shot is 2880x1800


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _MockV1(BaseHTTPRequestHandler):
    """Just enough OpenAI surface for stabbur to list a model and warm it up."""

    def _send(self, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        self._send({"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "local"}]})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "Ready."}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    def log_message(self, *args: object) -> None:  # keep the run quiet
        return


def _wait_for(url: str, timeout: float = 60.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):  # noqa: S310 - fixed localhost URL
                return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.5)
    return False


def main() -> int:
    """Serve the UI against the mock upstream, drive it to a clean empty state, and shoot it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is missing - run `uv sync --extra web` (make hero does this).", file=sys.stderr)
        return 1

    if not (REPO / "frontend" / "dist" / "index.html").is_file():
        print("frontend/dist is not built - run `make frontend` first.", file=sys.stderr)
        return 1

    library = os.environ.get("STABBUR_LIBRARY_ROOT")
    if not library:
        print("STABBUR_LIBRARY_ROOT is unset; the shot needs a real library for the voice controls.", file=sys.stderr)
        return 1

    mock_port, ui_port = _free_port(), _free_port()
    mock = HTTPServer(("127.0.0.1", mock_port), _MockV1)
    threading.Thread(target=mock.serve_forever, daemon=True).start()

    serve = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [
            shutil.which("uv") or "uv",
            "run",
            "stabbur",
            "serve",
            "--ui",
            "--upstream",
            f"http://127.0.0.1:{mock_port}/v1",
            "--port",
            str(ui_port),
        ],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for(f"http://127.0.0.1:{ui_port}/health"):
            print("stabbur serve did not come up", file=sys.stderr)
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]},
                device_scale_factor=SCALE,
                color_scheme="dark",
            )
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{ui_port}/", wait_until="networkidle")
            page.wait_for_timeout(1500)
            # Pick the model so the chip reads as a loaded model rather than "Select a model".
            page.get_by_role("button", name="Select a model").first.click()
            page.wait_for_timeout(800)
            page.get_by_text(MODEL).first.click()
            page.wait_for_timeout(2500)
            page.keyboard.press("Escape")
            # Click a dead area: otherwise the picker keeps a focus ring in the shot.
            page.mouse.click(VIEWPORT[0] - 340, VIEWPORT[1] - 200)
            page.wait_for_timeout(800)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.out))
            browser.close()
    finally:
        serve.terminate()
        try:
            serve.wait(timeout=15)
        except subprocess.TimeoutExpired:
            serve.kill()
        mock.shutdown()

    print(f"wrote {args.out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
