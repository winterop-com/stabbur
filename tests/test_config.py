"""Tests for Settings sourcing: kodo.toml is primary, env overrides it."""

from pathlib import Path

import pytest

from kodo.config import Settings


def _write_toml(tmp_path: Path, body: str) -> None:
    (tmp_path / "kodo.toml").write_text(body)


def test_settings_read_backup_root_from_kodo_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # kodo.toml is the primary config: a top-level key maps to a Settings field.
    _write_toml(tmp_path, 'backup_root = "/data/library"\n[project]\nmodel = "x"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KODO_BACKUP_ROOT", raising=False)

    assert Settings().backup_root == Path("/data/library")


def test_kodo_toml_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # kodo.toml outranks .env, so a stale .env cannot shadow the primary config.
    _write_toml(tmp_path, 'backup_root = "/from/toml"\n')
    (tmp_path / ".env").write_text("KODO_BACKUP_ROOT=/from/dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KODO_BACKUP_ROOT", raising=False)

    assert Settings().backup_root == Path("/from/toml")


def test_env_var_overrides_kodo_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real environment variable is the per-machine escape hatch and still wins.
    _write_toml(tmp_path, 'backup_root = "/from/toml"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KODO_BACKUP_ROOT", "/from/env")

    assert Settings().backup_root == Path("/from/env")


def test_project_tables_do_not_break_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The [project]/[[mcp]] tables belong to kodo.project; Settings must ignore
    # them rather than error on unknown keys.
    _write_toml(
        tmp_path,
        'backup_root = "/data/library"\n[project]\nmodel = "gemma"\n[[mcp]]\ncommand = "kodo-mcp-datetime"\n',
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KODO_BACKUP_ROOT", raising=False)

    settings = Settings()
    assert settings.backup_root == Path("/data/library")
