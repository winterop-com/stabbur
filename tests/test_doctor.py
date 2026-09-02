"""Tests for the `stabbur doctor` health checks."""

import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from stabbur import doctor, library
from stabbur.config import Settings
from stabbur.models import ModelFormat


def _settings(tmp_path: Path, *, drive: bool = True) -> Settings:
    root = tmp_path / "library" if drive else tmp_path / "missing"
    if drive:
        root.mkdir(parents=True, exist_ok=True)
    return Settings(library_root=root)


def _shared_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chdir into a project that lists @shared, with STABBUR_LIBRARY_ROOT removed from the env."""
    monkeypatch.delenv("STABBUR_LIBRARY_ROOT", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "stabbur.toml").write_text('libraries = ["library", "@shared"]\n[project]\nmodel = "x"\n')
    monkeypatch.chdir(proj)


def test_project_warns_when_shared_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shared_project(tmp_path, monkeypatch)
    settings = Settings()  # no library_root set -> @shared drops out, silently unreachable
    assert "library_root" not in settings.model_fields_set
    shared = [c for c in doctor.check_project(settings) if c.name == "Shared library (@shared)"]
    assert shared and shared[0].status is doctor.CheckStatus.warn
    assert shared[0].hint and "STABBUR_LIBRARY_ROOT" in shared[0].hint


def test_project_no_shared_warning_when_library_root_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shared_project(tmp_path, monkeypatch)
    settings = Settings(library_root=tmp_path / "lib")  # explicitly set -> @shared reachable
    assert not any(c.name == "Shared library (@shared)" for c in doctor.check_project(settings))


def test_runtimes_group_carries_the_platform_and_owns_the_binaries() -> None:
    # The OS/arch is the group's heading, not a top-level row: it exists to make "not applicable on
    # this platform" readable, and that is only ever read inside this group.
    checks = doctor.check_runtimes()
    parent, children = checks[0], checks[1:]
    assert parent.name == doctor.RUNTIMES_GROUP
    assert parent.group is None and parent.detail  # e.g. "Linux x86_64"
    assert children and all(c.group == doctor.RUNTIMES_GROUP for c in children)
    assert "llama.cpp (GGUF)" in {c.name for c in children}


def test_report_status_rolls_up_worst() -> None:
    ok = doctor.Check(name="a", status=doctor.CheckStatus.ok, detail="")
    warn = doctor.Check(name="b", status=doctor.CheckStatus.warn, detail="")
    fail = doctor.Check(name="c", status=doctor.CheckStatus.fail, detail="")
    assert doctor.DoctorReport(checks=[ok]).status is doctor.CheckStatus.ok
    assert doctor.DoctorReport(checks=[ok, warn]).status is doctor.CheckStatus.warn
    assert doctor.DoctorReport(checks=[ok, warn, fail]).status is doctor.CheckStatus.fail


def test_runtime_check_missing_required_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.runtime, "resolve_binary", lambda _b: None)
    c = doctor._runtime_check("GGUF", "llama-server", required=True)
    assert c.status is doctor.CheckStatus.fail
    assert c.hint  # carries an install hint


def test_runtime_check_missing_optional_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.runtime, "resolve_binary", lambda _b: None)
    assert doctor._runtime_check("MLX", "mlx_lm.server", required=False).status is doctor.CheckStatus.warn


def test_runtime_check_not_relevant_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    # MLX off Apple Silicon: missing binary is fine (N/A), not a warning.
    monkeypatch.setattr(doctor.runtime, "resolve_binary", lambda _b: None)
    c = doctor._runtime_check("MLX", "mlx_lm.server", required=False, relevant=False)
    assert c.status is doctor.CheckStatus.ok


def test_runtime_check_present_reports_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.runtime, "resolve_binary", lambda _b: "/usr/bin/llama-server")
    c = doctor._runtime_check("GGUF", "llama-server", required=True)
    assert c.status is doctor.CheckStatus.ok
    assert c.detail == "/usr/bin/llama-server"


def test_check_library_offline_drive_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "scan", lambda: [])
    checks = doctor.check_library(_settings(tmp_path, drive=False))
    root = next(c for c in checks if c.name == doctor.LIBRARY_GROUP)
    models = next(c for c in checks if c.name == "Runnable models")
    assert root.status is doctor.CheckStatus.warn  # not mounted
    assert root.group is None and models.group == doctor.LIBRARY_GROUP  # what's inside nests
    assert models.status is doctor.CheckStatus.warn  # empty


def _library_model(tmp_path: Path, name: str, fmt: ModelFormat) -> library.LibraryModel:
    return library.LibraryModel(name=name, model_format=fmt, path=tmp_path, load_target=tmp_path)


def _installed(monkeypatch: pytest.MonkeyPatch, *binaries: str) -> None:
    """Pretend exactly ``binaries`` are on this machine (the doctor's view of the runtimes)."""
    monkeypatch.setattr(doctor.runtime, "resolve_binary", lambda b: f"/usr/bin/{b}" if b in binaries else None)


def test_check_library_counts_by_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _model(name: str, fmt: ModelFormat) -> library.LibraryModel:
        return _library_model(tmp_path, name, fmt)

    _installed(monkeypatch, "llama-server", "mlx_lm.server", "mlx_vlm.server")
    monkeypatch.setattr(
        library,
        "scan",
        lambda: [_model("a", ModelFormat.gguf), _model("b", ModelFormat.gguf), _model("c", ModelFormat.mlx)],
    )
    models = next(c for c in doctor.check_library(_settings(tmp_path)) if c.name == "Runnable models")
    assert models.status is doctor.CheckStatus.ok
    assert "2 gguf" in models.detail and "1 mlx" in models.detail


def test_runnable_models_excludes_formats_whose_runtime_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model stabbur cannot start is not a runnable model.

    An MLX-only library on a machine with no mlx_lm.server used to report "1 (1 mlx)" and exit 0,
    and `stabbur chat` then died on "'mlx_lm.server' not found on PATH" — the pre-flight passing a
    failure it was there to catch.
    """
    _installed(monkeypatch, "llama-server")  # MLX runtimes absent
    monkeypatch.setattr(library, "scan", lambda: [_library_model(tmp_path, "a", ModelFormat.mlx)])
    models = next(c for c in doctor.check_library(_settings(tmp_path)) if c.name == "Runnable models")
    assert models.status is doctor.CheckStatus.warn
    assert models.detail.startswith("0 of 1")
    assert models.hint and "mlx_lm.server" in models.hint


def test_runnable_models_counts_the_ones_that_can_run_and_flags_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed(monkeypatch, "llama-server")
    monkeypatch.setattr(
        library,
        "scan",
        lambda: [_library_model(tmp_path, "a", ModelFormat.gguf), _library_model(tmp_path, "b", ModelFormat.mlx)],
    )
    models = next(c for c in doctor.check_library(_settings(tmp_path)) if c.name == "Runnable models")
    assert models.status is doctor.CheckStatus.warn  # not everything in the library can run here
    assert models.detail == "1 of 2 (1 gguf)"


def test_mlx_rows_are_quiet_when_no_mlx_models_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A library with no MLX models does not need the MLX runtimes.

    Warning about them there is a chore invented for a format the user does not use — most
    visibly inside a project, whose environment holds only what its own models require.
    """
    _installed(monkeypatch, "llama-server")
    checks = {c.name: c for c in doctor.check_runtimes(mlx_needed=False)}
    assert checks["MLX text (mlx-lm)"].status is doctor.CheckStatus.ok
    assert "not needed" in checks["MLX text (mlx-lm)"].detail
    assert checks["MLX text (mlx-lm)"].hint is None  # no install instructions for a runtime nothing wants


def test_mlx_rows_warn_once_an_mlx_model_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # The other half: with an MLX model in the library the runtime really is missing, and a model
    # that cannot start is what the pre-flight exists to catch.
    _installed(monkeypatch, "llama-server")
    checks = {c.name: c for c in doctor.check_runtimes(mlx_needed=True)}
    assert checks["MLX text (mlx-lm)"].status is doctor.CheckStatus.warn
    assert checks["MLX text (mlx-lm)"].hint  # ...and says how to install it


def test_upstream_makes_the_local_runtimes_not_applicable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Under `serve --upstream` stabbur spawns nothing here, so a missing local binary is a fact
    # about a machine that isn't running the model. Warning about it sends people to install
    # runtimes they will never use, and buries the row that matters (is the remote reachable).
    _installed(monkeypatch)  # nothing installed locally
    checks = {c.name: c for c in doctor.check_runtimes(upstream=True)}
    assert all(c.status is doctor.CheckStatus.ok for c in checks.values())
    assert "upstream" in checks[doctor.RUNTIMES_GROUP].detail
    assert checks["llama.cpp (GGUF)"].hint is None  # no install hint for a runtime that won't run


def test_without_an_upstream_a_missing_required_runtime_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    checks = {c.name: c for c in doctor.check_runtimes()}
    assert checks["llama.cpp (GGUF)"].status is doctor.CheckStatus.fail


def test_upstream_library_count_does_not_partition_by_local_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The models on the drive are inventory when something else runs them: "0 of 1" and an install
    # hint describe a limit that does not apply.
    _installed(monkeypatch, "llama-server")
    monkeypatch.setattr(library, "scan", lambda: [_library_model(tmp_path, "a", ModelFormat.mlx)])
    settings = Settings(library_root=tmp_path / "library", upstream="http://gpu-box:1234/v1")
    (tmp_path / "library").mkdir(exist_ok=True)
    models = next(c for c in doctor.check_library(settings) if c.name == "Runnable models")
    assert models.status is doctor.CheckStatus.ok
    assert models.detail == "1 (1 mlx)"
    assert models.hint is None


def test_default_model_warns_when_its_runtime_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Present in the library is not the same as startable; the row must mirror what chat will say."""
    monkeypatch.setattr(doctor.project_ops, "load", lambda: doctor.project_ops.Project(model="pub/Some-MLX"))
    monkeypatch.setattr(library, "find", lambda *_a, **_k: [_library_model(tmp_path, "pub/Some-MLX", ModelFormat.mlx)])
    _installed(monkeypatch, "llama-server")
    check = next(c for c in doctor.check_model(_settings(tmp_path)) if c.name == doctor.MODEL_ROW)
    assert check.status is doctor.CheckStatus.warn
    assert "mlx_lm.server" in check.detail


def test_default_model_is_ok_when_its_runtime_is_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.project_ops, "load", lambda: doctor.project_ops.Project(model="pub/Some-GGUF"))
    monkeypatch.setattr(
        library, "find", lambda *_a, **_k: [_library_model(tmp_path, "pub/Some-GGUF", ModelFormat.gguf)]
    )
    _installed(monkeypatch, "llama-server")
    check = next(c for c in doctor.check_model(_settings(tmp_path)) if c.name == doctor.MODEL_ROW)
    assert check.status is doctor.CheckStatus.ok
    assert "isn't installed" not in check.detail


def test_check_model_missing_from_library_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.project_ops, "load", lambda: doctor.project_ops.Project(model="pub/Absent"))
    monkeypatch.setattr(library, "find", lambda *_a, **_k: [])
    check = next(c for c in doctor.check_model(_settings(tmp_path)) if c.name == doctor.MODEL_ROW)
    assert check.status is doctor.CheckStatus.warn
    assert check.hint is not None


def test_check_model_prefers_what_is_actually_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A serving stabbur knows the resident model; the configured default is then not the answer, and
    # must not also appear — one fact, one row.
    monkeypatch.setattr(doctor.project_ops, "load", lambda: doctor.project_ops.Project(model="pub/Default"))
    rows = doctor.check_model(_settings(tmp_path), loaded=doctor.LoadedModel(name="pub/Running", n_ctx=32768))
    assert len(rows) == 1
    assert rows[0].status is doctor.CheckStatus.ok
    assert rows[0].detail == "pub/Running - loaded, 32,768 ctx"
    assert "pub/Default" not in rows[0].detail


def test_check_model_idle_server_is_not_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing loaded yet is the normal state of a freshly opened tab; an amber dot for it would
    # train people to ignore the dot. A runtime that DIED is the case worth colouring.
    monkeypatch.setattr(doctor.project_ops, "load", lambda: doctor.project_ops.Project(model="pub/Default"))
    idle = doctor.check_model(_settings(tmp_path), loaded=doctor.LoadedModel())[0]
    assert idle.status is doctor.CheckStatus.ok
    assert "none loaded" in idle.detail and "pub/Default" in idle.detail
    dead = doctor.check_model(_settings(tmp_path), loaded=doctor.LoadedModel(error="exited with code 1"))[0]
    assert dead.status is doctor.CheckStatus.fail
    assert "exited with code 1" in dead.detail
    assert dead.hint is not None and "restart it" in dead.hint


def test_check_model_hint_does_not_offer_to_restart_a_runtime_that_never_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Under --upstream nothing local ever started, so "the runtime exited; pick a model again to
    # restart it" points at a process that does not exist and a fix that is not on this machine.
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    row = doctor.check_model(
        _settings(tmp_path), loaded=doctor.LoadedModel(error="upstream http://gpu-box:1234 unreachable", upstream=True)
    )[0]
    assert row.status is doctor.CheckStatus.fail
    assert row.hint is not None
    assert "restart" not in row.hint and "Backend row" in row.hint


def test_check_model_falls_back_to_the_upstreams_resident(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Under an upstream stabbur may have selected nothing, while the remote still has a model resident —
    # that is what a message sent right now would run on, so it is the honest answer.
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    row = doctor.check_model(_settings(tmp_path), loaded=doctor.LoadedModel(), resident="qwen3-coder")[0]
    assert row.status is doctor.CheckStatus.ok
    assert row.detail.startswith("qwen3-coder - loaded")


def test_check_project_none_emits_no_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No project and no machine default is plain free-play — surface no checks at all.
    from stabbur import config

    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-xdg"))  # no machine config
    config.get_settings.cache_clear()
    assert doctor.check_project(_settings(tmp_path)) == []
    config.get_settings.cache_clear()


_UPSTREAM_LISTING = {
    "data": [
        {"id": "example-remote-model", "status": {"value": "unloaded"}},
        {"id": "qwen3-coder", "status": {"value": "loaded"}},
    ]
}


class _FakeResponse:
    """A stubbed httpx response: no network in the doctor tests, ever."""

    def __init__(self, payload: object, *, status_code: int = 200, content_type: str = "application/json") -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=httpx.Request("GET", "http://up:1234/v1/models"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _stub_get(monkeypatch: pytest.MonkeyPatch, result: object) -> list[str]:
    """Point `doctor.httpx.get` at ``result`` (a response, or an exception to raise); record URLs."""
    urls: list[str] = []

    def _get(url: str, timeout: object = None) -> Any:
        urls.append(url)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(doctor.httpx, "get", _get)
    return urls


def _chained(exc: httpx.HTTPError, cause: BaseException) -> httpx.HTTPError:
    """Mimic how httpx surfaces a transport error: the real OSError hangs off the chain."""
    exc.__cause__ = cause
    return exc


def _backend(settings: Settings) -> doctor.Check:
    checks = doctor.check_upstream(settings).checks
    assert len(checks) == 1  # exactly one row, in both modes
    return checks[0]


def test_backend_row_says_local_when_no_upstream(tmp_path: Path) -> None:
    # No probe, but not silence either: the health menu must still answer "what is this talking to".
    row = _backend(_settings(tmp_path))
    assert row.name == "Backend"
    assert row.status is doctor.CheckStatus.ok
    assert "Local runtime" in row.detail  # same words as the SPA status bar


def test_backend_reachable_upstream_reports_what_it_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls = _stub_get(monkeypatch, _FakeResponse(_UPSTREAM_LISTING))
    probe = doctor.check_upstream(Settings(library_root=tmp_path, upstream="http://up:1234/v1/"))
    row = probe.checks[0]
    assert row.status is doctor.CheckStatus.ok
    assert urls == ["http://up:1234/v1/models"]  # trailing /v1 normalized, not doubled
    assert row.detail == "Upstream up:1234 - reachable, 2 models"
    assert row.hint is None
    # The resident model is handed to the Model row rather than stated here — probed once, said once.
    assert probe.resident == "qwen3-coder"
    assert "qwen3-coder" not in row.detail


def test_backend_upstream_without_a_loaded_model_is_still_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Only llama-server router mode reports per-model `loaded`; its absence is not a fault.
    _stub_get(monkeypatch, _FakeResponse({"data": [{"id": "some-model"}]}))
    probe = doctor.check_upstream(Settings(library_root=tmp_path, upstream="http://up:1234"))
    assert probe.checks[0].status is doctor.CheckStatus.ok
    assert probe.checks[0].detail == "Upstream up:1234 - reachable, 1 model"
    assert probe.resident is None


def test_backend_upstream_serving_nothing_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_get(monkeypatch, _FakeResponse({"data": []}))
    row = _backend(Settings(library_root=tmp_path, upstream="http://up:1234"))
    assert row.status is doctor.CheckStatus.warn  # answering is not the same as being usable
    assert "serves no models" in row.detail


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        # The four failure modes must read differently: they send the user to different places.
        (_chained(httpx.ConnectError("boom"), socket.gaierror(8, "nodename nor servname")), "does not resolve"),
        (_chained(httpx.ConnectError("boom"), ConnectionRefusedError(61, "Connection refused")), "refused"),
        (httpx.ReadTimeout("timed out"), "no answer within 5s"),
        (httpx.ConnectError("something else entirely"), "connection failed"),
    ],
)
def test_backend_unreachable_upstream_fails_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception, expected: str
) -> None:
    _stub_get(monkeypatch, raised)
    row = _backend(Settings(library_root=tmp_path, upstream="http://up:1234"))
    assert row.status is doctor.CheckStatus.fail
    assert row.detail.startswith("Upstream up:1234 - ")
    assert expected in row.detail
    assert row.hint is not None and "http://up:1234" in row.hint  # names the URL to go and look at


def test_backend_non_json_answer_names_the_content_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Something is listening, it just isn't an OpenAI /v1 - a proxy error page, a login screen.
    _stub_get(monkeypatch, _FakeResponse(ValueError("no json"), content_type="text/html"))
    row = _backend(Settings(library_root=tmp_path, upstream="http://up:1234"))
    assert row.status is doctor.CheckStatus.fail
    assert "text/html" in row.detail and "not an OpenAI model listing" in row.detail


def test_backend_http_error_status_reports_the_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_get(monkeypatch, _FakeResponse({}, status_code=404))
    row = _backend(Settings(library_root=tmp_path, upstream="http://up:1234"))
    assert row.status is doctor.CheckStatus.fail
    assert "HTTP 404" in row.detail


def test_backend_probe_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A doctor run reports a failed check; it does not traceback, whatever the transport does.
    _stub_get(monkeypatch, RuntimeError("something nobody predicted"))
    row = _backend(Settings(library_root=tmp_path, upstream="http://up:1234"))
    assert row.status is doctor.CheckStatus.fail
    assert "probe failed" in row.detail


def test_every_grouped_check_has_its_parent_in_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole hierarchy travels in the payload, so a child whose parent isn't there is a row that
    # renders nowhere. Every `group` must name a top-level check in the same report.
    monkeypatch.setattr(library, "scan", lambda: [])
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    checks = doctor.run_checks(_settings(tmp_path)).checks
    parents = {c.name for c in checks if c.group is None}
    assert {c.group for c in checks if c.group} <= parents


def test_top_level_is_the_two_facts_then_the_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # What the menu shows without opening anything: is the backend alive, what is loaded, and a
    # short shelf of headings. Anything else added here has to earn a top-level row.
    monkeypatch.setattr(library, "scan", lambda: [])
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    monkeypatch.setattr(doctor.mcpservers, "resolve", lambda: [])
    report = doctor.run_checks(_settings(tmp_path), loaded=doctor.LoadedModel(name="pub/Running"))
    top = [c.name for c in report.checks if c.group is None]
    assert top == [doctor.BACKEND_ROW, doctor.MODEL_ROW, doctor.RUNTIMES_GROUP, doctor.LIBRARY_GROUP]


def test_mcp_children_nest_under_the_tools_summary() -> None:
    # The hierarchy travels in the payload: children name their parent instead of the UI parsing
    # a "MCP: " prefix back into a tree (which breaks the moment a check is renamed).
    from contextlib import AsyncExitStack

    from stabbur import tools
    from stabbur.routers.serving import core

    toolset = tools.MCPToolset()
    toolset.schemas.append({"type": "function", "function": {"name": "datetime__now", "parameters": {}}})
    rows = core._mcp_checks(toolset, tools.MCPBridge(toolset, AsyncExitStack()))
    assert [(c.name, c.group) for c in rows] == [("datetime", doctor.MCP_GROUP)]  # no "MCP: " prefix


def test_backend_row_leads_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "scan", lambda: [])
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    names = [c.name for c in doctor.run_checks(_settings(tmp_path)).checks]
    assert names.count(doctor.BACKEND_ROW) == 1
    assert names[0] == doctor.BACKEND_ROW  # the first thing a reader wants answered


def test_check_model_shows_machine_default_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Outside a project, the machine default model (stabbur config set model) is surfaced.
    from stabbur import config, library

    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    # A real model: the row now also asks which runtime it needs, so a stand-in object won't do.
    monkeypatch.setattr(library, "find", lambda *_a, **_k: [_library_model(tmp_path, "pub/Def", ModelFormat.gguf)])
    _installed(monkeypatch, "llama-server")
    cfg = tmp_path / "stabbur" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('default_model = "pub/Def"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    row = next(c for c in doctor.check_model(_settings(tmp_path)) if c.name == doctor.MODEL_ROW)
    assert row.status is doctor.CheckStatus.ok
    assert "pub/Def" in row.detail and "machine default" in row.detail
    config.get_settings.cache_clear()
