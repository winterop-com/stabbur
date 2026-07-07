"""OS-aware helpers so kodo runs cleanly on both macOS and Linux.

kodo serves on POSIX only (macOS + Linux; see :mod:`kodo.supervisor`). The two
diverge in a handful of user-facing ways — how you install the runtime binaries,
and how the CLI plays back synthesized audio — that would otherwise leak macOS
assumptions (``brew``, ``afplay``) into Linux output. Centralize those
differences here so every call site stays platform-agnostic and there's a single
place to teach kodo a new OS.
"""

import platform
import shutil
import sys
from pathlib import Path

# Runtime binaries kodo spawns / shells out to, and how to install them per OS.
# MLX is Apple-Silicon-only, so its hint is the same everywhere (an install hint
# for a runtime that only exists on the Mac).
_MLX_HINT = "Install the MLX runtimes: `uv sync --extra mlx` (Apple Silicon only)."


def is_macos() -> bool:
    """Whether the host is macOS."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Whether the host is Linux."""
    return sys.platform.startswith("linux")


def is_apple_silicon() -> bool:
    """Apple Silicon Mac — where the MLX runtimes and mlx-audio voice are relevant."""
    return is_macos() and platform.machine() == "arm64"


def os_label() -> str:
    """Short human label for the running OS/arch (e.g. ``Darwin arm64``, ``Linux x86_64``)."""
    return f"{platform.system()} {platform.machine()}"


def llama_cpp_hint() -> str:
    """OS-appropriate instructions for installing the llama.cpp binaries."""
    if is_macos():
        return "Install llama.cpp: `brew install llama.cpp`."
    return (
        "Install llama.cpp: grab a release binary from "
        "https://github.com/ggml-org/llama.cpp/releases, or build from source "
        "(some distros package it as `llama.cpp`)."
    )


def package_hint(package: str) -> str:
    """OS-appropriate instructions for installing a system package (e.g. ``ffmpeg``).

    Covers the CLI tools kodo shells out to that ship in every mainstream package
    manager under the same name (ffmpeg, espeak-ng); llama.cpp has its own hint
    (:func:`llama_cpp_hint`) since it isn't uniformly packaged on Linux.
    """
    if is_macos():
        return f"install it: `brew install {package}`"
    return f"install it via your package manager, e.g. `apt install {package}` or `dnf install {package}`"


def install_hints() -> dict[str, str]:
    """Install hints for the runtime binaries kodo spawns, keyed by executable name.

    Tailored to the running OS so ``kodo doctor`` and runtime errors never point a
    Linux user at Homebrew.
    """
    return {
        "llama-server": llama_cpp_hint(),
        "mlx_lm.server": _MLX_HINT,
        "mlx_vlm.server": _MLX_HINT,
    }


# CLI audio players to try, in preference order, per OS. macOS ships ``afplay``;
# Linux has no single guaranteed player, so probe the common ones (PulseAudio /
# PipeWire, ALSA, ffmpeg's ffplay, SoX).
_LINUX_PLAYERS = ("paplay", "aplay", "ffplay", "play")


def audio_play_command(path: Path) -> list[str] | None:
    """The argv to play ``path`` on this OS, or None if no player is installed.

    Returns a full command (binary resolved on PATH plus flags) ready to hand to
    :func:`subprocess.run`; callers decide what to do when it's None.
    """
    if is_macos():
        exe = shutil.which("afplay")
        return [exe, str(path)] if exe else None
    for name in _LINUX_PLAYERS:
        exe = shutil.which(name)
        if exe is None:
            continue
        if name == "ffplay":  # ffplay opens a window and never exits without these
            return [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        return [exe, str(path)]
    return None
