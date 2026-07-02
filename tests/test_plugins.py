"""kodo's plugin seam: first-party plugins are discovered and mount their command groups."""

from typing import Any, cast

from rich.console import Console

from kodo import plugins


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
    assert "benchmark" in names  # contributed by kodo-mcp-benchmark's kodo.plugins entry point


def test_cli_mounts_plugin_commands() -> None:
    # The assembled Typer app exposes the plugin group as a real command.
    from typer.testing import CliRunner

    from kodo.cli import app

    result = CliRunner().invoke(app, ["benchmark", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output and "list" in result.output
