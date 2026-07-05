"""Tests for the sandbox library (no container runs — those need Docker + images)."""

import pytest
from kodo_sandbox import RUNTIMES, SUPPORTED_LANGUAGES, docker_available, run_code


def test_docker_available_returns_bool() -> None:
    assert isinstance(docker_available(), bool)


def test_supported_languages() -> None:
    assert set(SUPPORTED_LANGUAGES) == {"python", "rust"}
    assert RUNTIMES["python"].image == "python:3.13-slim"
    assert RUNTIMES["python"].filename == "main.py"


def test_run_code_rejects_unknown_language() -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        run_code("cobol", "print(1)")
