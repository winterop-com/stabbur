"""The Typer application: the root app, its sub-apps, the global callback, and the entry point."""

import shlex
from typing import TYPE_CHECKING, Annotated

import typer

from kodo import (
    capabilities,
    config,
    project,
    runtime,
    tags,
)
from kodo import library as library_ops
from kodo.models import ModelFormat

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

from kodo.cli._common import (
    _resolve_library_model,
    console,
)

app = typer.Typer(
    help="Browse, pull, and run local LLM models (Hugging Face, Ollama, LM Studio).",
    no_args_is_help=True,
)

# Command groups. `library` owns the on-disk model store (list/pull/remove/tag/
# browse); `audio` owns text-to-speech; `project` owns the ./kodo.toml assistant
# (init/show). Primary verbs (chat, serve, doctor) stay top-level.
library_app = typer.Typer(
    help="Manage your local model library (list, pull, remove, tag, browse).", no_args_is_help=True
)
project_app = typer.Typer(help="The project's assistant (./kodo.toml): scaffold and inspect it.", no_args_is_help=True)
mcp_app = typer.Typer(
    help="MCP tool servers: browse a curated catalog + installed plugins, and add them to kodo.toml.",
    no_args_is_help=True,
)
voice_app = typer.Typer(help="Voice models (TTS/STT): list and import them into the library.", no_args_is_help=True)
config_app = typer.Typer(
    help="Machine-level defaults (~/.config/kodo/config.toml): library location + default model.",
    no_args_is_help=True,
)
app.add_typer(library_app, name="library")
app.add_typer(project_app, name="project")
app.add_typer(mcp_app, name="mcp")
app.add_typer(voice_app, name="voice")
app.add_typer(config_app, name="config")


@app.callback()
def _main(
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Verbose diagnostics: runtime command + live runtime logs (else discarded)."),
    ] = False,
    runtime_port: Annotated[
        int | None,
        typer.Option("--runtime-port", help="Pin the model-runtime port (default: auto-pick a free port)."),
    ] = None,
) -> None:
    """Build and run a local library of LLM models."""
    if debug:
        config.set_debug(True)
    if runtime_port is not None:
        config.set_runtime_port(runtime_port)


class _HostContext:
    """kodo's ``PluginContext``: hands plugins the model runtime (serve/complete/resolve)."""

    console = console

    def resolve_model(self, name: str | None, model_format: str | None = None) -> library_ops.LibraryModel:
        proj = project.load()
        model_name = project.resolve_model(name, proj)
        if model_name is None:
            console.print(
                "[red]No model given.[/] Pass a model name (see [cyan]kodo library ls[/]), "
                "set a machine default ([cyan]kodo config set model <name>[/]), "
                "or define one in a project ([cyan]kodo project init[/])."
            )
            raise typer.Exit(1)
        return _resolve_library_model(model_name, ModelFormat(model_format) if model_format else None)

    def serve(self, model: library_ops.LibraryModel) -> "AbstractContextManager[str]":
        return runtime._serve(model)

    def complete(
        self, base: str, model: library_ops.LibraryModel, prompt: str, system: str = "", max_tokens: int | None = None
    ) -> str:
        return runtime.complete(base, model, prompt, system, max_tokens)

    def run_agent(
        self, base: str, model: library_ops.LibraryModel, prompt: str, servers: list[str]
    ) -> tuple[list[tuple[str, dict[str, object]]], str]:
        import asyncio  # noqa: PLC0415

        from kodo import agent, sampling  # noqa: PLC0415
        from kodo import tools as mcp_tools  # noqa: PLC0415

        rec = sampling.recommended(model)
        commands: list[tuple[str | None, list[str]]] = [(None, shlex.split(s)) for s in servers]
        calls: list[tuple[str, dict[str, object]]] = []

        async def _go() -> str:
            async with mcp_tools.connect(commands) as toolset:
                # Record each (tool, args) by wrapping the toolset's call — keeps the real
                # toolset (and its type) for agent.run; args here are already parsed dicts.
                original = toolset.call

                async def _recording_call(name: str, args: dict[str, object], timeout: float | None = None) -> str:
                    calls.append((name, args))
                    return await original(name, args, timeout=timeout)

                toolset.call = _recording_call  # type: ignore[assignment]
                return await agent.run(
                    base,
                    [{"role": "user", "content": prompt}],
                    toolset,
                    temperature=rec.temperature,
                    top_p=rec.top_p,
                    top_k=rec.top_k,
                    min_p=rec.min_p,
                    repeat_penalty=rec.repeat_penalty,
                    model=str(model.load_target),  # required by mlx-vlm; ignored by llama-server/mlx-lm
                )

        answer = asyncio.run(_go())
        return calls, answer

    def list_models(self) -> list[library_ops.LibraryModel]:
        return [m for m in library_ops.scan() if m.generative]

    def supports_tools(self, model: library_ops.LibraryModel) -> bool:
        try:
            return capabilities.capabilities(model).tools
        except Exception:  # noqa: BLE001 - detection is best-effort; assume no tools on failure
            return False

    def model_tags(self, model: library_ops.LibraryModel) -> list[str]:
        return tags.load(model.library_root).get(model.name, [])


def _mount_plugins() -> None:
    """Discover ``kodo.plugins`` and mount each plugin's command group on the CLI."""
    from kodo import plugins  # noqa: PLC0415 - keep pluginkit off the hot path for `--help`

    for name, sub in plugins.command_groups(plugins.manager(), _HostContext()):
        app.add_typer(sub, name=name)


def main() -> None:
    """Console entry point: run the app, turning config problems into clean messages, not tracebacks."""
    import tomllib  # noqa: PLC0415

    from kodo import supervisor  # noqa: PLC0415

    # Reclaim any runtime a previously-crashed kodo left orphaned (holding memory) before doing
    # anything else. Best-effort and safe — only reaps runtimes whose owning kodo is gone (A4).
    try:
        supervisor.sweep_orphans()
    except Exception:  # noqa: BLE001 - a sweep failure must never block the command
        pass

    try:
        app()
    except library_ops.LibraryNotConfigured as exc:
        console.print(f"[red]{exc}[/]")
        raise SystemExit(1) from exc
    except (project.ProjectError, tomllib.TOMLDecodeError) as exc:
        # A malformed kodo.toml (hand-edited via `kodo mcp add`) is read by project.load and by
        # get_settings() (TomlConfigSettingsSource) — catch both so one typo doesn't traceback.
        console.print(f"[red]Config error:[/] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
