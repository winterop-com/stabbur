"""heim's plugin seam: first-party plugins are discovered and mount their command groups."""

from typing import Any, cast

from rich.console import Console

from heim import plugins


class _FakeContext:
    """A stand-in PluginContext; discovery/build never call the runtime methods."""

    console = Console()

    def resolve_model(self, name: str | None, model_format: str | None = None) -> Any:
        raise NotImplementedError

    def serve(self, model: Any) -> Any:
        raise NotImplementedError

    def complete(self, base: str, model: Any, prompt: str, system: str = "", max_tokens: int | None = None) -> str:
        raise NotImplementedError


def test_benchmark_plugin_is_discovered_and_mounts_a_group() -> None:
    # Exercises the whole seam: entry-point discovery -> pluginkit -> commands() -> (name, app).
    pm = plugins.load_plugins()
    groups = plugins.command_groups(pm, cast(plugins.PluginContext, _FakeContext()))
    names = [name for name, _ in groups]
    assert "benchmark" in names  # contributed by heim-benchmark's heim.plugins entry point


def test_cli_mounts_plugin_commands() -> None:
    # The assembled Typer app exposes the plugin group as a real command.
    from typer.testing import CliRunner

    from heim.cli import app

    result = CliRunner().invoke(app, ["benchmark", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output and "list" in result.output


def test_advertised_mcp_servers_and_resolution() -> None:
    # Register the advertise-only plugins directly (entry-point-independent) and check
    # the mcp_servers hook surfaces them and that names resolve to spawn commands.
    from pluginkit import PluginManager

    from heim.benchmark.plugin import PLUGIN as BENCH
    from heim.mcp_servers.datetime.plugin import PLUGIN as DATETIME

    pm = PluginManager(plugins.PROJECT)
    pm.add_extension_points(plugins.Specs)
    pm.register(DATETIME, name="datetime")
    pm.register(BENCH, name="benchmark")

    servers = {s.name: s.command for s in plugins.advertised_servers(pm)}
    assert servers["datetime"] == "heim-mcp-datetime"
    # benchmark is a dev/benchmarking tool (heim benchmark + its own MCP app), not an
    # everyday assistant tool, so it deliberately advertises no assistant MCP server.
    assert "benchmark" not in servers
