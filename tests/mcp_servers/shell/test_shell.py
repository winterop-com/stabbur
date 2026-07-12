"""Behavior tests for the shell MCP server (in-memory client)."""

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from heim.mcp_servers.shell import app, mcp


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


async def test_readonly_gates_mutating_net_binaries() -> None:
    # ip/route/arp/ifconfig are mutating-capable, so they're no longer whole-binary allowed:
    # only their show/list forms pass in read-only mode.
    for cmd in (
        "ip link set lo down",
        "ip addr add 10.0.0.1/24 dev lo",
        "route add default gw 10.0.0.1",
        "arp -d 10.0.0.1",
        "ifconfig lo0 down",
    ):
        with pytest.raises(ToolError):
            await _call("run", command=cmd)


def test_net_binary_readonly_predicates() -> None:
    # Read forms stay allowed (checked at the predicate level — the binaries themselves
    # may not exist on every platform/CI runner).
    assert app._ip_readonly([]) and app._ip_readonly(["addr"]) and app._ip_readonly(["-s", "link"])
    assert app._ip_readonly(["route", "get", "8.8.8.8"]) and app._ip_readonly(["link", "show"])
    assert not app._ip_readonly(["link", "set", "lo", "down"])
    # -batch/-b runs an arbitrary ip command file (mutations included) — must not slip the empty-verb path.
    assert not app._ip_readonly(["-b", "/tmp/cmds"])
    assert not app._ip_readonly(["-batch", "/tmp/cmds"])
    assert not app._ip_readonly(["-force", "-b", "/tmp/cmds"])
    assert app._route_readonly(["-n"]) and not app._route_readonly(["del", "default"])
    assert app._arp_readonly(["-a", "-n"]) and not app._arp_readonly(["-s", "h", "aa:bb"])
    assert app._ifconfig_readonly(["-a"]) and app._ifconfig_readonly(["lo0"])
    assert not app._ifconfig_readonly(["lo0", "up"])


async def test_unrestricted_mode_runs_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEIM_SHELL_UNRESTRICTED", "1")
    # A pipe only works through a shell — proves full mode uses one.
    out = await _call("run", command="echo one two three | wc -w")
    assert out["exit_code"] == 0 and out["stdout"].strip() == "3"
    assert out["mode"] == "unrestricted"


async def test_empty_command_errors() -> None:
    with pytest.raises(ToolError):
        await _call("run", command="   ")


async def test_timeout_returns_partial_output(monkeypatch: pytest.MonkeyPatch) -> None:
    # A command that prints then hangs (ping without -c, tail -f, ...) is stopped at the timeout
    # and returns what it printed, flagged — not an empty error.
    monkeypatch.setenv("HEIM_SHELL_UNRESTRICTED", "1")  # need a shell to `sleep`
    monkeypatch.setattr(app, "_TIMEOUT", 0.5)
    out = await _call("run", command="printf 'ONE\\nTWO\\n'; sleep 5")
    assert out["timed_out"] is True and out["exit_code"] is None
    assert "ONE" in out["stdout"] and "TWO" in out["stdout"]
    assert "bounded" in out.get("note", "")


async def test_timeout_kills_the_whole_process_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # subprocess.run would kill only the direct bash, leaving its background children
    # running detached; the group kill must take them down too.
    import asyncio  # noqa: PLC0415

    monkeypatch.setenv("HEIM_SHELL_UNRESTRICTED", "1")
    monkeypatch.setattr(app, "_TIMEOUT", 0.5)
    marker = tmp_path / "grandchild-survived"
    out = await _call("run", command=f"(sleep 2 && touch {marker}) & wait")
    assert out["timed_out"] is True
    await asyncio.sleep(2.2)  # past the grandchild's own sleep
    assert not marker.exists()  # it was killed with the group, so it never wrote the marker
