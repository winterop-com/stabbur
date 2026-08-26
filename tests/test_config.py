"""Tests for Settings sourcing: stabbur.toml is primary, env overrides it."""

from pathlib import Path

import pytest

from stabbur import config
from stabbur.config import Settings


def _write_toml(tmp_path: Path, body: str) -> None:
    (tmp_path / "stabbur.toml").write_text(body)


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


def test_settings_read_library_root_from_heim_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # stabbur.toml is the primary config: a top-level key maps to a Settings field.
    _write_toml(tmp_path, 'library_root = "/data/library"\n[project]\nmodel = "x"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)

    assert Settings().library_root == Path("/data/library")


def test_heim_toml_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # stabbur.toml outranks .env, so a stale .env cannot shadow the primary config.
    _write_toml(tmp_path, 'library_root = "/from/toml"\n')
    (tmp_path / ".env").write_text("STABBUR_LIBRARY_ROOT=/from/dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)

    assert Settings().library_root == Path("/from/toml")


def test_env_var_overrides_heim_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real environment variable is the per-machine escape hatch and still wins.
    _write_toml(tmp_path, 'library_root = "/from/toml"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STABBUR_LIBRARY_ROOT", "/from/env")

    assert Settings().library_root == Path("/from/env")


def test_project_tables_do_not_break_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The [project]/[[mcp]] tables belong to stabbur.project; Settings must ignore
    # them rather than error on unknown keys.
    _write_toml(
        tmp_path,
        'library_root = "/data/library"\n[project]\nmodel = "gemma"\n[[mcp]]\ncommand = "stabbur-mcp-datetime"\n',
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)

    settings = Settings()
    assert settings.library_root == Path("/data/library")


def test_assistants_array_does_not_break_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The [[assistants]] array-of-tables belongs to stabbur.project; Settings must ignore it (extra="ignore")
    # rather than error on the unknown top-level key, just like [project]/[[mcp]].
    _write_toml(
        tmp_path,
        'library_root = "/data/library"\n[project]\nmodel = "m"\n'
        '[[assistants]]\nname = "play42"\nbase_url = "https://demo/dev-2-42"\nmcp_servers = ["play42"]\n'
        '[[assistants]]\nname = "staging"\nbase_url = "https://demo/staging"\n',
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)

    assert Settings().library_root == Path("/data/library")


def _machine_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Write a machine config under an isolated XDG_CONFIG_HOME and point stabbur at it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "stabbur" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)


def test_machine_config_supplies_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The machine config is the durable per-machine default source (library + default model).
    _machine_config(tmp_path, monkeypatch, 'library_root = "/from/machine"\ndefault_model = "gemma"\n')
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)  # no stabbur.toml/.env here

    settings = Settings()
    assert settings.library_root == Path("/from/machine")
    assert settings.default_model == "gemma"


def test_heim_toml_overrides_machine_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A project pins its own model/library, so stabbur.toml outranks the machine default.
    _machine_config(tmp_path, monkeypatch, 'library_root = "/from/machine"\n')
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_toml(proj, 'library_root = "/from/toml"\n')
    monkeypatch.chdir(proj)
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)

    assert Settings().library_root == Path("/from/toml")


def test_env_overrides_machine_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _machine_config(tmp_path, monkeypatch, 'default_model = "from-machine"\n')
    monkeypatch.setenv("STABBUR_DEFAULT_MODEL", "from-env")
    assert Settings().default_model == "from-env"


def test_userconfig_set_value_roundtrips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import userconfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = userconfig.set_value("default_model", "pub/Model")
    assert path == userconfig.config_path()
    assert userconfig.read()["default_model"] == "pub/Model"
    # A second write updates in place, keeping earlier keys.
    userconfig.set_value("library_root", "/lib")
    data = userconfig.read()
    assert data == {"default_model": "pub/Model", "library_root": "/lib"}


def test_resolve_model_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import project

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
    monkeypatch.setenv("STABBUR_CORS_ORIGINS", "chrome-extension://abc")
    assert Settings().cors_origins == ["chrome-extension://abc"]
    monkeypatch.setenv("STABBUR_CORS_ORIGINS", "a.com, b.com")
    assert Settings().cors_origins == ["a.com", "b.com"]
    monkeypatch.setenv("STABBUR_CORS_ORIGINS", '["x.com","y.com"]')
    assert Settings().cors_origins == ["x.com", "y.com"]


def test_frontend_dir_prefers_the_spa_packaged_inside_the_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The regression this guards: 0.6.0-0.6.2 shipped wheels with no SPA, so `serve --ui`
    # answered 404 at `/` for every PyPI install while every checkout worked — because the
    # default pointed only at `frontend/dist` beside the source tree, which an installed
    # package does not have. The packaged copy must win when it exists.
    pkg = tmp_path / "stabbur"
    webui = pkg / "webui"
    webui.mkdir(parents=True)
    (webui / "index.html").write_text("<div id='root'></div>")
    monkeypatch.setattr(config, "__file__", str(pkg / "config.py"))
    assert config._default_frontend_dir() == webui


def test_frontend_dir_falls_back_to_the_checkout_when_not_packaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A checkout has no packaged webui/ (it is a build artifact), and must keep using the live
    # Vite output so editing the SPA does not require re-packaging it.
    pkg = tmp_path / "repo" / "src" / "stabbur"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(config, "__file__", str(pkg / "config.py"))
    assert config._default_frontend_dir() == tmp_path / "repo" / "frontend" / "dist"


def test_frontend_dir_ignores_a_packaged_dir_with_no_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty or half-staged webui/ is worse than none: mounting it would serve a directory
    # that cannot answer `/`. Presence of the directory is not the test — index.html is.
    pkg = tmp_path / "stabbur"
    (pkg / "webui").mkdir(parents=True)
    monkeypatch.setattr(config, "__file__", str(pkg / "config.py"))
    assert config._default_frontend_dir() == tmp_path.parent / "frontend" / "dist"
