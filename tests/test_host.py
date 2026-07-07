"""Tests for the OS-aware helpers in :mod:`kodo.host`."""

from pathlib import Path

import pytest

from kodo import host


def test_os_detection_is_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.sys, "platform", "linux")
    assert host.is_linux() and not host.is_macos() and not host.is_apple_silicon()
    monkeypatch.setattr(host.sys, "platform", "darwin")
    assert host.is_macos() and not host.is_linux()


def test_apple_silicon_needs_darwin_and_arm64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.sys, "platform", "darwin")
    monkeypatch.setattr(host.platform, "machine", lambda: "arm64")
    assert host.is_apple_silicon()
    monkeypatch.setattr(host.platform, "machine", lambda: "x86_64")
    assert not host.is_apple_silicon()  # Intel Mac
    monkeypatch.setattr(host.sys, "platform", "linux")
    monkeypatch.setattr(host.platform, "machine", lambda: "arm64")
    assert not host.is_apple_silicon()  # arm64 Linux is not Apple Silicon


def test_llama_hint_is_os_appropriate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.sys, "platform", "darwin")
    assert "brew install llama.cpp" in host.llama_cpp_hint()
    monkeypatch.setattr(host.sys, "platform", "linux")
    hint = host.llama_cpp_hint()
    assert "brew" not in hint
    assert "github.com/ggml-org/llama.cpp" in hint


def test_package_hint_is_os_appropriate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.sys, "platform", "darwin")
    assert host.package_hint("ffmpeg") == "install it: `brew install ffmpeg`"
    monkeypatch.setattr(host.sys, "platform", "linux")
    linux = host.package_hint("ffmpeg")
    assert "brew" not in linux and "apt install ffmpeg" in linux


def test_install_hints_cover_the_runtime_binaries() -> None:
    hints = host.install_hints()
    assert set(hints) == {"llama-server", "mlx_lm.server", "mlx_vlm.server"}
    assert all(hints.values())


def test_audio_play_command_prefers_afplay_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.sys, "platform", "darwin")
    monkeypatch.setattr(host.shutil, "which", lambda b: f"/usr/bin/{b}" if b == "afplay" else None)
    assert host.audio_play_command(Path("/tmp/a.wav")) == ["/usr/bin/afplay", "/tmp/a.wav"]


def test_audio_play_command_probes_linux_players(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.sys, "platform", "linux")
    # Only ffplay present -> it wins, with the flags that make it exit without a window.
    monkeypatch.setattr(host.shutil, "which", lambda b: "/usr/bin/ffplay" if b == "ffplay" else None)
    cmd = host.audio_play_command(Path("/tmp/a.wav"))
    assert cmd is not None
    assert cmd[0] == "/usr/bin/ffplay" and "-autoexit" in cmd and cmd[-1] == "/tmp/a.wav"


def test_audio_play_command_none_when_no_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.sys, "platform", "linux")
    monkeypatch.setattr(host.shutil, "which", lambda _b: None)
    assert host.audio_play_command(Path("/tmp/a.wav")) is None
