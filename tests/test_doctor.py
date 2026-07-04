"""Tests for the `kodo doctor` health checks."""

from pathlib import Path

import pytest

from kodo import doctor, library
from kodo.config import Settings
from kodo.models import ModelFormat


def _settings(tmp_path: Path, *, drive: bool = True) -> Settings:
    root = tmp_path / "library" if drive else tmp_path / "missing"
    if drive:
        root.mkdir(parents=True, exist_ok=True)
    return Settings(library_root=root)


def _shared_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chdir into a project that lists @shared, with KODO_LIBRARY_ROOT removed from the env."""
    monkeypatch.delenv("KODO_LIBRARY_ROOT", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "kodo.toml").write_text('libraries = ["library", "@shared"]\n[project]\nmodel = "x"\n')
    monkeypatch.chdir(proj)


def test_project_warns_when_shared_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shared_project(tmp_path, monkeypatch)
    settings = Settings()  # no library_root set -> @shared drops out, silently unreachable
    assert "library_root" not in settings.model_fields_set
    shared = [c for c in doctor.check_project(settings) if c.name == "Shared library (@shared)"]
    assert shared and shared[0].status is doctor.CheckStatus.warn
    assert shared[0].hint and "KODO_LIBRARY_ROOT" in shared[0].hint


def test_project_no_shared_warning_when_library_root_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _shared_project(tmp_path, monkeypatch)
    settings = Settings(library_root=tmp_path / "lib")  # explicitly set -> @shared reachable
    assert not any(c.name == "Shared library (@shared)" for c in doctor.check_project(settings))


def test_report_status_rolls_up_worst() -> None:
    ok = doctor.Check(name="a", status=doctor.CheckStatus.ok, detail="")
    warn = doctor.Check(name="b", status=doctor.CheckStatus.warn, detail="")
    fail = doctor.Check(name="c", status=doctor.CheckStatus.fail, detail="")
    assert doctor.DoctorReport(checks=[ok]).status is doctor.CheckStatus.ok
    assert doctor.DoctorReport(checks=[ok, warn]).status is doctor.CheckStatus.warn
    assert doctor.DoctorReport(checks=[ok, warn, fail]).status is doctor.CheckStatus.fail


def test_runtime_check_missing_required_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _b: None)
    c = doctor._runtime_check("GGUF", "llama-server", required=True)
    assert c.status is doctor.CheckStatus.fail
    assert c.hint  # carries an install hint


def test_runtime_check_missing_optional_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _b: None)
    assert doctor._runtime_check("MLX", "mlx_lm.server", required=False).status is doctor.CheckStatus.warn


def test_runtime_check_not_relevant_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    # MLX off Apple Silicon: missing binary is fine (N/A), not a warning.
    monkeypatch.setattr(doctor.shutil, "which", lambda _b: None)
    c = doctor._runtime_check("MLX", "mlx_lm.server", required=False, relevant=False)
    assert c.status is doctor.CheckStatus.ok


def test_runtime_check_present_reports_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _b: "/usr/bin/llama-server")
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
    # No project is a valid free-play mode, so it should surface no project checks at all.
    monkeypatch.setattr(doctor.project_ops, "load", lambda: None)
    assert doctor.check_project(_settings(tmp_path)) == []
