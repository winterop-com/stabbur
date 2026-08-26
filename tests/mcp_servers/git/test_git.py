"""Behavior tests for the git MCP server (in-memory client against a real temp repo)."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from stabbur.mcp_servers.git.app import GitSettings, build_server

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Tester",
    "GIT_AUTHOR_EMAIL": "tester@example.com",
    "GIT_COMMITTER_NAME": "Tester",
    "GIT_COMMITTER_EMAIL": "tester@example.com",
    # Ignore the developer's global/system git config. Without this the suite inherits
    # whatever they have set — notably `commit.gpgsign = true`, where gpg has no TTY to
    # prompt on under pytest and each commit hangs until it fails (exit 128). Hooks,
    # templates and aliases would leak in the same way.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

# Read tools the server always advertises (there are no write tools).
_READ_TOOLS = {"git_status", "git_log", "git_diff", "git_show", "git_branches", "git_ls_files", "git_blame"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=_ENV, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    """Create a temp git repo with two commits and a dirty working tree."""
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "a.txt").write_text("hello\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "add a.txt")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("def foo():\n    return 42\n")
    _git(tmp_path, "add", "sub/b.py")
    _git(tmp_path, "commit", "-m", "add b.py")
    (tmp_path / "a.txt").write_text("hello\nworld\n")  # unstaged change -> a dirty tree
    return tmp_path


async def _call(settings: GitSettings, name: str, **kw: Any) -> Any:
    async with Client(build_server(settings)) as client:
        return (await client.call_tool(name, kw)).data


async def test_status_reports_the_dirty_tree(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    out = await _call(s, "git_status")
    assert "a.txt" in out and "main" in out


async def test_log_returns_the_commits(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    out = await _call(s, "git_log")
    assert "add a.txt" in out and "add b.py" in out and "Tester" in out
    # count is clamped and honored.
    one = await _call(s, "git_log", count=1)
    assert one.count("\n") == 0 and "add b.py" in one


async def test_log_can_scope_to_a_path(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    out = await _call(s, "git_log", path="sub/b.py")
    assert "add b.py" in out and "add a.txt" not in out


async def test_diff_shows_the_working_change(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    out = await _call(s, "git_diff")
    assert "+world" in out and "a.txt" in out


async def test_show_a_commit(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    out = await _call(s, "git_show", ref="HEAD")
    assert "add b.py" in out and "b.py" in out


async def test_branches_lists_main(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    branches = await _call(s, "git_branches")
    assert "main" in branches


async def test_ls_files_and_glob(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    files = await _call(s, "git_ls_files")
    assert "a.txt" in files and "sub/b.py" in files
    only_py = await _call(s, "git_ls_files", pattern="*.py")
    assert only_py == ["sub/b.py"]


async def test_blame_reports_author(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    out = await _call(s, "git_blame", path="sub/b.py")
    assert "Tester" in out and "def foo" in out
    ranged = await _call(s, "git_blame", path="sub/b.py", start=1, end=1)
    assert "def foo" in ranged and "return 42" not in ranged


async def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    for name, kw in (("git_blame", {"path": "../escape"}), ("git_diff", {"path": "/etc/passwd"})):
        with pytest.raises(ToolError):
            await _call(s, name, **kw)


async def test_ref_option_injection_is_rejected(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path))
    with pytest.raises(ToolError):
        await _call(s, "git_show", ref="--output=/tmp/x")


async def test_non_repo_root_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    s = GitSettings(repo_root=plain)
    with pytest.raises(ToolError):
        await _call(s, "git_status")


async def test_no_write_tools_when_readonly(tmp_path: Path) -> None:
    s = GitSettings(repo_root=_repo(tmp_path), allow_write=False)
    async with Client(build_server(s)) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == _READ_TOOLS  # exactly the read tools; nothing mutating is exposed
