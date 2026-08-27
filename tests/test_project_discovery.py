"""Walk-up project discovery: which ``stabbur.toml`` applies, and what its paths are relative to.

The rule spans four modules — :mod:`stabbur.project` finds the manifest, :func:`stabbur.library.roots`
and :func:`stabbur.mcpservers.project_path` resolve against it — so the whole rule is tested in one
place rather than a fragment per module.
"""

import json
from pathlib import Path

import pytest

from stabbur import library, mcpservers, project
from stabbur.config import Settings

_MANIFEST = '[project]\nmodel = "pub/X-GGUF"\nsystem_prompt = "Be brief."\n'


def _project(root: Path, body: str = _MANIFEST) -> Path:
    """Write a project at ``root`` and return a nested subdirectory inside it (``src/deep``)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "stabbur.toml").write_text(body)
    sub = root / "src" / "deep"
    sub.mkdir(parents=True)
    return sub


# --- finding the manifest ---------------------------------------------------------------------


def test_discover_in_the_project_root_stays_the_bare_relative_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Standing in the project root is the pre-walk-up case and must be byte-for-byte unchanged:
    # the plain relative name, which is what every error message and hint prints.
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert project.discover() == Path("stabbur.toml")


def test_discover_walks_up_from_a_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The gap this closes: from `myproject/src/deep` every command used to see no project at all
    # and silently drop to free-play. Found further up, the path is absolute so callers can say which.
    sub = _project(tmp_path)
    monkeypatch.chdir(sub)
    assert project.discover() == tmp_path / "stabbur.toml"


def test_discover_returns_none_outside_any_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Free-play is a real state, not a fallback bug: no manifest anywhere up the chain means None.
    monkeypatch.setenv("HOME", str(tmp_path))  # bound the walk so the machine's own dirs can't leak in
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert project.discover() is None
    assert project.load() is None


def test_discover_stops_at_the_nearest_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A nested project shadows the enclosing one from there down — first hit wins, like `git`.
    outer = tmp_path / "outer"
    sub = _project(outer)  # outer/src/deep
    inner = outer / "src"
    (inner / "stabbur.toml").write_text(_MANIFEST)
    monkeypatch.chdir(sub)
    assert project.discover() == inner / "stabbur.toml"


# --- the boundaries ---------------------------------------------------------------------------


def test_the_filesystem_root_is_never_searched(tmp_path: Path) -> None:
    # A stabbur.toml in `/` would otherwise claim every shell on the machine.
    dirs = list(project._search_dirs(tmp_path))
    assert dirs[0] == tmp_path
    assert Path(tmp_path.anchor) not in dirs


def test_home_is_the_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    work = home / "work"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)

    # Above home (/Users, /home, and up) is other people's business — never searched.
    (tmp_path / "stabbur.toml").write_text(_MANIFEST)
    assert project.discover() is None

    # Home *itself* is searched, so a manifest people keep in ~ still applies.
    (home / "stabbur.toml").write_text(_MANIFEST)
    assert project.discover() == home / "stabbur.toml"


def test_a_mount_boundary_stops_the_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A project on an external drive must not reach back into the machine's own filesystem.
    # `_device` is stubbed rather than mounting anything: the boundary is an st_dev change.
    (tmp_path / "stabbur.toml").write_text(_MANIFEST)  # on "the machine", across the mount
    drive = tmp_path / "drive"
    sub = drive / "assistant"
    sub.mkdir(parents=True)
    real_device = project._device
    monkeypatch.setattr(project, "_device", lambda p: 42 if p == drive or drive in p.parents else real_device(p))
    monkeypatch.chdir(sub)
    assert project.discover() is None

    # Everything up to the mount point is still fair game.
    (drive / "stabbur.toml").write_text(_MANIFEST)
    assert project.discover() == drive / "stabbur.toml"


# --- what a discovered manifest is relative to -------------------------------------------------


def test_load_from_a_subdirectory_records_where_the_manifest_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = _project(tmp_path)
    monkeypatch.chdir(sub)
    proj = project.load()
    assert proj is not None
    assert proj.model == "pub/X-GGUF"
    assert proj.manifest_path == tmp_path / "stabbur.toml"
    assert proj.directory == tmp_path  # the base for every project-relative path, not the cwd


def test_roots_resolves_relative_libraries_against_the_manifest_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The correctness consequence of walking up: resolved against the cwd, `libraries = ["models"]`
    # would mean <project>/src/deep/models from this subdirectory — a store that doesn't exist, so
    # the project would silently run with none of its own models.
    sub = _project(tmp_path, 'libraries = ["models", "@shared"]\n\n[project]\nmodel = "pub/X-GGUF"\n')
    shared = tmp_path / "shared"
    settings = Settings(library_root=shared)
    monkeypatch.setattr(library._roots, "get_settings", lambda: settings)
    monkeypatch.chdir(sub)
    assert library.roots(settings) == [tmp_path / "models", shared]


def test_project_mcp_json_is_found_next_to_the_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `.mcp.json` is the project's tools, so it lives beside the project's manifest — a subdirectory
    # must get those tools instead of looking for a file that only ever exists at the top.
    sub = _project(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # no machine-global servers
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"datetime": {"command": "stabbur-mcp-datetime"}}}))
    monkeypatch.chdir(sub)
    assert mcpservers.project_path() == tmp_path / ".mcp.json"
    assert [s.name for s in mcpservers.resolve()] == ["datetime"]


def test_project_mcp_json_falls_back_to_the_cwd_outside_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Outside a project there is nothing to be adjacent to, so the pre-walk-up answer stands.
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert mcpservers.project_path() == work / ".mcp.json"
