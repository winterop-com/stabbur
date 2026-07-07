"""Tests for project scaffolding logic extracted from the CLI (A7).

These functions used to live in command bodies, reachable only through Typer; extracting them to
``kodo.scaffold`` makes them directly testable.
"""

from __future__ import annotations

from pathlib import Path

from kodo.project import scaffold


def test_strip_uvx() -> None:
    assert scaffold.strip_uvx("uvx dhis2w-mcp-bridge") == "dhis2w-mcp-bridge"
    assert scaffold.strip_uvx("kodo-mcp-datetime") == "kodo-mcp-datetime"  # unchanged when no uvx


def test_split_env_prefix() -> None:
    cmd, env = scaffold.split_env_prefix("env DHIS2_PROFILE=play42 dhis2w-mcp-bridge")
    assert cmd == "dhis2w-mcp-bridge"
    assert env == {"DHIS2_PROFILE": "play42"}
    # No prefix → command unchanged, empty env.
    assert scaffold.split_env_prefix("kodo-mcp-utils") == ("kodo-mcp-utils", {})


def test_pip_deps_from_mcp() -> None:
    mcp = [
        ("bridge", "uvx dhis2w-mcp-bridge==1.2.0"),  # uvx pkg → pinned dep (version stripped)
        ("datetime", "kodo-mcp-datetime"),  # bundled, no uvx → skipped
        ("node", "npx some-node-server"),  # node runner → skipped
    ]
    assert scaffold.pip_deps_from_mcp(mcp) == ["dhis2w-mcp-bridge"]


def test_render_pyproject_pins_kodo_and_mcp_deps() -> None:
    text = scaffold.render_pyproject(
        "My Project!", mcp=[("bridge", "uvx dhis2w-mcp-bridge")], mlx=True, extras=["voice"]
    )
    assert 'name = "my-project"' in text  # sanitized package name
    assert '"kodo[mlx,voice]"' in text  # extras merged + sorted, mlx added
    assert '"dhis2w-mcp-bridge"' in text  # the uvx server pinned
    assert "[tool.uv.sources]" in text and "editable = true" in text  # local kodo checkout pinned


def test_add_pyproject_dep_inserts_and_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\ndependencies = [\n    "kodo",\n]\n')
    assert scaffold.add_pyproject_dep(p, "extra-pkg") is True
    assert '"extra-pkg",' in p.read_text()
    assert scaffold.add_pyproject_dep(p, "extra-pkg") is False  # already present → left alone
    assert scaffold.add_pyproject_dep(p, "kodo") is False  # existing dep (bare) not re-added


def test_add_pyproject_dep_expands_empty_list(tmp_path: Path) -> None:
    p = tmp_path / "pyproject.toml"
    p.write_text("[project]\ndependencies = []\n")
    assert scaffold.add_pyproject_dep(p, "only") is True
    assert '"only",' in p.read_text()


def test_render_readme_mentions_the_local_library() -> None:
    text = scaffold.render_readme("Demo")
    assert "Demo" in text and f"`{scaffold.LOCAL_LIBRARY}/`" in text


def test_git_init_writes_gitignore_and_inits(tmp_path: Path) -> None:
    ok, status = scaffold.git_init(tmp_path)
    gi = tmp_path / ".gitignore"
    assert gi.is_file() and f"/{scaffold.LOCAL_LIBRARY}/" in gi.read_text() and ".env" in gi.read_text()
    # git is available in CI; either it initialized (ok) or it was already a repo — both are ok=True.
    assert ok and "wrote .gitignore" in status or "initialized" in status
    # A second call on the now-existing repo reports it, still ok.
    ok2, status2 = scaffold.git_init(tmp_path)
    assert ok2 and "already a repo" in status2
