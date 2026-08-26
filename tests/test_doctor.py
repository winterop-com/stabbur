"""Tests for the `heim doctor` health checks."""

import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from heim import doctor, library
from heim.config import Settings
from heim.models import ModelFormat


def _settings(tmp_path: Path, *, drive: bool = True) -> Settings:
    root = tmp_path / "library" if drive else tmp_path / "missing"
    if drive:
        root.mkdir(parents=True, exist_ok=True)
    return Settings(library_root=root)


def _shared_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chdir into a project that lists @shared, with HEIM_LIBRARY_ROOT removed from the env."""
    monkeypatch.delenv("HEIM_LIBRARY_ROOT", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "heim.toml").write_text('libraries = ["library", "@shared"]\n[project]\nmodel = "x"\n')
    monkeypatch.chdir(proj)


def test_project_warns_when_shared_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shared_project(tmp_path, monkeypatch)
    settings = Settings()  # no library_root set -> @shared drops out, silently unreachable
    assert "library_root" not in settings.model_fields_set
    shared = [c for c in doctor.check_project(settings) if c.name == "Shared library (@shared)"]
    assert shared and shared[0].status is doctor.CheckStatus.warn
    assert shared[0].hint and "HEIM_LIBRARY_ROOT" in shared[0].hint


def test_project_no_shared_warning_when_library_root_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shared_project(tmp_path, monkeypatch)
    settings = Settings(library_root=tmp_path / "lib")  # explicitly set -> @shared reachable
    assert not any(c.name == "Shared library (@shared)" for c in doctor.check_project(settings))


def test_platform_check_reports_os() -> None:
    checks = doctor.check_platform()
    assert len(checks) == 1
    assert checks[0].name == "Platform"
    assert checks[0].status is doctor.CheckStatus.ok
    assert checks[0].detail  # e.g. "Linux x86_64"


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
    root = next(c for c in checks if c.name == "Libraries")
    models = next(c for c in checks if c.name == "Runnable models")
    assert root.status is doctor.CheckStatus.warn  # not mounted
    assert models.status is doctor.CheckStatus.warn  # empty


def test_check_library_counts_by_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _model(name: str, fmt: ModelFormat) -> library.LibraryModel:
        return library.LibraryModel(name=name, model_format=fmt, path=tmp_path, load_target=tmp_path)

    monkeypatch.setattr(
        library,
        "scan",
        lambda: [_model("a", ModelFormat.gguf), _model("b", ModelFormat.gguf), _model("c", ModelFormat.mlx)],
    )
    models = next(c for c in doctor.check_library(_settings(tmp_path)) if c.name == "Runnable models")
    assert models.status is doctor.CheckStatus.ok
    assert "2 gguf" in models.detail and "1 mlx" in models.detail


def test_check_project_missing_model_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.project_ops, "load", lambda: doctor.project_ops.Project(model="pub/Absent"))
    monkeypatch.setattr(library, "find", lambda *_a, **_k: [])
    check = next(c for c in doctor.check_project(_settings(tmp_path)) if c.name == "Default model")
    assert check.status is doctor.CheckStatus.warn
    assert check.hint is not None


def test_check_project_none_emits_no_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No project and no machine default is plain free-play — surface no checks at all.
    from heim import config

    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-xdg"))  # no machine config
    config.get_settings.cache_clear()
    assert doctor.check_project(_settings(tmp_path)) == []
    config.get_settings.cache_clear()


_UPSTREAM_LISTING = {
    "data": [
        {"id": "gemma-4-12b-qat", "status": {"value": "unloaded"}},
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
    checks = doctor.check_upstream(settings)
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
    row = _backend(Settings(library_root=tmp_path, upstream="http://up:1234/v1/"))
    assert row.status is doctor.CheckStatus.ok
    assert urls == ["http://up:1234/v1/models"]  # trailing /v1 normalized, not doubled
    assert row.detail == "Upstream up:1234 - reachable, 2 models, loaded: qwen3-coder"
    assert row.hint is None


def test_backend_upstream_without_a_loaded_model_is_still_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Only llama-server router mode reports per-model `loaded`; its absence is not a fault.
    _stub_get(monkeypatch, _FakeResponse({"data": [{"id": "some-model"}]}))
    row = _backend(Settings(library_root=tmp_path, upstream="http://up:1234"))
    assert row.status is doctor.CheckStatus.ok
    assert row.detail == "Upstream up:1234 - reachable, 1 model (none reported loaded)"


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


def test_checks_are_top_level_unless_grouped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `group` is opt-in: adding it must leave every existing check a flat, top-level row.
    monkeypatch.setattr(library, "scan", lambda: [])
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    assert all(c.group is None for c in doctor.run_checks(_settings(tmp_path)).checks)


def test_mcp_children_nest_under_the_tools_summary() -> None:
    # The hierarchy travels in the payload: children name their parent instead of the UI parsing
    # a "MCP: " prefix back into a tree (which breaks the moment a check is renamed).
    from contextlib import AsyncExitStack

    from heim import tools
    from heim.routers.serving import core

    toolset = tools.MCPToolset()
    toolset.schemas.append({"type": "function", "function": {"name": "datetime__now", "parameters": {}}})
    rows = core._mcp_checks(toolset, tools.MCPBridge(toolset, AsyncExitStack()))
    assert [(c.name, c.group) for c in rows] == [("datetime", doctor.MCP_GROUP)]  # no "MCP: " prefix


def test_backend_row_is_part_of_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "scan", lambda: [])
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    names = [c.name for c in doctor.run_checks(_settings(tmp_path)).checks]
    assert names.count("Backend") == 1
    assert names.index("Backend") > names.index("Platform")


def test_check_project_shows_machine_default_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Outside a project, the machine default model (heim config set model) is surfaced.
    from heim import config, library

    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    monkeypatch.setattr(library, "find", lambda *_a, **_k: [object()])  # resolves in the library
    cfg = tmp_path / "heim" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('default_model = "pub/Def"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    row = next(c for c in doctor.check_project(_settings(tmp_path)) if c.name == "Default model")
    assert row.status is doctor.CheckStatus.ok
    assert "pub/Def" in row.detail and "machine default" in row.detail
    config.get_settings.cache_clear()
