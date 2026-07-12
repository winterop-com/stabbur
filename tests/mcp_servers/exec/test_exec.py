"""Tests for the exec tool (the sandbox is stubbed — no Docker in the gate)."""

from typing import Any

import pytest
from heim_sandbox import DockerError, RunResult

import heim.mcp_servers.exec.app as app


def test_run_python_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app,
        "run_code",
        lambda *a, **k: RunResult(stdout="42\n", stderr="", exit_code=0, timed_out=False, duration_s=0.1),
    )
    out = app.run_python(code="print(42)")
    assert out["ok"] is True
    assert out["stdout"] == "42\n"


def test_run_python_nonzero_is_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app,
        "run_code",
        lambda *a, **k: RunResult(stdout="", stderr="boom", exit_code=1, timed_out=False, duration_s=0.1),
    )
    out = app.run_python(code="raise SystemExit(1)")
    assert out["ok"] is False
    assert out["stderr"] == "boom"


def test_run_python_reports_missing_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: Any, **k: Any) -> RunResult:
        raise DockerError("docker not found on PATH")

    monkeypatch.setattr(app, "run_code", _raise)
    out = app.run_python(code="print(1)")
    assert out["ok"] is False
    assert "Docker" in out["error"]
