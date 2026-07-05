"""Regression tests for the mlx-audio voice runtime gate (REVIEW VO-H1)."""

from __future__ import annotations

import importlib.util

import pytest

from kodo.voice import runtime


def test_available_returns_false_when_mlx_audio_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # find_spec on a dotted name imports the parent, raising ModuleNotFoundError when it's absent
    # (Linux / no `voice` extra). available() must degrade to False, not propagate the crash.
    def _raise(name: str) -> None:
        raise ModuleNotFoundError("No module named 'mlx_audio'")

    monkeypatch.setattr(importlib.util, "find_spec", _raise)
    assert runtime.available() is False
