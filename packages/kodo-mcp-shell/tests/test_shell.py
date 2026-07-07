"""Behavior tests for the shell MCP server (in-memory client)."""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from kodo_mcp_shell import mcp


async def _call(name: str, **kw: Any) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, kw)).data


async def test_readonly_allows_a_listed_command() -> None:
    # `echo` is in the read-only allowlist and runs without a shell.
    out = await _call("run", command="echo hello")
    assert out["exit_code"] == 0 and out["stdout"].strip() == "hello"
    assert out["mode"] == "read-only" and out["truncated"] is False


async def test_readonly_blocks_a_non_allowlisted_command() -> None:
    with pytest.raises(ToolError):  # `rm` is not in the allowlist
        await _call("run", command="rm -rf /tmp/nope")


async def test_readonly_blocks_egress_commands() -> None:
    # curl/wget are deliberately excluded from read-only (exfiltration vectors).
    with pytest.raises(ToolError):
        await _call("run", command="curl https://example.com")


async def test_readonly_dual_use_subcommand_gate() -> None:
    # git is allowed only for read subcommands: `git push` is refused before running.
    with pytest.raises(ToolError):
        await _call("run", command="git push origin main")
    # `git log` is an allowed read subcommand — it runs (exit code may vary), not blocked.
    out = await _call("run", command="git log --oneline -1")
    assert out["mode"] == "read-only"


async def test_unrestricted_mode_runs_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KODO_SHELL_UNRESTRICTED", "1")
    # A pipe only works through a shell — proves full mode uses one.
    out = await _call("run", command="echo one two three | wc -w")
    assert out["exit_code"] == 0 and out["stdout"].strip() == "3"
    assert out["mode"] == "unrestricted"


async def test_empty_command_errors() -> None:
    with pytest.raises(ToolError):
        await _call("run", command="   ")
