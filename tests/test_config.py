"""Tests for Settings sourcing: kodo.toml is primary, env overrides it."""

from pathlib import Path

import pytest

from kodo import config
from kodo.config import Settings


def _write_toml(tmp_path: Path, body: str) -> None:
    (tmp_path / "kodo.toml").write_text(body)


def test_debug_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_debug", False)
    assert config.debug_enabled() is False
    config.set_debug(True)
    assert config.debug_enabled() is True
    config.set_debug(False)  # restore


def test_pinned_runtime_port_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # --runtime-port pins the port; unset means auto-pick (None).
    monkeypatch.setattr(config, "_runtime_port_override", None)
    config.set_runtime_port(9999)
    assert config.pinned_runtime_port() == 9999
    config.set_runtime_port(None)
    assert config._runtime_port_override is None


def test_settings_read_library_root_from_kodo_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # kodo.toml is the primary config: a top-level key maps to a Settings field.
    _write_toml(tmp_path, 'library_root = "/data/library"\n[project]\nmodel = "x"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KODO_LIBRARY_ROOT", raising=False)

    assert Settings().library_root == Path("/data/library")


def test_kodo_toml_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # kodo.toml outranks .env, so a stale .env cannot shadow the primary config.
    _write_toml(tmp_path, 'library_root = "/from/toml"\n')
    (tmp_path / ".env").write_text("KODO_LIBRARY_ROOT=/from/dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KODO_LIBRARY_ROOT", raising=False)

    assert Settings().library_root == Path("/from/toml")


def test_env_var_overrides_kodo_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real environment variable is the per-machine escape hatch and still wins.
    _write_toml(tmp_path, 'library_root = "/from/toml"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KODO_LIBRARY_ROOT", "/from/env")

    assert Settings().library_root == Path("/from/env")


def test_project_tables_do_not_break_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The [project]/[[mcp]] tables belong to kodo.project; Settings must ignore
    # them rather than error on unknown keys.
    _write_toml(
        tmp_path,
        'library_root = "/data/library"\n[project]\nmodel = "gemma"\n[[mcp]]\ncommand = "kodo-mcp-datetime"\n',
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KODO_LIBRARY_ROOT", raising=False)

    settings = Settings()
    assert settings.library_root == Path("/data/library")
