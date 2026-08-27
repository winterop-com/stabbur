"""Declaring backends: `[[backends]]`, repeatable `--upstream`, and the implicit local library.

Declaration only — which backends exist and what they are called. Nothing here loads a model,
lists one, or routes to a backend; those arrive with the later ROADMAP steps.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stabbur import cli, config
from stabbur.backends import BackendSpec
from stabbur.config import LOCAL_BACKEND_NAME, BackendDeclarationError, Settings, declared_backends

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Drop the cached settings around every test — each one writes a different config."""
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def serve_env() -> Iterator[None]:
    """Restore the env vars ``serve`` writes directly (monkeypatch never saw them set)."""
    watched = ("STABBUR_UPSTREAM", "STABBUR_BACKENDS", "STABBUR_SERVE_UI", "STABBUR_SERVE_MODEL")
    before = {key: os.environ.get(key) for key in watched}
    yield
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _settings(**kwargs: object) -> Settings:
    """Settings built straight from init args, so a test states its whole config."""
    return Settings(**kwargs)  # type: ignore[arg-type]


# --- the implicit local backend ---------------------------------------------


def test_a_configured_library_is_a_backend_on_its_own() -> None:
    # The machine's own library is a backend like any other, without being declared anywhere.
    assert declared_backends(settings=_settings(library_root=Path("/tmp/lib"))) == [
        BackendSpec(name=LOCAL_BACKEND_NAME, url=None)
    ]


def test_no_library_means_no_local_backend() -> None:
    # `serve --upstream` on a machine with no library is a supported setup (the models are all
    # remote): declaring a local backend there would promise a store that does not exist.
    specs = declared_backends(["http://msai:1234"], settings=_settings(library_root=None))
    assert specs == [BackendSpec(name="msai", url="http://msai:1234")]


def test_local_leads_the_listing() -> None:
    # Listing order, not selection priority: the one backend that needs no network reads first.
    specs = declared_backends(["http://msai:1234"], settings=_settings(library_root=Path("/tmp/lib")))
    assert [s.name for s in specs] == [LOCAL_BACKEND_NAME, "msai"]


def test_an_entry_without_a_url_renames_the_local_library() -> None:
    # url is None *means* the local library, so declaring one is how you give it a better name;
    # the implicit one must not then appear twice under two names.
    specs = declared_backends(settings=_settings(library_root=Path("/tmp/lib"), backends=[{"name": "t9"}]))
    assert specs == [BackendSpec(name="t9", url=None)]


def test_a_declaration_may_take_the_local_name() -> None:
    # An explicit declaration always wins over an implicit one — including for the name.
    specs = declared_backends(
        settings=_settings(library_root=Path("/tmp/lib"), backends=[{"name": "local", "url": "http://box:1/v1"}])
    )
    assert specs == [BackendSpec(name="local", url="http://box:1")]


# --- [[backends]] entries ----------------------------------------------------


def test_entries_are_read_in_file_order_with_urls_normalized() -> None:
    specs = declared_backends(
        settings=_settings(
            library_root=None,
            backends=[{"name": "msai", "url": "http://msai:1234/v1"}, {"name": "gpu", "url": "http://gpu:8080/"}],
        )
    )
    assert specs == [
        BackendSpec(name="msai", url="http://msai:1234"),
        BackendSpec(name="gpu", url="http://gpu:8080"),
    ]


def test_a_project_toml_declares_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole point of `[[backends]]` in stabbur.toml: it is committed with the project, so
    # everyone who checks it out gets the same named backends.
    (tmp_path / "stabbur.toml").write_text('[[backends]]\nname = "msai"\nurl = "http://msai:1234/v1"\n')
    monkeypatch.chdir(tmp_path)
    config.get_settings.cache_clear()
    assert declared_backends() == [
        BackendSpec(name=LOCAL_BACKEND_NAME, url=None),
        BackendSpec(name="msai", url="http://msai:1234"),
    ]


def test_the_machine_config_declares_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A remote that belongs to the machine, not to any project.
    cfg = tmp_path / "stabbur"
    cfg.mkdir()
    (cfg / "config.toml").write_text('[[backends]]\nname = "workstation"\nurl = "http://ws:8080"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # no stabbur.toml here
    config.get_settings.cache_clear()
    assert [s.name for s in declared_backends()] == [LOCAL_BACKEND_NAME, "workstation"]


def test_a_project_replaces_the_machine_list_rather_than_adding_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One field, the ordinary Settings layering: the higher source wins whole. Same rule as
    # `libraries`, so there is one thing to learn, not two.
    cfg = tmp_path / "stabbur"
    cfg.mkdir()
    (cfg / "config.toml").write_text('[[backends]]\nname = "workstation"\nurl = "http://ws:8080"\n')
    project = tmp_path / "proj"
    project.mkdir()
    (project / "stabbur.toml").write_text('[[backends]]\nname = "msai"\nurl = "http://msai:1234"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.chdir(project)
    config.get_settings.cache_clear()
    assert [s.name for s in declared_backends()] == [LOCAL_BACKEND_NAME, "msai"]


# --- --upstream, and its derived names ---------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://msai:1234/v1", "msai"),
        ("http://gpu-box.lan:8080", "gpu-box"),  # the first label is what a person calls the box
        ("https://Models.Example.COM/v1", "models"),  # hosts are case-insensitive; names are not
        ("http://127.0.0.1:9999", "127.0.0.1"),  # an IP keeps every digit: "127" names nothing
        ("http://[::1]:9999/v1", "::1"),
        ("msai:1234", "msai"),  # a bare host:port, which urlparse would read as a scheme
    ],
)
def test_upstream_names_come_from_the_host(url: str, expected: str) -> None:
    specs = declared_backends([url], settings=_settings(library_root=None))
    assert [s.name for s in specs] == [expected]


def test_the_same_place_written_two_ways_is_one_backend() -> None:
    # Normalized before comparison, or `--upstream http://x` and `--upstream http://x/v1` would
    # be two backends fighting over one derived name.
    specs = declared_backends(["http://msai:1234", "http://msai:1234/v1/"], settings=_settings(library_root=None))
    assert specs == [BackendSpec(name="msai", url="http://msai:1234")]


def test_an_already_declared_url_keeps_its_declared_name() -> None:
    # Naming a host the project already declares is a no-op, not a collision: the configured
    # name wins and the flag adds nothing.
    specs = declared_backends(
        ["http://msai:1234/v1"],
        settings=_settings(library_root=None, backends=[{"name": "the-box", "url": "http://msai:1234"}]),
    )
    assert specs == [BackendSpec(name="the-box", url="http://msai:1234")]


def test_two_ports_on_one_host_collide_loudly() -> None:
    # Both derive "msai". Never a silent pick, and never a silent drop: the message has to say
    # which two places clashed and how to fix it.
    with pytest.raises(BackendDeclarationError) as exc:
        declared_backends(["http://msai:1234", "http://msai:5678"], settings=_settings(library_root=None))
    assert "http://msai:1234" in str(exc.value)
    assert "http://msai:5678" in str(exc.value)
    assert "[[backends]]" in str(exc.value)


def test_a_flag_name_may_collide_with_a_declared_one() -> None:
    with pytest.raises(BackendDeclarationError, match="two backends are named 'msai'"):
        declared_backends(
            ["http://msai:5678"],
            settings=_settings(library_root=None, backends=[{"name": "msai", "url": "http://msai:1234"}]),
        )


def test_a_url_with_no_host_is_rejected() -> None:
    with pytest.raises(BackendDeclarationError, match="could not derive a backend name"):
        declared_backends(["http:///v1"], settings=_settings(library_root=None))


# --- back-compat: the single upstream switch ---------------------------------


def test_the_legacy_upstream_setting_declares_one_backend() -> None:
    # STABBUR_UPSTREAM keeps working untouched, and now also has a name.
    specs = declared_backends(settings=_settings(library_root=None, upstream="http://msai:1234"))
    assert specs == [BackendSpec(name="msai", url="http://msai:1234")]


def test_a_flag_replaces_the_upstream_setting() -> None:
    # Two spellings of one switch (command line and environment), not two backends — the CLI wins.
    specs = declared_backends(["http://gpu:8080"], settings=_settings(library_root=None, upstream="http://msai:1234"))
    assert specs == [BackendSpec(name="gpu", url="http://gpu:8080")]


# --- malformed declarations --------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("http://msai:1234", "is not a table"),  # the list-of-strings people will try first
        ({"url": "http://msai:1234"}, "has no name"),
        ({"name": "", "url": "http://msai:1234"}, "has no name"),
        ({"name": "msai", "ur1": "http://msai:1234"}, "unknown key"),
        ({"name": "msai", "url": 8080}, "unusable url"),
        ({"name": "ms@i", "url": "http://msai:1234"}, "may not contain"),
        ({"name": "my box", "url": "http://msai:1234"}, "may not contain"),
    ],
)
def test_a_malformed_entry_is_reported_readably(entry: object, message: str) -> None:
    # These come from a hand-edited file, so the message is the whole product.
    with pytest.raises(BackendDeclarationError, match=message):
        declared_backends(settings=_settings(library_root=None, backends=[entry]))


def test_a_malformed_entry_does_not_break_every_command() -> None:
    # Validation happens where backends are used, not at Settings(): a typo in stabbur.toml must
    # not stop `stabbur library ls` from running.
    assert _settings(backends=["nonsense"]).backends == ["nonsense"]


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(BackendDeclarationError, match="two backends are named 'msai'"):
        declared_backends(
            settings=_settings(
                library_root=None,
                backends=[{"name": "msai", "url": "http://a:1"}, {"name": "msai", "url": "http://b:2"}],
            )
        )


# --- serve --upstream --------------------------------------------------------


@pytest.fixture
def _no_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let ``serve`` run its whole pre-flight and banner without binding a port."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr("stabbur.cli.serve.project.load", lambda *a, **k: None)
    monkeypatch.setattr("stabbur.cli.serve._port_free", lambda host, port: True)


@pytest.mark.usefixtures("_no_uvicorn", "serve_env")
def test_one_upstream_serves_exactly_as_before() -> None:
    # The back-compat case: same env hand-off, same banner, no mention of backends.
    result = runner.invoke(cli.app, ["serve", "--upstream", "http://msai:1234/v1"])
    assert result.exit_code == 0, result.output
    assert os.environ["STABBUR_UPSTREAM"] == "http://msai:1234"
    assert "Upstream: http://msai:1234" in result.output
    assert "Backends:" not in result.output


@pytest.mark.usefixtures("_no_uvicorn", "serve_env")
def test_several_upstreams_are_declared_and_handed_to_the_worker() -> None:
    result = runner.invoke(
        cli.app, ["serve", "--upstream", "http://msai:1234/v1", "--upstream", "http://gpu-box.lan:8080"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(os.environ["STABBUR_BACKENDS"]) == [
        {"name": LOCAL_BACKEND_NAME, "url": None},
        {"name": "msai", "url": "http://msai:1234"},
        {"name": "gpu-box", "url": "http://gpu-box.lan:8080"},
    ]
    assert "msai" in result.output and "gpu-box" in result.output
    # Only the first is actually fronted today, and saying so is not optional.
    assert os.environ["STABBUR_UPSTREAM"] == "http://msai:1234"
    assert "Serving the first upstream only" in result.output


@pytest.mark.usefixtures("_no_uvicorn", "serve_env")
def test_serve_refuses_upstreams_it_cannot_name_apart() -> None:
    result = runner.invoke(cli.app, ["serve", "--upstream", "http://msai:1234", "--upstream", "http://msai:5678"])
    assert result.exit_code == 1, result.output
    # Rich must not eat the `[[backends]]` the message tells the user to write.
    assert "[[backends]]" in result.output


@pytest.mark.usefixtures("_no_uvicorn", "serve_env")
def test_the_exported_backends_round_trip_into_settings() -> None:
    # STABBUR_BACKENDS is the serve->worker channel (it matters under --reload, where the worker
    # is a fresh process): what serve writes must be what the worker reads back.
    runner.invoke(cli.app, ["serve", "--upstream", "http://msai:1234", "--upstream", "http://gpu:8080"])
    config.get_settings.cache_clear()
    assert declared_backends() == [
        BackendSpec(name=LOCAL_BACKEND_NAME, url=None),
        BackendSpec(name="msai", url="http://msai:1234"),
        BackendSpec(name="gpu", url="http://gpu:8080"),
    ]


# --- writing the machine config ----------------------------------------------


def test_config_set_preserves_a_declared_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `stabbur config set` rewrites the whole file; a hand-added [[backends]] section must survive
    # it, or setting an unrelated key would silently take the machine's backends away.
    from stabbur import userconfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "stabbur").mkdir()
    (tmp_path / "stabbur" / "config.toml").write_text(
        'default_model = "a"\n\n[[backends]]\nname = "msai"\nurl = "http://msai:1234"\n'
    )
    userconfig.set_value("port", 8123)
    data = userconfig.read()
    assert data["port"] == 8123
    assert data["default_model"] == "a"
    assert data["backends"] == [{"name": "msai", "url": "http://msai:1234"}]
