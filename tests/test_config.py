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


def _machine_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Write a machine config under an isolated XDG_CONFIG_HOME and point kodo at it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "kodo" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)


def test_machine_config_supplies_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The machine config is the durable per-machine default source (library + default model).
    _machine_config(tmp_path, monkeypatch, 'library_root = "/from/machine"\ndefault_model = "gemma"\n')
    monkeypatch.delenv("KODO_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)  # no kodo.toml/.env here

    settings = Settings()
    assert settings.library_root == Path("/from/machine")
    assert settings.default_model == "gemma"


def test_kodo_toml_overrides_machine_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A project pins its own model/library, so kodo.toml outranks the machine default.
    _machine_config(tmp_path, monkeypatch, 'library_root = "/from/machine"\n')
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_toml(proj, 'library_root = "/from/toml"\n')
    monkeypatch.chdir(proj)
    monkeypatch.delenv("KODO_LIBRARY_ROOT", raising=False)

    assert Settings().library_root == Path("/from/toml")


def test_env_overrides_machine_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _machine_config(tmp_path, monkeypatch, 'default_model = "from-machine"\n')
    monkeypatch.setenv("KODO_DEFAULT_MODEL", "from-env")
    assert Settings().default_model == "from-env"


def test_userconfig_set_value_roundtrips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kodo import userconfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = userconfig.set_value("default_model", "pub/Model")
    assert path == userconfig.config_path()
    assert userconfig.read()["default_model"] == "pub/Model"
    # A second write updates in place, keeping earlier keys.
    userconfig.set_value("library_root", "/lib")
    data = userconfig.read()
    assert data == {"default_model": "pub/Model", "library_root": "/lib"}


def test_resolve_model_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kodo import project

    _machine_config(tmp_path, monkeypatch, 'default_model = "machine-default"\n')
    monkeypatch.chdir(tmp_path)
    config.get_settings.cache_clear()
    # explicit arg wins over everything
    assert project.resolve_model("explicit", project.Project(model="proj")) == "explicit"
    # project model beats the machine default
    assert project.resolve_model(None, project.Project(model="proj")) == "proj"
    # outside a project, the machine default is the fallback
    assert project.resolve_model(None, None) == "machine-default"
    config.get_settings.cache_clear()


def test_cors_origins_accepts_plain_string_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KODO_CORS_ORIGINS", "chrome-extension://abc")
    assert Settings().cors_origins == ["chrome-extension://abc"]
    monkeypatch.setenv("KODO_CORS_ORIGINS", "a.com, b.com")
    assert Settings().cors_origins == ["a.com", "b.com"]
    monkeypatch.setenv("KODO_CORS_ORIGINS", '["x.com","y.com"]')
    assert Settings().cors_origins == ["x.com", "y.com"]
