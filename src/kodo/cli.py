"""Command-line interface for browsing, pulling, and running local models."""

import os
import secrets
import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kodo import (
    attach,
    capabilities,
    cards,
    config,
    consumers,
    doctor,
    host,
    mcp_catalog,
    mcpservers,
    project,
    runtime,
    scaffold,
    tags,
    userconfig,
)
from kodo import catalog as catalog_ops
from kodo import library as library_ops
from kodo.config import get_settings
from kodo.models import CuratedModel, ModelFormat, ModelSource, _human_size
from kodo.sources import huggingface as hf
from kodo.templates import TEMPLATES

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

console = Console()

_FORMAT_STYLE = {
    ModelFormat.gguf: "cyan",
    ModelFormat.mlx: "magenta",
    ModelFormat.safetensors: "yellow",
    ModelFormat.unknown: "dim",
}

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


# The MCP catalog (curated + optional first-party servers) lives in `kodo.mcp_catalog` so the
# web layer can share it. Aliases keep the existing call sites terse.
_CURATED_MCP = mcp_catalog.CURATED
_OPTIONAL_FIRST_PARTY = mcp_catalog.OPTIONAL_FIRST_PARTY
_uninstalled_optional = mcp_catalog.uninstalled_optional


# Project templates now live in kodo.templates (TEMPLATES).


def _project_mcp_keys() -> set[str]:
    """Names of MCP servers already in the local ``.mcp.json`` (empty if none)."""
    return {s.name for s in mcpservers.read_project()}


@mcp_app.command("list")
@mcp_app.command("ls", hidden=True)  # `ls` alias, mirroring `kodo library ls`
def mcp_list() -> None:
    """List MCP tool servers: first-party plugins first, then an external catalog.

    First-party ``kodo-mcp-*`` plugins are the recommended set — kodo controls them, they
    need no external runtime, and they're always up to date. The external catalog is a
    fallback for tools kodo doesn't ship yet. A [green]✓[/] marks a server already in this
    directory's ``kodo.toml``; add one with ``kodo mcp add <name>``.
    """
    from kodo import plugins  # noqa: PLC0415

    in_project = _project_mcp_keys()

    def mark(name: str, command: str) -> str:
        return "[green]✓[/]" if name in in_project or command in in_project else ""

    # First-party plugins lead: they're what kodo controls and recommends.
    servers = plugins.advertised_servers(plugins.manager())
    if servers:
        plug = Table(
            box=box.SIMPLE,
            header_style="bold",
            title="[bold]Installed plugins[/] [dim]— first-party, kodo-controlled (recommended)[/]",
            title_justify="left",
        )
        plug.add_column("IN", justify="center")
        plug.add_column("NAME", style="cyan")
        plug.add_column("COMMAND")
        plug.add_column("DESCRIPTION", style="dim")
        for s in servers:
            plug.add_row(mark(s.name, s.command), s.name, s.command, s.description)
        console.print(plug)

    # Optional first-party servers not yet installed — show them with an install hint so they're
    # discoverable before `make install-<name>` (once installed they move up to Installed plugins).
    optional = _uninstalled_optional({s.name for s in servers})
    if optional:
        opt = Table(
            box=box.SIMPLE,
            header_style="bold",
            title="[bold]Optional first-party[/] [dim]— not installed yet[/]",
            title_justify="left",
        )
        opt.add_column("NAME", style="cyan")
        opt.add_column("INSTALL")
        opt.add_column("DESCRIPTION", style="dim", overflow="fold")
        for o in optional:
            opt.add_row(o.name, o.setup.replace("install with: ", ""), o.description)
        console.print(opt)

    catalog = Table(
        box=box.SIMPLE,
        header_style="bold",
        title="[bold]External catalog[/] [dim]— third-party servers, added on demand[/]",
        title_justify="left",
    )
    catalog.add_column("IN", justify="center")
    catalog.add_column("NAME", style="cyan")
    catalog.add_column("COMMAND")
    catalog.add_column("DESCRIPTION", style="dim", overflow="fold")
    for c in _CURATED_MCP:
        desc = c.description + (f"\n[yellow]setup:[/] [dim]{c.setup}[/]" if c.setup else "")
        catalog.add_row(mark(c.name, c.command), c.name, c.command, desc)
    console.print(catalog)
    console.print("[dim]Add one to this project's kodo.toml (plugins or catalog):[/] kodo mcp add <name>")


@mcp_app.command("add")
def mcp_add(
    name: Annotated[str, typer.Argument(help="An installed plugin or external-catalog name (see `kodo mcp list`).")],
    to_global: Annotated[
        bool,
        typer.Option(
            "--global", "-g", help="Add to the machine-global ~/.config/kodo/mcp.json instead of this project."
        ),
    ] = False,
) -> None:
    """Add an MCP server to the standard ``mcpServers`` JSON (idempotent — replaces by name).

    Targets this directory's ``.mcp.json`` by default, or the machine-global
    ``~/.config/kodo/mcp.json`` with ``--global`` (tools every free-play chat gets). Resolves
    ``name`` against installed first-party plugins first, then the external catalog; edit the
    entry afterwards if its command has a placeholder (path/profile).
    """
    from kodo import plugins  # noqa: PLC0415

    # First-party plugins win a name clash — kodo controls them, so prefer them over external.
    server = next((s for s in plugins.advertised_servers(plugins.manager()) if s.name == name), None)
    uninstalled_optional = False
    if server is not None:
        entry_name, command, setup = server.name, server.command, ""
    else:
        # An optional first-party server (e.g. `web`) resolves here when its extra isn't installed
        # — add it, but warn it reports 0 tools until installed.
        chosen = next((c for c in _CURATED_MCP if c.name == name), None) or next(
            (c for c in _OPTIONAL_FIRST_PARTY if c.name == name), None
        )
        if chosen is None:
            console.print(f"[red]Unknown MCP {name!r}.[/] See [bold]kodo mcp list[/] for the catalog.")
            raise typer.Exit(1)
        entry_name, command, setup = chosen.name, chosen.command, chosen.setup
        uninstalled_optional = chosen in _OPTIONAL_FIRST_PARTY

    target = mcpservers.global_path() if to_global else mcpservers.project_path()
    existing = mcpservers.read_global() if to_global else mcpservers.read_project()
    if any(s.name == entry_name for s in existing):
        console.print(f"[yellow]{entry_name}[/] is already in {target}.")
        return

    # In a uv *project* (pyproject.toml present, not --global) the server is a pinned dependency,
    # so drop the runtime `uvx` fetch from the command and add the package to pyproject.toml.
    pyproject = Path("pyproject.toml")
    uv_project = not to_global and pyproject.is_file()
    mcpservers.add(_to_mcp_server(entry_name, scaffold.strip_uvx(command) if uv_project else command), glob=to_global)
    console.print(f"[green]Added[/] [cyan]{entry_name}[/] to {target}")

    if uv_project:
        for pkg in scaffold.pip_deps_from_mcp([(entry_name, command)]):  # original (uvx) command -> pip pkg
            if scaffold.add_pyproject_dep(pyproject, pkg):
                console.print(f"  [dim]uv:[/] pinned [cyan]{pkg}[/] in pyproject.toml — run [bold]uv sync[/]")
    if uninstalled_optional:
        console.print(f"  [yellow]not installed[/] — reports 0 tools until you {setup}")
    elif setup:
        console.print(f"  [yellow]setup:[/] {setup}")
    console.print("[dim]Check it:[/] kodo project show")


@mcp_app.command("remove")
@mcp_app.command("rm", hidden=True)
def mcp_remove(
    name: Annotated[str, typer.Argument(help="The server name to remove (see `kodo project show`).")],
    from_global: Annotated[
        bool, typer.Option("--global", "-g", help="Remove from the machine-global mcp.json instead of this project.")
    ] = False,
) -> None:
    """Remove an MCP server from this project's ``.mcp.json`` or the global ``mcp.json`` (``--global``)."""
    written = mcpservers.remove(name, glob=from_global)
    target = mcpservers.global_path() if from_global else mcpservers.project_path()
    if written is None:
        console.print(f"[yellow]{name}[/] is not in {target}.")
        raise typer.Exit(1)
    console.print(f"[green]Removed[/] [cyan]{name}[/] from {target}")


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


SourceOption = Annotated[
    ModelSource | None,
    typer.Option("--source", "-s", help="Limit to a single source."),
]
FormatOption = Annotated[
    ModelFormat | None,
    typer.Option("--format", "-f", help="Disambiguate when a model exists in multiple formats."),
]


# Curated starter models for `kodo project init` (verified GGUF repos; small but capable
# — sub-1B toy models are too weak to be useful defaults, so the floor is ~3B).
_CURATED: list[CuratedModel] = [
    CuratedModel(id="unsloth/Llama-3.2-3B-Instruct-GGUF", note="tiny + fast, kinda works — good for testing (~2 GB)"),
    CuratedModel(id="unsloth/Qwen3.5-4B-GGUF", note="compact + good at tools (~2.5 GB)"),
    CuratedModel(id="lmstudio-community/gemma-4-12B-it-QAT-GGUF", note="capable all-rounder, vision + audio (~6.7 GB)"),
    CuratedModel(id="unsloth/gpt-oss-20b-GGUF", note="strong reasoning + tools (~10.8 GB)"),
    CuratedModel(id="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", note="coding specialist (~17 GB)"),
]


def _fmt_cell(model_format: ModelFormat) -> str:
    """Render a format value with its color style."""
    return f"[{_FORMAT_STYLE[model_format]}]{model_format.value}[/]"


def _caps_label(caps: "capabilities.ModelCapabilities | None") -> str:
    """A compact, colored list of a model's capabilities (present ones only)."""
    if caps is None:
        return "[dim]?[/]"
    parts = []
    if caps.tools:
        parts.append("[cyan]tools[/]")
    if caps.vision:
        parts.append("[magenta]vision[/]")
    if caps.audio:
        parts.append("[green]audio[/]")
    return " ".join(parts) or "[dim]—[/]"


def _fmt_ctx(n: int | None) -> str:
    """Human-readable context window (e.g. 262144 → 256K)."""
    if not n:
        return "[dim]—[/]"
    return f"{round(n / 1024)}K" if n >= 1024 else str(n)


def _print_model_card(model: library_ops.LibraryModel, model_tags: list[str]) -> None:
    """Print one model as a full-detail 'card' — a bordered panel of key/value fields."""
    try:
        caps = capabilities.capabilities(model)
    except Exception:  # noqa: BLE001 - detection is best-effort; never break the listing
        caps = None
    ctx = caps.context_length if caps else None
    ctx_str = _fmt_ctx(ctx) + (f" [dim]({ctx:,} tokens)[/]" if ctx else "")
    loads = f"[dim]{model.load_target.name}[/]"
    if model.mmproj:
        loads += f" [dim](+ mmproj {model.mmproj.name})[/]"

    body = Table.grid(padding=(0, 2))
    body.add_column(style="dim", justify="right")
    body.add_column(overflow="fold")
    body.add_row("format", f"{_fmt_cell(model.model_format)}  [dim]· {model.size_human} · {model.file_count} files[/]")
    body.add_row("capabilities", _caps_label(caps))
    body.add_row("context", ctx_str)
    body.add_row("library", f"[dim]{model.library_root}[/]")
    body.add_row("loads", loads)
    body.add_row("path", f"[dim]{model.path}[/]")
    if model_tags:
        body.add_row("tags", "  ".join(f"[cyan]{t}[/]" for t in model_tags))
    if model.is_ollama:
        body.add_row("runtime", "[yellow]Ollama[/] [dim](not llama.cpp)[/]")

    console.print(
        Panel(
            body,
            title=f"[bold white]{model.name}[/]",
            title_align="left",
            border_style=_FORMAT_STYLE[model.model_format],
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


#: The project-local library directory that `kodo project init` scaffolds (owned by kodo.scaffold).
_LOCAL_LIBRARY = scaffold.LOCAL_LIBRARY


def _to_mcp_server(name: str, command: str) -> mcpservers.McpServer:
    """Turn a CLI ``(name, command)`` pair into a standard :class:`kodo.mcpservers.McpServer`.

    Splits a leading ``env VAR=val …`` prefix into the entry's ``env`` table, then the remaining
    command into ``command`` + ``args`` (the ``mcpServers`` JSON shape).
    """
    cmd, env = scaffold.split_env_prefix(command)
    argv = shlex.split(cmd)
    return mcpservers.McpServer(name=name, command=argv[0], args=argv[1:], env=env)


def _cli_mcp_spec(value: str) -> tuple[str | None, list[str], dict[str, str]]:
    """Resolve a ``--mcp`` value to the ``(name, argv, env)`` spec :func:`kodo.tools.connect` wants.

    An advertised server *name* (or *command*) resolves to that server; anything else is used
    verbatim as a command. A leading ``env VAR=val`` prefix is lifted into ``env``.
    """
    from kodo import plugins  # noqa: PLC0415

    name, command = plugins.resolve_mcp(value)
    cmd, env = scaffold.split_env_prefix(command)
    return name, shlex.split(cmd), env


def _library_names() -> set[str]:
    """Names of models already in the library, plus their bare repo/tag forms."""
    names: set[str] = set()
    for m in library_ops.scan():
        names.add(m.name.lower())
        names.add(m.name.rsplit("/", 1)[-1].lower())
    return names


_DOCTOR_STYLE = {
    doctor.CheckStatus.ok: ("green", "ok"),
    doctor.CheckStatus.warn: ("yellow", "warn"),
    doctor.CheckStatus.fail: ("red", "fail"),
}


def _print_doctor_table(report: doctor.DoctorReport) -> None:
    """Render a doctor report as the shared status table (used by `doctor` and `setup`)."""
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    table.add_column("", width=4)
    table.add_column("Check", style="bold")
    table.add_column("Detail", overflow="fold")
    for check in report.checks:
        color, label = _DOCTOR_STYLE[check.status]
        detail = check.detail
        if check.hint:
            detail += f"\n[dim]{check.hint}[/]"
        table.add_row(f"[{color}]{label}[/]", check.name, detail)
    console.print(table)


@app.command("doctor")
def doctor_() -> None:  # doctor_ to avoid shadowing the imported doctor module
    """Check system health: runtimes, library, and the current project.

    A quick pre-flight: are the runtime binaries kodo spawns installed, is the
    library reachable and non-empty, and does the project (if any) point at a
    model that's present. Exits non-zero if any check fails.
    """
    report = doctor.run_checks()
    _print_doctor_table(report)
    if report.status is doctor.CheckStatus.fail:
        console.print("\n[red]Some checks failed.[/] Address the items above to run models.")
        raise typer.Exit(1)
    if report.status is doctor.CheckStatus.warn:
        console.print("\n[yellow]All essentials present[/], with warnings above.")
    else:
        console.print("\n[green]All good.[/]")


def _setup_library_root(explicit: Path | None, yes: bool) -> None:
    """Ensure a library location is configured, persisting it to the machine config."""
    current = config.Settings().library_root
    if explicit is None and current is not None:
        console.print(f"[green]Library[/]  already configured -> {current}")
        return
    fallback = userconfig.default_library_dir()
    if explicit is not None:
        root = explicit.expanduser().resolve()
    elif yes:
        root = fallback.resolve()
    else:
        raw = typer.prompt("Where should your library live?", default=str(fallback))
        root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    userconfig.set_value("library_root", str(root))
    config.get_settings.cache_clear()  # so the model picker + final doctor see the new root
    console.print(f"[green]Set[/]  library root -> {root}")
    # The machine config is the lowest-priority source, so a KODO_LIBRARY_ROOT env var or a cwd
    # .env still shadows what we just wrote. Say so rather than let doctor's differing value baffle.
    effective = config.Settings().library_root
    if effective is not None and effective != root:
        console.print(
            f"[yellow]Note[/]  a higher-priority source (KODO_LIBRARY_ROOT env or ./.env) still "
            f"points at [cyan]{effective}[/] and will win over the machine config."
        )


def _setup_default_model(explicit: str | None, yes: bool) -> None:
    """Set (or offer to pick) the machine default model used outside a project."""
    if explicit is not None:
        userconfig.set_value("default_model", explicit)
        config.get_settings.cache_clear()
        console.print(f"[green]Set[/]  default model -> {explicit}")
        return
    if config.Settings().default_model:
        console.print(f"[green]Model[/]  default -> {config.Settings().default_model}")
        return
    try:
        models = [m for m in library_ops.scan() if m.generative and not m.is_ollama]
    except Exception:  # noqa: BLE001 - a library scan hiccup shouldn't abort setup
        models = []
    if not models:
        console.print("[dim]Model[/]  no runnable models yet — pull one with `kodo library pull`.")
        return
    if yes:
        console.print("[dim]Model[/]  no default set — `kodo config set model <name>` to pick one.")
        return
    console.print("\nPick a default model to use outside a project [dim](optional)[/]:")
    shown = models[:10]
    for i, m in enumerate(shown, 1):
        console.print(f"  [cyan]{i}[/]  {m.name}")
    raw = typer.prompt("Number (blank to skip)", default="", show_default=False)
    if raw.strip().isdigit() and 1 <= int(raw) <= len(shown):
        chosen = shown[int(raw) - 1].name
        userconfig.set_value("default_model", chosen)
        config.get_settings.cache_clear()
        console.print(f"[green]Set[/]  default model -> {chosen}")


def _setup_ui(build_ui: bool | None, yes: bool) -> None:
    """Build the web UI if it isn't built and bun is available (from a source checkout)."""
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    dist = config.Settings().frontend_dir
    if (dist / "index.html").is_file():
        console.print(f"[green]Web UI[/]  built -> {dist}")
        return
    frontend_src = Path(__file__).resolve().parent.parent.parent / "frontend"
    if not (frontend_src / "package.json").is_file():
        console.print("[dim]Web UI[/]  source not present (packaged install) — skipping.")
        return
    bun = shutil.which("bun")
    if bun is None:
        console.print("[yellow]Web UI[/]  not built — install bun (https://bun.sh), then `make frontend`.")
        return
    do_build = build_ui if build_ui is not None else (yes or typer.confirm("Build the web UI now (bun)?", default=True))
    if not do_build:
        console.print("[dim]Web UI[/]  not built — run `make frontend` when you want it.")
        return
    with console.status("[cyan]Building the web UI (bun)…", spinner="dots"):
        install = subprocess.run([bun, "install"], cwd=frontend_src, capture_output=True, text=True)  # noqa: S603
        built = (
            subprocess.run([bun, "run", "build"], cwd=frontend_src, capture_output=True, text=True)  # noqa: S603
            if install.returncode == 0
            else install
        )
    if built.returncode == 0:
        console.print(f"[green]Web UI[/]  built -> {dist}")
    else:
        console.print("[red]Web UI[/]  build failed — run `make frontend` to see the error.")


def _setup_default_tools(yes: bool) -> None:
    """Seed the global mcp.json with a minimal default toolset (datetime) if it has none."""
    existing = mcpservers.read_global()
    if existing:
        console.print(f"[green]Tools[/]  global default -> {', '.join(s.name for s in existing)}")
        return
    from kodo import plugins  # noqa: PLC0415

    # Minimal, safe default: datetime — models otherwise don't know the current date/time. More
    # via `kodo mcp add --global <name>` (see `kodo mcp list`).
    server = next((s for s in plugins.advertised_servers(plugins.manager()) if s.name == "datetime"), None)
    if server is None:
        return
    if not yes and not typer.confirm("Enable the default 'datetime' tool for chats?", default=True):
        console.print("[dim]Tools[/]  none — add with `kodo mcp add --global <name>`.")
        return
    mcpservers.add(_to_mcp_server(server.name, server.command), glob=True)
    console.print(f"[green]Set[/]  default tools -> {server.name}  [dim](kodo mcp add --global <name> for more)[/]")


@app.command()
def setup(
    library_root: Annotated[
        Path | None, typer.Option("--library-root", help="Set the library location (skips the prompt).")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Set the machine default model.")] = None,
    build_ui: Annotated[
        bool | None, typer.Option("--build-ui/--no-build-ui", help="Build the web UI (default: ask if bun is present).")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Accept defaults without prompting (non-interactive).")
    ] = False,
) -> None:
    """First-run machine setup: configure the library + default model, build the UI, check runtimes.

    The write-mode companion to `kodo doctor`, at machine scope (whereas `kodo project init`
    scaffolds a single project). It persists per-machine defaults to ~/.config/kodo/config.toml,
    builds the browser UI if bun is available, and fixes what it can — printing an OS-specific
    hint for what it can't (installing the llama.cpp binary). Safe to re-run.
    """
    console.rule("[bold]kodo setup")
    _setup_library_root(library_root, yes)
    _setup_default_model(model, yes)
    _setup_default_tools(yes)
    _setup_ui(build_ui, yes)
    console.print()
    report = doctor.run_checks()
    _print_doctor_table(report)
    if report.status is doctor.CheckStatus.fail:
        console.print("\n[yellow]Setup done, but some checks still fail[/] (see above — likely the llama.cpp binary).")
    else:
        console.print("\n[green]Setup complete.[/] Try `kodo chat` or `kodo serve --ui`.")


@config_app.command("path")
def config_path() -> None:
    """Print the machine config file path."""
    console.print(str(userconfig.config_path()))


@config_app.command("show")
def config_show() -> None:
    """Show the machine config file and the effective (resolved) defaults."""
    path = userconfig.config_path()
    stored = userconfig.read()
    console.print(f"[bold]Machine config[/]  {path}" + ("" if path.is_file() else "  [dim](not created yet)[/]"))
    if stored:
        for key, field in userconfig.WRITABLE.items():
            if field in stored:
                console.print(f"  {key} = {stored[field]!r}")
    # The effective values fold in env vars / project kodo.toml / .env that outrank this file.
    settings = config.Settings()
    console.print("\n[bold]Effective[/] [dim](after env / project / .env override)[/]")
    console.print(f"  library-root  {settings.library_root or '[yellow](not set)[/]'}")
    console.print(f"  model         {settings.default_model or '[dim](none — free-play)[/]'}")


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help=f"Config key: {', '.join(userconfig.WRITABLE)}.")],
    value: Annotated[str, typer.Argument(help="The value to store.")],
) -> None:
    """Set a machine default (persisted to the config file), e.g. `kodo config set model <name>`."""
    field = userconfig.WRITABLE.get(key)
    if field is None:
        typer.secho(
            f"Unknown key {key!r}. Known keys: {', '.join(userconfig.WRITABLE)}.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)
    stored = value
    if field == "library_root":  # persist an absolute, ~-expanded path so it resolves from any cwd
        stored = str(Path(value).expanduser().resolve())
    written = userconfig.set_value(field, stored)
    console.print(f"[green]Set[/] {key} = {stored}\n[dim]{written}[/]")


@library_app.command("ls")
def list_models(
    details: Annotated[
        bool,
        typer.Option("--details", "-d", help="Full-detail card per model: caps, context, location, path, files."),
    ] = False,
) -> None:
    """List the models in your library — what you've pulled, ready to run.

    Scans the libraries in scope (a project's ``libraries`` in ``kodo.toml``, else
    the default ``KODO_LIBRARY_ROOT``). To browse models in your app caches that you
    *could* pull, use ``kodo library sources``. Pass ``--details`` (``-d``) for a
    stacked card per model with its full detail.
    """
    settings = get_settings()
    all_models = library_ops.scan()
    models = [m for m in all_models if m.generative]  # chat LLMs
    voices = [m for m in all_models if m.voice_kind]  # TTS/STT voice models
    lib_roots = library_ops.roots(settings)
    missing = [r for r in lib_roots if not r.is_dir()]
    if not models and not voices:
        console.print("Your library is empty.")
        if missing:
            console.print(f"[yellow]Library not mounted:[/] [dim]{', '.join(str(r) for r in missing)}[/]")
        console.print("[dim]Pull one with[/] kodo library pull [dim]· browse with[/] kodo library sources")
        return

    total = _human_size(sum(m.size_bytes for m in all_models))
    voice_note = f" · [magenta]{len(voices)} voice[/]" if voices else ""
    console.print(f"\n[bold]{len(models)} models{voice_note} · {total}[/] in your library\n")
    if missing:
        console.print(f"[yellow]Note:[/] not mounted: [dim]{', '.join(str(r) for r in missing)}[/]\n")
    if details:  # full-detail cards, one per model, stacked and grouped by format
        tag_maps: dict[Path, dict[str, list[str]]] = {}  # cache tags.json per library
        for fmt in sorted({m.model_format for m in models}, key=lambda f: f.value):
            for m in sorted((m for m in models if m.model_format is fmt), key=lambda m: m.name):
                tag_maps.setdefault(m.library_root, tags.load(m.library_root))
                _print_model_card(m, tag_maps[m.library_root].get(m.name, []))
        for m in sorted(voices, key=lambda m: (m.voice_kind, m.name)):
            tag_maps.setdefault(m.library_root, tags.load(m.library_root))
            _print_model_card(m, tag_maps[m.library_root].get(m.name, []))
        return
    for fmt in sorted({m.model_format for m in models}, key=lambda f: f.value):
        rows = sorted((m for m in models if m.model_format is fmt), key=lambda m: m.name)
        subtotal = _human_size(sum(m.size_bytes for m in rows))
        title = f"[{_FORMAT_STYLE[fmt]}][bold]{fmt.value}[/][/]  [dim]{len(rows)} · {subtotal}[/]"
        table = Table(box=box.SIMPLE_HEAD, title=title, title_justify="left", pad_edge=False)
        table.add_column("SIZE", justify="right")
        table.add_column("CAPS")
        table.add_column("CTX", justify="right")
        table.add_column("NAME", style="white")
        for m in rows:
            try:
                caps = capabilities.capabilities(m)
            except Exception:  # noqa: BLE001 - detection is best-effort; never break the listing
                caps = None
            table.add_row(m.size_human, _caps_label(caps), _fmt_ctx(caps.context_length if caps else None), m.name)
        console.print(table)

    if voices:  # the Voice category (TTS/STT) — a peer of the format groups
        subtotal = _human_size(sum(m.size_bytes for m in voices))
        vt = Table(
            box=box.SIMPLE_HEAD,
            title=f"[magenta][bold]voice[/][/]  [dim]{len(voices)} · {subtotal}[/]",
            title_justify="left",
            pad_edge=False,
        )
        vt.add_column("SIZE", justify="right")
        vt.add_column("KIND")
        vt.add_column("NAME", style="white")
        for m in sorted(voices, key=lambda m: (m.voice_kind, m.name)):
            kind = "[cyan]tts[/]" if m.voice_kind == "tts" else "[green]stt[/]"
            vt.add_row(m.size_human, kind, m.name)
        console.print(vt)
        console.print("[dim]Voice-specific ops:[/] kodo voice ls / import")


@library_app.command("rm")
def remove_model(
    name: Annotated[str, typer.Argument(help="Library model to remove (full name or bare repo/tag).")],
    model_format: FormatOption = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove a model from the library — deletes its files from disk.

    Resolves like ``kodo chat`` (use ``--format`` to disambiguate a model kept in
    more than one format). Ollama models keep any blobs still shared with other
    installed models.
    """
    copies = library_ops.find_copies(name, model_format=model_format)
    if not copies:
        typer.secho(f"No library model matches {name!r} (see `kodo library ls`).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    # Ambiguity is per distinct (name, format); multiple *copies* of the same model
    # (e.g. kept on both the local disk and the drive) are all removed together.
    distinct = {(m.name, m.model_format) for m in copies}
    if len(distinct) > 1:
        fmts = ", ".join(sorted({m.model_format.value for m in copies}))
        typer.secho(f"{name!r} is ambiguous across formats ({fmts}) — pass --format.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    model = copies[0]
    plural = f" ({len(copies)} copies)" if len(copies) > 1 else ""
    console.print(f"\n[bold]Remove[/] {model.name}  [dim]({model.model_format.value} · {model.size_human}){plural}[/]")
    for m in copies:
        console.print(f"  [dim]{m.path}[/]")
    if not yes and not typer.confirm("Delete these files?", default=False):
        console.print("[dim]Aborted.[/]")
        raise typer.Exit(0)
    total_files, total_freed, failed = 0, 0, 0
    for m in copies:
        count, freed = library_ops.remove(m)
        total_files += count
        total_freed += freed
        if count == 0 and m.file_count > 0:  # rmtree couldn't delete it (read-only drive, files in use)
            failed += 1
    if failed:
        console.print(
            f"[red]Could not remove {failed} of {len(copies)} copies[/] of {model.name} "
            "(read-only drive, or files held open by a running server?)."
        )
        if total_files:
            console.print(
                f"[green]Removed[/] the rest — freed [bold]{_human_size(total_freed)}[/] ({total_files} files)"
            )
        raise typer.Exit(1)
    console.print(f"[green]Removed[/] {model.name} — freed [bold]{_human_size(total_freed)}[/] ({total_files} files)")


@library_app.command("migrate")
def migrate(
    apply: Annotated[
        bool, typer.Option("--apply", help="Actually reorganize (default: a dry-run showing the plan).")
    ] = False,
) -> None:
    """Reorganize old ``huggingface/`` pulls into the format-centric layout (gguf/mlx/safetensors).

    Older Hugging Face pulls landed in ``huggingface/<repo>``; kodo now stores by format. This
    moves each into its bucket (a same-drive rename), and removes any that are already duplicated
    in a bucket. Dry-run by default — pass ``--apply`` to make the changes.
    """
    any_plan = False
    total_moved = total_deduped = total_freed = 0
    for root in library_ops.roots():
        actions = library_ops.plan_migration(root)
        if not actions:
            continue
        any_plan = True
        console.print(f"\n[bold]{root}[/]")
        for a in actions:
            if a.kind == "move":
                console.print(
                    f"  [cyan]move[/]  huggingface/{a.repo_id}  [dim]→[/]  {a.model_format.value}/{a.repo_id}"
                )
            else:
                console.print(
                    f"  [yellow]dedup[/] huggingface/{a.repo_id}  [dim]— already in {a.model_format.value}/,"
                    f" remove ({_human_size(a.size_bytes)})[/]"
                )
        if apply:
            moved, deduped, freed = library_ops.apply_migration(actions)
            total_moved += moved
            total_deduped += deduped
            total_freed += freed

    if not any_plan:
        console.print("[green]Nothing to migrate[/] — no old huggingface/ pulls with recognizable weights.")
        return
    if apply:
        console.print(
            f"\n[green]Done[/] — moved [bold]{total_moved}[/], deduped [bold]{total_deduped}[/] "
            f"(freed {_human_size(total_freed)})."
        )
    else:
        console.print("\n[dim]Dry run.[/] Re-run with [bold]--apply[/] to make these changes.")


@library_app.command("install")
def install(
    model: Annotated[str, typer.Argument(help="Library model name, e.g. Qwen3.5-4B-GGUF.")],
    to: Annotated[str, typer.Option("--to", help="Target runtime: ollama | lmstudio.")] = "ollama",
    model_format: FormatOption = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Ollama: name to register under (default: a sanitized repo tail).")
    ] = None,
    system: Annotated[
        str | None, typer.Option("--system", help="Ollama: default system prompt to bake into the model.")
    ] = None,
) -> None:
    """Install a canonical library model into a runtime (feed a *consumer*).

    The library keeps one canonical copy; this points a runtime at it. ``--to ollama``
    imports the GGUF into a running Ollama (``ollama create``); ``--to lmstudio`` symlinks
    the GGUF/MLX model into LM Studio's models dir (zero copy). Either way the drive stays
    the single source of truth.
    """
    if to not in ("ollama", "lmstudio"):
        console.print(f"[red]Unsupported target {to!r}[/] — use [bold]ollama[/] or [bold]lmstudio[/].")
        raise typer.Exit(1)
    # Ollama only imports GGUF; LM Studio takes GGUF or MLX (use --format to disambiguate).
    if to == "ollama" and model_format is not None and model_format is not ModelFormat.gguf:
        console.print(
            f"[red]Ollama imports GGUF only[/] — drop `--format {model_format.value}` or use `--to lmstudio`."
        )
        raise typer.Exit(1)
    resolved = _resolve_library_model(model, ModelFormat.gguf if to == "ollama" else model_format)
    try:
        if to == "ollama":
            result = consumers.install_ollama(resolved, name=name, system=system)
        else:
            result = consumers.install_lmstudio(resolved)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    if to == "ollama":
        console.print(
            f"[green]Installed[/] {resolved.name} [dim]→[/] ollama [bold]{result.name}[/].\n"
            f"[dim]Run it with[/]  ollama run {result.name}"
        )
    else:
        console.print(
            f"[green]Linked[/] {resolved.name} [dim]→[/] LM Studio [dim]({result.detail}).[/]\n"
            "[dim]Rescan/restart LM Studio if it doesn't appear.[/]"
        )


@library_app.command("uninstall")
def uninstall(
    model: Annotated[str, typer.Argument(help="Library model name (or the Ollama name, for --from ollama).")],
    from_: Annotated[str, typer.Option("--from", help="Runtime to remove it from: ollama | lmstudio.")] = "ollama",
    model_format: FormatOption = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Ollama: the registered name to remove (default: derived from model).")
    ] = None,
) -> None:
    """Remove a model from a runtime (undo `kodo library install`). The library copy is kept.

    ``--from lmstudio`` removes only kodo's symlink (never a real LM Studio download); ``--from
    ollama`` runs ``ollama rm`` on the registered name (derived from the model, or ``--name``).
    """
    if from_ not in ("ollama", "lmstudio"):
        console.print(f"[red]Unsupported runtime {from_!r}[/] — use [bold]ollama[/] or [bold]lmstudio[/].")
        raise typer.Exit(1)
    try:
        if from_ == "ollama":
            # Don't require the model to still be in the library — you may want to drop the Ollama
            # copy of a model you've already removed from the drive.
            result = consumers.uninstall_ollama(name or consumers.ollama_name(model))
        else:
            resolved = _resolve_library_model(model, model_format)
            result = consumers.uninstall_lmstudio(resolved)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Removed[/] [bold]{result.name}[/] from {from_} [dim]({result.detail}); library copy kept.[/]"
    )


@library_app.command("installed")
def installed() -> None:
    """Show which runtimes each library model is currently installed into (Ollama / LM Studio).

    A read-only cross-reference: for each model on the drive, whether Ollama holds an import of it
    and whether LM Studio has a kodo symlink to it. Undo any of these with `kodo library uninstall`.
    """
    library_ops.roots()  # fail fast + clean if no library is configured
    models = library_ops.scan()
    ollama_names = consumers.ollama_installed_names()
    linked = consumers.lmstudio_linked_names(library_ops.roots())

    lines: list[str] = []
    for m in sorted(models, key=lambda x: x.name.lower()):
        targets = []
        # Ollama imports GGUF only; a match is our deterministic install name being present.
        if m.model_format is ModelFormat.gguf and not m.is_ollama and consumers.ollama_name(m.name) in ollama_names:
            targets.append(f"ollama [dim]({consumers.ollama_name(m.name)})[/]")
        if m.name in linked:
            targets.append("lmstudio")
        if targets:
            lines.append(f"  [cyan]{m.name}[/] [dim]→[/] {', '.join(targets)}")

    if not lines:
        console.print(
            "No library models are installed into a runtime yet.\n"
            "[dim]Feed one to a runtime with[/] kodo library install <model> --to ollama|lmstudio"
        )
        return
    console.print("[bold]Installed into runtimes[/] [dim](library keeps the canonical copy)[/]")
    for line in lines:
        console.print(line)


@library_app.command("cards")
def cards_backfill(
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-fetch even models that already have a card.")] = False,
) -> None:
    """Backfill missing Hugging Face model cards into library models' ``.kodo/`` sidecars.

    A model's card is its README — the docs the UI/CLI info panel shows. Some LM Studio downloads
    (and older pulls) ship without one; this infers the HF repo from each model's
    ``<publisher>/<repo>`` name and fetches its README. Ollama models are skipped (their card is
    generated from the manifest). Best-effort: a model that isn't on HF is reported, not fatal.
    """
    library_ops.roots()  # fail fast + clean if no library is configured
    token = get_settings().hf_token
    fetched = skipped = unavailable = 0
    for m in library_ops.scan():
        if m.is_ollama:
            continue  # Ollama cards are generated from the manifest, not fetched
        if not refresh and cards.has_card(m.path):
            skipped += 1
            continue
        text = cards.fetch_hf_readme(m.name, token)
        if text is None:
            console.print(f"  [yellow]no card[/] {m.name} [dim](not on HF, or no README)[/]")
            unavailable += 1
            continue
        cards.write_card(m.path / cards.SIDECAR_DIR, text)
        console.print(f"  [green]card[/] {m.name}")
        fetched += 1
    console.print(f"\n[bold]{fetched}[/] fetched, [dim]{skipped} already had one, {unavailable} unavailable.[/]")


@library_app.command("verify")
def verify_library(
    query: Annotated[str | None, typer.Argument(help="Model to verify (full name or bare tail); omit for all.")] = None,
    deep: Annotated[
        bool, typer.Option("--deep", help="Re-hash Ollama blobs against their sha256 (slow; true content integrity).")
    ] = False,
) -> None:
    """Check library models on disk against their recorded metadata.

    Verifies each model's total size, file count, and card against its ``.kodo/metadata.json``
    (catches truncated/incomplete pulls or deleted files). Ollama models are content-addressed,
    so their blobs are checked for existence — and with ``--deep``, re-hashed against their sha256.
    """
    models = library_ops.find(query) if query else library_ops.scan()
    if not models:
        console.print(f"[yellow]No models to verify[/]{f' matching {query!r}' if query else ''}.")
        raise typer.Exit(1 if query else 0)

    table = Table(box=box.SIMPLE, header_style="bold")
    table.add_column("", justify="center")
    table.add_column("MODEL", style="cyan")
    table.add_column("CHECKED", style="dim")
    table.add_column("ISSUES")
    ok_count = 0
    for m in sorted(models, key=lambda x: x.name):
        r = library_ops.verify(m, deep=deep)
        ok_count += r.ok
        mark = "[green]✓[/]" if r.ok else "[red]✗[/]"
        table.add_row(mark, m.name, r.checked, "[green]ok[/]" if r.ok else f"[red]{'; '.join(r.issues)}[/]")
    console.print(table)
    bad = len(models) - ok_count
    console.print(f"\n[bold]{ok_count}/{len(models)} ok[/]" + (f" · [red]{bad} with issues[/]" if bad else ""))
    if bad:
        raise typer.Exit(1)


@library_app.command("tag")
def tag_model(
    name: Annotated[str, typer.Argument(help="Library model (full name or bare repo/tag).")],
    model_format: FormatOption = None,
    add: Annotated[list[str], typer.Option("--add", "-a", help="Tag(s) to add (repeatable).")] = [],
    remove: Annotated[list[str], typer.Option("--remove", "-r", help="Tag(s) to remove (repeatable).")] = [],
    clear: Annotated[bool, typer.Option("--clear", help="Remove all tags from the model.")] = False,
) -> None:
    """Add, remove, or list a model's user tags (tested, favorite, coding, ...).

    Tags are *your* labels, separate from the auto-detected vision/audio/tools
    capabilities. With no ``--add``/``--remove``/``--clear`` the current tags are
    just listed. They're stored inside the model's own library, so they travel with
    it, and show up as filter chips in the web Models view.
    """
    model = _resolve_library_model(name, model_format)
    root = model.library_root
    if clear:
        result = tags.set_tags(root, model.name, [])
    elif add or remove:
        result = tags.edit_tags(root, model.name, add, remove)
    else:
        result = tags.tags_for(root, model.name)
    label = "  ".join(f"[cyan]{t}[/]" for t in result) if result else "[dim](none)[/]"
    console.print(f"[white]{model.name}[/]\n  [dim]tags[/]  {label}")


@library_app.command("tag-style")
def tag_style(
    tag: Annotated[str, typer.Argument(help="Tag name to style (e.g. tested, coding).")],
    color: Annotated[str | None, typer.Option("--color", "-c", help="Hex color for the tag, e.g. '#22c55e'.")] = None,
    icon: Annotated[str | None, typer.Option("--icon", "-i", help="A short glyph/emoji for the tag.")] = None,
    description: Annotated[str | None, typer.Option("--description", "-d", help="Optional description.")] = None,
) -> None:
    """Give a tag a first-class color / icon (a per-library tag registry).

    Assignments stay plain name references (``kodo library tag``); this stores the tag's *style*
    once, keyed by name, so ``coding`` looks the same on every model. The web UI prefers a
    registered color over the name-derived one. With no options, prints the tag's current style.
    """
    if color is not None and not tags.valid_color(color):
        console.print(f"[red]{color!r} is not a hex color[/] — use '#rgb' or '#rrggbb' (e.g. '#22c55e').")
        raise typer.Exit(1)
    root = library_ops.roots()[0]  # the primary in-scope library (project-local or shared)
    key = tags.normalize(tag)
    meta = (
        tags.set_tag_meta(root, tag, color=color, icon=icon, description=description)
        if (color is not None or icon is not None or description is not None)
        else tags.load_registry(root).get(key, tags.TagMeta())
    )
    style = "  ".join(
        f"[dim]{f}[/] {v}" for f, v in (("color", meta.color), ("icon", meta.icon), ("desc", meta.description)) if v
    )
    console.print(f"[cyan]{key}[/]\n  {style or '[dim](no style set)[/]'}")


def _pull_voice_all(root: Path | None, move: bool) -> None:
    """Import every registry voice model already in the HF cache into the target library.

    Mirrors the old ``kodo voice import --all`` (no mass downloads): only cached models are
    pulled. Fetch a not-yet-downloaded one by name, e.g. ``kodo library pull voice kokoro``.
    """
    from kodo.voice import importer as voice_importer  # noqa: PLC0415

    target = root or library_ops.default_root()
    ids = voice_importer.cached_voice_ids(target)
    if not ids:
        console.print("No cached voice models to import. Pull one by name, e.g. kodo library pull voice kokoro.")
        return
    failed = 0
    for vid in ids:
        try:
            result = catalog_ops.pull(ModelSource.voice, vid, library_root=root, move=move)
        except Exception as exc:  # noqa: BLE001 - one bad model must not abort the batch
            failed += 1
            console.print(f"[red]✗ fail[/] {vid} [dim]— {exc}[/]")
            continue
        console.print(f"[green]✓ pull[/] {vid} [dim]({result.size_human})[/]")
    console.print(f"\n[bold]{len(ids) - failed} imported[/] · {failed} failed")
    if failed:
        raise typer.Exit(1)


def _pull_all(source: ModelSource, root: Path | None, move: bool) -> None:
    """Import every model from ``source``'s local store into the library.

    Idempotent (skips models already in the library) and resilient (one failing
    model logs and the batch continues).
    """
    if source is ModelSource.voice:
        _pull_voice_all(root, move)
        return
    entries = catalog_ops.list_models(source).entries
    if not entries:
        console.print(f"No {source.value} models found in the local store.")
        return

    # Skip on exact identity only: a source model imports to the same library name,
    # so bare-name aliasing (which would wrongly skip bob/Foo when alice/Foo exists)
    # must not be used here.
    lib_names = {m.name.lower() for m in library_ops.scan()}

    def in_library(nm: str) -> bool:
        return nm.lower() in lib_names

    imported = skipped = failed = 0
    for entry in sorted(entries, key=lambda e: e.name):
        if in_library(entry.name):
            skipped += 1
            console.print(f"[dim]— skip[/] {entry.name} [dim](already in library)[/]")
            continue
        try:
            result = catalog_ops.pull(source, entry.name, library_root=root, move=move)
        except Exception as exc:  # noqa: BLE001 - one bad model must not abort the batch
            failed += 1
            console.print(f"[red]✗ fail[/] {entry.name} [dim]— {exc}[/]")
            continue
        imported += 1
        console.print(f"[green]✓ pull[/] {entry.name} [dim]({result.size_human})[/]")

    console.print(f"\n[bold]{imported} imported[/] · {skipped} already in library · {failed} failed")
    if failed:
        raise typer.Exit(1)


@library_app.command()
def pull(
    source: Annotated[ModelSource, typer.Argument(help="Source the model belongs to.")],
    name: Annotated[
        str | None,
        typer.Argument(
            help="Model id (HF repo, Ollama model:tag, LM Studio path, or voice id e.g. kokoro). Omit with --all."
        ),
    ] = None,
    all_: Annotated[
        bool,
        typer.Option("--all", "-a", help="Import every model from this source's local store (idempotent)."),
    ] = False,
    move: Annotated[
        bool,
        typer.Option("--move", help="Delete the local source after a verified copy (frees local disk)."),
    ] = False,
    shared: Annotated[
        bool,
        typer.Option("--shared", help="Pull into the shared/default library instead of the project-local one."),
    ] = False,
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            help="HF only: filename glob(s) to fetch, e.g. --include '*Q4_K_M*' to grab one GGUF quant. Repeatable.",
        ),
    ] = None,
    vocoder: Annotated[
        str | None,
        typer.Option(
            "--vocoder",
            help="HF only: a vocoder repo to co-locate as a TTS model, e.g. --vocoder ggml-org/WavTokenizer.",
        ),
    ] = None,
) -> None:
    """Pull (or move) a model into a library.

    Targets the project-local library when you're in a project, else the default
    library; ``--shared`` forces the shared/default library. Give a model name for
    one model, or --all to import everything from this source's local store
    (skipping models already in the library).

    The ``voice`` source pulls a TTS/STT model by its registry id (e.g.
    ``kodo library pull voice kokoro``) into ``<root>/voice/`` — downloading it if it
    isn't already in the Hugging Face cache. This is the project-aware way to add a
    voice model (``kodo voice import`` is the older cache-only alias).
    """
    # Default target: the first library in scope (project-local if any, else the
    # default). --shared forces the machine's default (shared) library.
    root = library_ops.default_root() if shared else library_ops.roots()[0]
    if all_ == (name is not None):
        console.print("[red]Provide either a model name or --all, not both.[/]")
        raise typer.Exit(2)

    if all_:
        _pull_all(source, root, move)
        return

    assert name is not None  # narrowed by the guard above
    verb = "Moving" if move else "Pulling"
    typer.echo(f"{verb} {source.value}:{name} -> {root} ...")
    try:
        result = catalog_ops.pull(source, name, library_root=root, move=move, include=include, vocoder=vocoder)
    except Exception as exc:  # noqa: BLE001 - a pull can fail many ways (disk, network, HF Hub); surface it cleanly
        typer.secho(f"Pull failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if move and not result.source_removed:
        suffix = " [yellow](local copy KEPT — copy could not be verified)[/]"
    elif result.source_removed:
        suffix = " (local copy removed)"
    else:
        suffix = ""
    console.print(f"Done: {result.file_count} files, {result.size_human} -> {result.destination}{suffix}")


@library_app.command()
def sources(
    source: SourceOption = None,
    show_all: Annotated[bool, typer.Option("--all", "-a", help="Include embedding/vision/partial entries.")] = False,
) -> None:
    """Browse models in your app caches (HF / Ollama / LM Studio) you can pull.

    These live in caches on this machine (e.g. ~/.cache/huggingface) — *not* your
    library. The IN LIBRARY column marks what you've already pulled; pull leftover
    local models onto the drive with ``kodo library pull --move`` to free local disk.
    Non-chat (embedding/vision) and partial entries are hidden unless ``--all``.
    """
    entries = catalog_ops.list_models(source).entries
    chat_models = [e for e in entries if e.generative]
    hidden = len(entries) - len(chat_models)
    shown = entries if show_all else chat_models
    if not shown:
        console.print("No chat models found in local app caches.")
        if hidden:
            console.print(f"[dim]({hidden} embedding / vision / partial entries — use --all to see them)[/]")
        return

    lib = _library_names()

    def in_library(name: str) -> bool:
        return name.lower() in lib or name.rsplit("/", 1)[-1].lower() in lib

    pulled = sum(1 for e in shown if in_library(e.name))
    shown_total = _human_size(sum(e.size_bytes for e in shown))
    console.print(
        f"\n[bold]{len(shown)} models · {shown_total}[/] in local app caches "
        f"[dim]· {pulled} already in your library · {len(shown) - pulled} to pull[/]"
    )
    console.print("[dim]Caches on this machine, not your library — see[/] kodo library ls [dim]for your library.[/]\n")
    for src in sorted({e.source for e in shown}, key=lambda s: s.value):
        rows = sorted((e for e in shown if e.source is src), key=lambda e: e.name)
        table = Table(box=box.SIMPLE_HEAD, title=f"[bold]{src.value}[/]", title_justify="left", pad_edge=False)
        table.add_column("IN LIBRARY", justify="center")
        table.add_column("FORMAT")
        table.add_column("SIZE", justify="right")
        table.add_column("NAME", style="white")
        if show_all:
            table.add_column("CHAT?", justify="center")
        for e in rows:
            mark = "[green]✓[/]" if in_library(e.name) else "[dim]—[/]"
            extra = (["[green]chat[/]" if e.generative else "[dim]no[/]"]) if show_all else []
            table.add_row(mark, _fmt_cell(e.model_format), e.size_human, e.name, *extra)
        console.print(table)
    if hidden and not show_all:
        console.print(
            f"\n[dim]{hidden} non-chat (embedding/vision) or partial entries hidden — use --all to show them.[/]"
        )


def _pick_model_interactive() -> str:
    """Pick the project's default model: a chat model already in the library, or a starter to pull."""
    lib = [m for m in (library_ops.scan() if library_ops.configured() else []) if m.generative and not m.is_ollama]
    console.print("\n[bold]1. Default model[/] [dim]— what this project loads[/]")
    options: list[str] = []
    for m in sorted(lib, key=lambda m: m.name):
        options.append(m.name)
        console.print(f"  {len(options)}. {m.name}  [dim]{m.model_format.value} · {m.size_human}[/]")
    if lib:
        console.print("  [dim]— or pull a starter —[/]")
    for c in _CURATED:
        options.append(c.id)
        console.print(f"  {len(options)}. {c.id}  [dim]pull · {c.note}[/]")
    choice = typer.prompt("Number", default="1")
    try:
        return options[int(choice) - 1]
    except (ValueError, IndexError):
        console.print(f"[red]Not a valid choice: {choice!r}[/]")
        raise typer.Exit(1) from None


def _pick_tools_interactive() -> list[tuple[str, str]]:
    """Multi-select MCP servers (from installed plugins) for the project's toolset."""
    from kodo import plugins  # noqa: PLC0415

    servers = plugins.advertised_servers(plugins.manager())
    if not servers:
        console.print("\n[bold]2. Tools[/] [dim]— no MCP plugins installed; skipping (add later in kodo.toml)[/]")
        return []
    console.print("\n[bold]2. Tools[/] [dim]— MCP servers this assistant can call[/]")
    for i, s in enumerate(servers, 1):
        console.print(f"  {i}. {s.name}  [dim]{s.description}[/]")
    raw = typer.prompt("Select (comma-separated numbers, 'all', or blank for none)", default="").strip().lower()
    if not raw:
        return []
    if raw == "all":
        chosen = list(servers)
    else:
        chosen = [servers[int(p) - 1] for p in raw.split(",") if p.strip().isdigit() and 0 < int(p) <= len(servers)]
    return [(s.name, s.command) for s in chosen]


def _pull_or_exit(model: str, library_root: Path | None) -> None:
    """Pull ``model`` from Hugging Face (into ``library_root`` or the shared library), or exit."""
    try:
        if library_root is None:
            catalog_ops.pull(ModelSource.huggingface, model)
        else:
            catalog_ops.pull(ModelSource.huggingface, model, library_root=library_root)
    except Exception as exc:  # noqa: BLE001 - surface pull/network failures
        console.print(f"[red]Pull failed:[/] {exc}")
        raise typer.Exit(1) from exc


def _scaffold_project(
    target: Path,
    model: str | None,
    force: bool,
    local: bool,
    git: bool = False,
    uv: bool = True,
    template: str | None = None,
) -> None:
    """Interactive project scaffolder shared by `init` (here) and `new` (a fresh dir).

    Walks through a kind, default model, toolset (installed MCP plugins), system prompt, and
    a spoken-reply voice, then writes ``target/kodo.toml``. By default it uses your machine
    library (KODO_LIBRARY_ROOT), pulling a missing model into it. ``local`` (``--local``/
    ``--copy``) instead builds a project-local ``library/`` — copying the model from the
    shared library if it's there (a fast local-disk copy), else downloading it there. With no
    KODO_LIBRARY_ROOT set it falls back to that project-local library so you can start fresh.

    ``uv`` (default on) also makes the project a **self-contained uv project**: a
    ``pyproject.toml`` pinning ``kodo`` + its MCP servers, so ``uv run kodo serve`` uses the
    project's own environment instead of a global kodo and runtime ``uvx`` fetches.
    """
    proj = target / "kodo.toml"
    if proj.exists() and not force:
        console.print(f"[red]{proj} already exists[/] — use --force to overwrite.")
        raise typer.Exit(1)

    tmpl = None
    if template is not None:
        tmpl = TEMPLATES.get(template)
        if tmpl is None:
            console.print(f"[red]Unknown template {template!r}[/] — available: {', '.join(sorted(TEMPLATES))}")
            raise typer.Exit(1)

    if tmpl is not None:
        # A template presets the whole wizard, so scaffolding is reproducible in one command.
        console.print(f"\nUsing the [bold]{template}[/] template.")
        model = model or tmpl.model
        mcp = list(tmpl.mcp)
        system_prompt = tmpl.system_prompt
        chat_voice = tmpl.chat_voice or "kokoro:af_heart"
    else:
        # Gather every choice first, so canceling the wizard (Ctrl-C) leaves nothing behind —
        # the directory is created only once we're committing to writing the project below.
        console.print("\n[bold]0. Kind[/] [dim]— tailors the defaults[/]")
        console.print("  1. Chat  [dim]— text conversation (replies can still be spoken)[/]")
        console.print("  2. Voice  [dim]— talk to it: mic dictation in, spoken replies out[/]")
        voice_project = typer.prompt("Number", default="1").strip() == "2"

        model = model or _pick_model_interactive()
        mcp = _pick_tools_interactive()
        default_prompt = (
            "You are a friendly voice assistant. Keep replies short and natural for speech."
            if voice_project
            else "You are a concise, helpful assistant."
        )
        system_prompt = typer.prompt("\n3. System prompt", default=default_prompt)
        # Kokoro is tiny, so every project can speak replies; a voice project lets you pick which voice.
        chat_voice = (
            typer.prompt("\n4. Spoken-reply voice (Kokoro)", default="kokoro:af_heart")
            if voice_project
            else "kokoro:af_heart"
        )

    target.mkdir(parents=True, exist_ok=True)
    shared = library_ops.configured()
    existing = library_ops.find(model) if shared else []
    use_local = local or not shared  # --local/--copy forces it; no KODO_LIBRARY_ROOT falls back to it
    if not use_local:
        if existing:
            console.print(f"\n[green]✓[/] {model} is in your library")
        else:
            console.print(f"\nPulling [bold]{model}[/] into your library …")
            _pull_or_exit(model, None)
    else:
        local_lib = target / _LOCAL_LIBRARY
        local_lib.mkdir(parents=True, exist_ok=True)
        if existing and existing[0].is_ollama:
            # Ollama models live in a content-addressed store (manifest + shared blobs); they can't
            # be copied into a loose project-local library. Reject cleanly instead of crashing on copytree.
            console.print(
                f"[red]{model!r} is an Ollama model[/] — it can't be copied into a project-local library.\n"
                "Drop `--local`/`--copy` (use the shared library), or pick a GGUF/MLX model."
            )
            raise typer.Exit(1)
        if existing:
            console.print(f"\nCopying [bold]{model}[/] into [bold]{_LOCAL_LIBRARY}/[/] (local-disk copy) …")
            scaffold.copy_model_local(existing[0], local_lib)
        else:
            if not shared:
                console.print(
                    f"\n[yellow]No KODO_LIBRARY_ROOT set[/] — downloading [bold]{model}[/] into {_LOCAL_LIBRARY}/."
                )
            else:
                console.print(f"\nDownloading [bold]{model}[/] into {_LOCAL_LIBRARY}/ …")
            _pull_or_exit(model, local_lib)

    proj.write_text(
        project.render_manifest(
            model=model,
            system_prompt=system_prompt,
            local_library_dir=_LOCAL_LIBRARY if use_local else None,
            chat_voice=chat_voice,
        )
    )
    # Tools go in the standard .mcp.json (not kodo.toml). In a uv project the servers are pinned
    # deps that run straight off PATH, so drop the runtime `uvx ` fetch from each command.
    scaffold_mcp = [(name, scaffold.strip_uvx(command)) for name, command in mcp] if uv else mcp
    for entry_name, command in scaffold_mcp:
        mcpservers.add(_to_mcp_server(entry_name, command), glob=False, project_dir=target)

    if uv:
        mlx = "mlx" in model.lower() or bool(existing and existing[0].model_format == "mlx")
        # Pass the original (uvx-bearing) mcp so pip deps are extracted before uvx is stripped.
        tmpl_extras = tmpl.extras if tmpl is not None else []
        (target / "pyproject.toml").write_text(scaffold.render_pyproject(target.resolve().name, mcp, mlx, tmpl_extras))
        (target / "README.md").write_text(scaffold.render_readme(target.resolve().name))

    if tmpl is not None:
        for rel, content in tmpl.files.items():
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)

    console.print(f"\n[green]Created[/] {proj}")
    console.print(f"  [dim]model:[/] {model}")
    console.print(f"  [dim]tools:[/] {', '.join(n for n, _ in mcp) if mcp else 'none'}")
    console.print(f"  [dim]voice:[/] {chat_voice}")
    if uv:
        console.print("  [dim]uv:[/] pyproject.toml (run [bold]uv sync[/] to build the environment)")
    if git:
        ok, status = scaffold.git_init(target)
        console.print(f"  [{'dim' if ok else 'yellow'}]git:[/] {status}")
    if tmpl is not None and tmpl.next_steps:
        console.print(f"\n[bold]Next steps[/]\n{tmpl.next_steps}")


_ModelOpt = Annotated[str | None, typer.Option("--model", help="Model to bind (skips the model picker).")]
_ForceOpt = Annotated[bool, typer.Option("--force", help="Overwrite an existing kodo.toml.")]
_LocalOpt = Annotated[
    bool,
    typer.Option("--local", "--copy", help="Copy the model into a project-local library/ (fast local disk)."),
]
_GitOpt = Annotated[
    bool,
    typer.Option("--git", help="git init the project + write a .gitignore (excludes the local library/ + .env)."),
]
_UvOpt = Annotated[
    bool,
    typer.Option("--uv/--no-uv", help="Make it a self-contained uv project (pyproject.toml pinning kodo + tools)."),
]
_TemplateOpt = Annotated[
    str | None,
    typer.Option("--template", "-t", help="Start from a preset (e.g. 'dhis2'): model + prompt + tools + files."),
]


@project_app.command("init")
def init(
    model: _ModelOpt = None,
    force: _ForceOpt = False,
    local: _LocalOpt = False,
    git: _GitOpt = False,
    uv: _UvOpt = True,
    template: _TemplateOpt = None,
) -> None:
    """Scaffold a project assistant in the current directory (interactive wizard).

    A project is a purpose-built assistant: `kodo serve`/`chat` here bind to its model
    (like --model) with its tools + prompt; outside a project it's free-play. Use
    `kodo project new <dir>` to scaffold into a fresh directory instead. `--template dhis2`
    presets a reproducible DHIS2 assistant (model + prompt + bridge + example files).
    """
    _scaffold_project(Path("."), model, force, local, git, uv, template)
    console.print(f"[dim]Next:[/] {'uv sync && uv run kodo serve --ui' if uv else 'kodo serve --ui'}")


@project_app.command("new")
def new(
    name: Annotated[str, typer.Argument(help="New project directory to create + scaffold.")],
    model: _ModelOpt = None,
    force: _ForceOpt = False,
    local: _LocalOpt = False,
    git: _GitOpt = False,
    uv: _UvOpt = True,
    template: _TemplateOpt = None,
) -> None:
    """Create a new project directory and scaffold an assistant in it (like `cargo new`).

    `--template dhis2` presets a reproducible DHIS2 assistant (model + prompt + bridge + files);
    pair with `--copy` to bundle the model and `--git` to init a repo.
    """
    _scaffold_project(Path(name), model, force, local, git, uv, template)
    console.print(f"[dim]Next:[/] cd {name} && {'uv sync && uv run kodo serve --ui' if uv else 'kodo serve --ui'}")


def _connect_project_tools(
    mcp: list[mcpservers.McpServer],
) -> tuple[dict[str, list[tuple[str, str]]], str | None, list[tuple[str, str]]]:
    """Spawn the given MCP servers and return their real tools, grouped by server.

    Returns ``({server: [(tool, description), ...]}, error, failures)``: ``error`` is a message
    if the whole connect failed, else ``None``; ``failures`` is per-server ``(label, reason)``
    for servers that couldn't start (e.g. an uninstalled optional server) — the rest still work.
    """
    import asyncio  # noqa: PLC0415

    from kodo import tools as mcp_tools  # noqa: PLC0415

    servers = [m.to_spec() for m in mcp]

    async def _collect() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        async with mcp_tools.connect(servers) as toolset:
            pairs = [(s["function"]["name"], s["function"].get("description", "") or "") for s in toolset.schemas]
            return pairs, toolset.errors

    try:
        pairs, failures = asyncio.run(_collect())
    except Exception as exc:  # noqa: BLE001 - a missing/failing MCP server shouldn't crash `show`
        return {}, str(exc), []
    grouped: dict[str, list[tuple[str, str]]] = {}
    for name, desc in pairs:
        server, _, tool = name.partition("__")  # tools are namespaced <server>__<tool>
        grouped.setdefault(server, []).append((tool or name, desc))
    return grouped, None, failures


@project_app.command("show")
def project_(
    card: Annotated[
        bool, typer.Option("--card", "-c", help="Also render the bound model's model card (README).")
    ] = False,
) -> None:  # project_ to avoid shadowing the imported project module
    """Show the active project (kodo.toml): model + card, system prompt, and live tools.

    A project is a reproducible assistant — `kodo chat` and `kodo serve --ui` here
    default to this model, system prompt, and MCP tool servers. This connects to
    those servers to list the tools they actually expose (``--card`` also prints
    the bound model's model card).
    """
    proj = project.load()
    if proj is None:
        console.print("[yellow]No kodo.toml here.[/] Run [bold]kodo project init[/] to scaffold a project.")
        raise typer.Exit(1)

    console.print("\n[bold]Project[/] [dim](kodo.toml)[/]\n")

    # Model — full detail card if it's in the library, else just the (missing) name.
    model = None
    if proj.model:
        matches = library_ops.find(proj.model)
        if matches:
            model = matches[0]
            _print_model_card(model, tags.tags_for(model.library_root, model.name))
        else:
            console.print(f"[bold]Model[/]  {proj.model}  [yellow]not in library — run kodo project init[/]\n")
    else:
        console.print("[bold]Model[/]  [dim]none set[/]\n")

    # System prompt — full, so the user sees exactly what the assistant is framed with.
    sp = proj.system_prompt.strip()
    console.print("[bold]System prompt[/]")
    console.print(Panel(sp, border_style="grey37", padding=(0, 1)) if sp else "  [dim]none[/]")

    # Tools — the effective MCP servers (global + project .mcp.json), with their live tools.
    console.print("\n[bold]Tools[/] [dim](global + project .mcp.json)[/]")
    servers = mcpservers.resolve()
    if not servers:
        console.print("  [dim]none[/]")
    else:
        console.print(f"  [dim]connecting to {len(servers)} MCP server(s) …[/]")
        grouped, error, failures = _connect_project_tools(servers)
        if error:
            console.print(f"  [red]could not connect:[/] [dim]{error}[/]")
        failed = {label: reason for label, reason in failures}
        for m in servers:
            command = " ".join([m.command, *m.args])
            if m.name in failed:  # this one couldn't start; the others still work
                console.print(f"  [yellow]{m.name}[/] [dim]({command})[/] — [red]failed:[/] [dim]{failed[m.name]}[/]")
                if m.command.startswith("kodo-mcp-web"):
                    console.print(
                        "    [dim]hint:[/] the web reader is optional — install it with [bold]make install-web[/]"
                    )
                continue
            tools_here = grouped.get(m.name, [])
            console.print(f"  [cyan]{m.name}[/] [dim]({command})[/] — [bold]{len(tools_here)}[/] tool(s)")
            for tool, desc in tools_here:
                summary = desc.splitlines()[0][:80] if desc else ""
                console.print(f"    [white]{tool}[/]{f'  [dim]{summary}[/]' if summary else ''}")

    # Model card (README) — opt-in, since it can be long.
    if card and model is not None:
        card_path = cards.find_card(model.path)
        console.print("\n[bold]Model card[/]")
        if card_path and card_path.is_file():
            from rich.markdown import Markdown  # noqa: PLC0415

            console.print(Markdown(card_path.read_text(errors="replace")[:20_000]))
        else:
            console.print("  [dim]no model card found[/]")


@library_app.command()
def search(
    query: Annotated[str, typer.Argument(help="Text to search the Hugging Face Hub for.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results.")] = 20,
    gguf: Annotated[bool, typer.Option("--gguf", help="Only GGUF (llama.cpp-ready) repos.")] = False,
) -> None:
    """Search the Hugging Face Hub for new models to pull."""
    results = hf.search(query, limit=limit, gguf=gguf)
    if not results:
        console.print(f"No Hub results for {query!r} [dim](or you're offline).[/]")
        return

    console.print(f"\n[bold]{len(results)} results[/] for {query!r} [dim](most downloaded)[/]\n")
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("DOWNLOADS", justify="right")
    table.add_column("LIKES", justify="right")
    table.add_column("~PULL", justify="right")  # est. download (preferred quant, not the whole repo)
    table.add_column("MODEL", style="white")
    for r in results:
        size = _human_size(r.size_bytes) if r.size_bytes else "[dim]?[/]"
        table.add_row(f"{r.downloads:,}", f"{r.likes:,}", size, r.id)
    console.print(table)
    console.print(
        "\n[dim]~PULL = approx download for the preferred quant (not the full repo).\n"
        "Pull one with[/] kodo library pull huggingface <model>"
    )


def _not_chat_msg(name: str, model_format: ModelFormat) -> str:
    return f"[red]{name!r} is not a chat model[/] ({model_format.value}) — kodo runs generative LLMs only."


def _resolve_library_model(name: str, model_format: ModelFormat | None) -> library_ops.LibraryModel:
    """Resolve a generative library model by name, or exit with a helpful message."""
    matches = library_ops.find(name, model_format=model_format)
    if not matches:
        q = name.lower()
        in_sources = [
            e for e in catalog_ops.list_models().entries if q in (e.name.lower(), e.name.rsplit("/", 1)[-1].lower())
        ]
        hit = in_sources[0] if in_sources else None
        if hit and not hit.generative:
            console.print(_not_chat_msg(hit.name, hit.model_format))
        elif hit:
            console.print(
                f"[yellow]{name!r} is not in your library yet — pull it first:[/]  "
                f"kodo library pull {hit.source.value} {hit.name}"
            )
        else:
            roots = ", ".join(str(r) for r in library_ops.roots())  # the libraries actually searched
            console.print(f"[red]{name!r} is not in the library[/] ([dim]searched: {roots}[/]).")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[yellow]{name!r} is ambiguous; narrow with --format:[/]")
        for m in matches:
            console.print(f"  {m.model_format.value:<6} {m.name}")
        raise typer.Exit(1)
    model = matches[0]
    if not model.generative:
        console.print(_not_chat_msg(model.name, model.model_format))
        raise typer.Exit(1)
    if model.model_format is ModelFormat.safetensors:
        # A generative HF checkpoint is still safetensors: a convert/fine-tune
        # source, not a runnable build. Route the user to a GGUF/MLX instead.
        console.print(
            f"[red]{model.name!r} is safetensors[/] — a convert/fine-tune source, not directly "
            "runnable; pull a GGUF or MLX build to run it."
        )
        raise typer.Exit(1)
    if model.is_ollama:
        # Ollama's GGUFs (e.g. gemma3) don't load in stock llama.cpp — run via Ollama.
        # The store is in the model's own library (where it was scanned from), not necessarily
        # the shared default.
        store = model.library_root / "ollama"
        console.print(f"[red]{model.name!r} is an Ollama model[/] — kodo runs GGUF/MLX, not Ollama's format.")
        console.print(f"[yellow]Run it with Ollama:[/]  OLLAMA_MODELS={store} ollama run {model.name}")
        raise typer.Exit(1)
    return model


@voice_app.command("voices")
def voices() -> None:
    """List the built-in Kokoro voices (the always-available in-chat TTS)."""
    from kodo import kokoro  # noqa: PLC0415

    if not kokoro.available():
        typer.secho("Kokoro TTS is unavailable — reinstall kodo (`uv sync`).", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    table = Table(title="Kokoro voices", box=None, header_style="bold")
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column("language")
    table.add_column("gender")
    for v in kokoro.voices():
        table.add_row(v.id, v.name, v.language, v.gender)
    console.print(table)
    console.print(f'\n[dim]{len(kokoro.voices())} voices — use with[/] kodo voice speak --voice <id> "…"')


@voice_app.command("speak")
def speak(
    words: Annotated[list[str], typer.Argument(help="Text to synthesize into speech.")],
    voice: Annotated[
        str | None,
        typer.Option("--voice", "-v", help="Kokoro voice id (e.g. af_heart; see `kodo voice voices`)."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Voice model: an mlx-audio model (dia, qwen3-tts) or a library OuteTTS."),
    ] = None,
    ref_audio: Annotated[
        Path | None,
        typer.Option("--ref-audio", help="Reference clip to clone a voice from (Dia; pair with --ref-text)."),
    ] = None,
    ref_text: Annotated[
        str | None,
        typer.Option("--ref-text", help="Exact transcript of --ref-audio (required for a good clone)."),
    ] = None,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Pin Dia's otherwise-random voice for a reproducible result."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: wav, mp3, flac, opus, ogg, aac (non-wav needs ffmpeg)."),
    ] = "wav",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write audio here (default: a temp file, played aloud)."),
    ] = None,
    play: Annotated[
        bool,
        typer.Option("--play/--no-play", help="Play the audio after generating (via the OS audio player)."),
    ] = True,
) -> None:
    """Text-to-speech: synthesize ``text`` to audio.

    ``--voice`` picks one of Kokoro's built-in voices (the lightweight default engine).
    ``--model dia`` (or ``qwen3-tts``) uses the mlx-audio runtime — with ``--ref-audio`` +
    ``--ref-text`` Dia clones the voice in that clip, or ``--seed`` pins its random voice.
    Any other ``--model`` uses ``llama-tts``/OuteTTS. ``--format`` transcodes the result
    (ffmpeg); with ``-o`` writes there, otherwise a temp file is played.
    """
    from kodo import kokoro, tts  # noqa: PLC0415
    from kodo.voice import audio as audio_export  # noqa: PLC0415
    from kodo.voice import registry as voice_registry  # noqa: PLC0415
    from kodo.voice import runtime as voice_runtime  # noqa: PLC0415

    text = tts.speech_text(" ".join(words))  # accept an unquoted phrase; strip any Markdown
    if not audio_export.is_supported(fmt):
        typer.secho(
            f"Unknown format {fmt!r}; use one of {', '.join(audio_export.FORMATS)}.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    if voice is not None and model is not None:
        console.print(
            f"[yellow]--voice takes precedence[/] — ignoring `--model {model}` (that's for mlx-audio/OuteTTS)."
        )
    spec = voice_registry.get(model) if model else None
    spec = spec or (voice_registry.by_repo(model) if model else None)
    # Enforce the registry's supported flag at the action (A6/VO-M3): reject an unsupported model
    # (e.g. Qwen3-TTS) upfront with a clear reason instead of loading it and failing on empty audio.
    if spec is not None and not spec.supported:
        typer.secho(f"{spec.display_name} isn't supported for synthesis in kodo yet.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        if voice is not None:  # Kokoro (ONNX) — the lightweight preset engine
            if not kokoro.available():
                typer.secho("Kokoro TTS is unavailable — reinstall kodo (`uv sync`).", fg=typer.colors.RED, err=True)
                raise typer.Exit(1)
            if not kokoro.assets_present():
                with console.status("[cyan]Downloading Kokoro voices (~310 MB, first run only)…", spinner="dots"):
                    kokoro.ensure_assets()
            with console.status(f"[cyan]Synthesizing speech ({voice})…", spinner="dots"):
                data = kokoro.synthesize(text, voice, None).read_bytes()
        elif spec is not None and spec.backend == voice_registry.Backend.mlx_audio:  # Dia / Qwen3-TTS
            if not voice_runtime.available():
                typer.secho("mlx-audio not installed. Run `uv sync --extra voice`.", fg=typer.colors.RED, err=True)
                raise typer.Exit(1)
            matches = [m for m in library_ops.find(spec.repo) if m.voice_kind == "tts"]
            if not matches:
                typer.secho(
                    f"{spec.display_name} is not in the library (`kodo voice import`).", fg=typer.colors.RED, err=True
                )
                raise typer.Exit(1)
            extra: dict[str, Any] = {"seed": seed} if seed is not None else {}
            with console.status(f"[cyan]Synthesizing speech ({spec.display_name})…", spinner="dots"):
                data = voice_runtime.synthesize(
                    matches[0].load_target, text, ref_audio=ref_audio, ref_text=ref_text, **extra
                )
        else:  # llama-tts / OuteTTS
            model_path = vocoder_path = None
            if model is not None:
                matches = [m for m in library_ops.find(model) if m.tts]
                if not matches:
                    typer.secho(
                        f"No TTS model matches {model!r} (see `kodo library ls`).", fg=typer.colors.RED, err=True
                    )
                    raise typer.Exit(1)
                model_path, vocoder_path = matches[0].load_target, matches[0].vocoder
            with console.status("[cyan]Synthesizing speech…", spinner="dots"):
                data = tts.synthesize(text, None, model_path, vocoder_path).read_bytes()
        data = audio_export.convert(data, fmt)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    _finish_speak(data, fmt, output, play)


def _finish_speak(data: bytes, fmt: str, output: Path | None, play: bool) -> None:
    """Write synthesized audio (to ``output`` or a temp file) and optionally play it."""
    if output is not None:
        dest = output
    else:
        fd, name = tempfile.mkstemp(suffix=f".{fmt}")
        os.close(fd)
        dest = Path(name)
    dest.write_bytes(data)
    console.print(f"[green]Wrote[/] {dest}")
    if not play:
        return
    if output is not None:
        console.print("[dim](not auto-played — audio was written to your -o file)[/]")
        return
    cmd = host.audio_play_command(dest)
    if cmd is None:
        console.print("[dim](no audio player found; install one to auto-play, e.g. ffmpeg)[/]")
        return
    import subprocess  # noqa: PLC0415

    subprocess.run(cmd)  # noqa: S603 - local playback of our own file


# Attachment helpers live in kodo.attach (shared with the Textual chat); alias the
# names used below.
_media_data_url = attach.media_data_url


def _load_media(
    paths: list[Path], model: library_ops.LibraryModel, *, kind: str, default_mime: str, capable: bool
) -> list[str]:
    """Read image/audio files into data URLs; warn if the model lacks that modality."""
    if not paths:
        return []
    if not capable:
        console.print(f"[yellow]Note:[/] {model.name!r} isn't detected as a {kind} model; {kind} may be ignored.")
    urls: list[str] = []
    for p in paths:
        if not p.is_file():
            console.print(f"[red]{kind.capitalize()} not found:[/] {p}")
            raise typer.Exit(1)
        urls.append(_media_data_url(p, default_mime))
    return urls


@app.command()
def chat(
    name: Annotated[
        str | None,
        typer.Argument(help="Library model (defaults to the project's model in kodo.toml)."),
    ] = None,
    prompt: Annotated[
        str | None,
        typer.Option("-p", "--prompt", help="One-shot prompt, prints just the answer (Claude-style -p)."),
    ] = None,
    model_format: FormatOption = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", "-n", help="Cap generated tokens.")] = None,
    mcp: Annotated[
        list[str],
        typer.Option("--mcp", help="MCP server command(s) for tools; repeatable, e.g. --mcp kodo-mcp-datetime."),
    ] = [],
    tools: Annotated[
        bool,
        typer.Option("--tools/--no-tools", help="Attach MCP tools. Use --no-tools for non-tool-trained models."),
    ] = True,
    system: Annotated[
        str | None,
        typer.Option("--system", help="System prompt for this session (overrides kodo.toml)."),
    ] = None,
    image: Annotated[
        list[Path],
        typer.Option("--image", "-i", help="Attach image file(s) for a vision model (repeatable)."),
    ] = [],
    audio: Annotated[
        list[Path],
        typer.Option("--audio", "-a", help="Attach audio file(s) for an audio model (repeatable)."),
    ] = [],
) -> None:
    """Chat with a library model: full-screen TUI, one-shot with ``-p``, tools with ``--mcp``.

    Interactive chat opens a Textual app (markdown replies, live tool activity, a
    context footer); ``-p`` prints just the answer for scripting. In a project dir,
    ``kodo.toml`` supplies the default model, its MCP tool servers, and a system
    prompt; ``--mcp`` flags add to (not replace) those, ``--system`` overrides the
    prompt, and ``--no-tools`` drops tools entirely (some models regurgitate the
    tool schema instead of calling it).
    """
    proj = project.load()
    model_name = project.resolve_model(name, proj)
    if model_name is None:
        console.print(
            "[red]No model given.[/] Pass a model name (see [cyan]kodo library ls[/]), "
            "set a machine default ([cyan]kodo config set model <name>[/]), "
            "or define one in a project ([cyan]kodo project init[/])."
        )
        raise typer.Exit(1)
    model = _resolve_library_model(model_name, model_format)

    # (name, argv, env) per server. A bare --mcp value resolves against advertised servers
    # (so `--mcp datetime` finds kodo-mcp-datetime), else it's used verbatim as a command;
    # the rest come from the resolved mcp.json layers (global + project, see kodo.mcpservers).
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]] = (
        [_cli_mcp_spec(c) for c in mcp] + [s.to_spec() for s in mcpservers.resolve()] if tools else []
    )
    system_prompt = system if system is not None else (proj.system_prompt if proj else "")
    caps = capabilities.capabilities(model)
    images = _load_media(image, model, kind="vision", default_mime="image/png", capable=caps.vision)
    audios = _load_media(audio, model, kind="audio", default_mime="audio/wav", capable=caps.audio)
    try:
        if prompt is not None and not mcp_servers:
            # Scripted one-shot, no tools: print only the reply to stdout (clean for
            # piping); errors go to stderr. Everything else goes through the one
            # interactive/agent path below (tools optional, empty list = plain chat).
            print(runtime.generate(model, prompt, max_tokens, system_prompt, images, audios))  # noqa: T201
        else:
            _chat_with_tools(model, mcp_servers, prompt, max_tokens, system_prompt, images, audios)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _chat_with_tools(
    model: library_ops.LibraryModel,
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]],
    prompt: str | None,
    max_tokens: int | None,
    system_prompt: str = "",
    images: list[str] | None = None,
    audios: list[str] | None = None,
) -> None:
    """Serve the model, then chat: the Textual TUI interactively, or ``-p`` scripted.

    With ``prompt`` set (``-p``) this streams a single answer to stdout (tools still
    run); otherwise it hands off to the full-screen Textual chat.
    """
    import asyncio  # noqa: PLC0415

    from rich.progress import Progress  # noqa: PLC0415

    from kodo import (
        agent,  # noqa: PLC0415
        chatui,  # noqa: PLC0415
        sampling,  # noqa: PLC0415
    )
    from kodo import tools as mcp_tools  # noqa: PLC0415

    servers = mcp_servers  # already (name, argv, env) specs
    # Model-recommended sampling (incl. the anti-loop repeat_penalty default), applied
    # to every CLI chat turn just like the web path does.
    rec = sampling.recommended(model)
    # Tool activity is meta → stderr, so `-p` stdout stays just the answer.
    err = Console(stderr=True)

    # Per-turn state: a "thinking" spinner shown until the first token/tool-call
    # arrives (model prefill latency otherwise looks dead), and whether the reply
    # label has been printed yet.
    turn_status: Progress | None = None
    turn_labeled = True
    # Reasoning (thinking) streams dim to stderr with no trailing newline; the
    # answer streams to stdout with no leading newline. Without a separator the
    # answer glues onto the last thinking line ("…structure.```json"). Track when
    # thinking happened so the first answer token starts on a fresh line.
    turn_reasoned = False
    turn_separated = True

    def _first_output() -> None:
        nonlocal turn_status, turn_labeled
        if turn_status is not None:
            turn_status.stop()
            turn_status = None
        if not turn_labeled:
            chatui.assistant_prefix(err, inline=False)
            turn_labeled = True

    def _think() -> None:
        nonlocal turn_status, turn_reasoned, turn_separated
        turn_reasoned = False
        turn_separated = True
        turn_status = chatui.thinking(err)
        turn_status.start()

    def _separate() -> None:
        # Break the reasoning block off the answer with a newline, once per turn.
        nonlocal turn_separated
        if turn_reasoned and not turn_separated:
            err.print()
            turn_separated = True

    def on_event(kind: str, detail: str) -> None:
        _first_output()
        _separate()  # break a preceding thinking block off the tool line
        icon = "[cyan]⚙[/]" if kind == "call" else "[grey62]↳[/]"
        err.print(f"  {icon} [grey62]{detail[:200]}[/]")

    def on_token(text: str) -> None:
        _first_output()
        _separate()
        print(text, end="", flush=True)  # noqa: T201

    def on_reasoning(text: str) -> None:
        # Reasoning models' thinking → dim on stderr (keeps -p stdout the answer only).
        nonlocal turn_reasoned, turn_separated
        _first_output()
        turn_reasoned = True
        turn_separated = False
        err.print(text, end="", style="grey42")

    def seed() -> list[dict[str, object]]:
        return [{"role": "system", "content": system_prompt}] if system_prompt else []

    async def _run_oneshot(base: str) -> None:
        """The scripted ``-p`` path (with tools): stream the answer to stdout, no UI."""
        nonlocal turn_labeled
        assert prompt is not None  # only entered when -p supplied a prompt
        async with mcp_tools.connect(servers) as toolset:
            turn_labeled = True  # -p mode: stdout is just the answer, no label
            _think()
            await agent.run(
                base,
                [*seed(), {"role": "user", "content": agent.user_content(prompt, images, audios)}],
                toolset,
                max_tokens,
                on_event,
                on_token,
                on_reasoning=on_reasoning,
                temperature=rec.temperature,
                top_p=rec.top_p,
                top_k=rec.top_k,
                min_p=rec.min_p,
                repeat_penalty=rec.repeat_penalty,
                model=str(model.load_target),  # required by mlx-vlm; ignored by llama-server/mlx-lm
            )
            _first_output()
            print()  # noqa: T201 - newline after streamed answer

    rt = runtime.load(model)  # start the runtime (with a load spinner); caller/TUI owns stop()
    if prompt is not None:
        try:
            asyncio.run(_run_oneshot(rt.base))
        finally:
            runtime.stop(rt)
        return
    # Interactive: hand the runtime to the full-screen Textual chat (imported lazily so
    # `kodo library ls` and friends don't pay textual's import cost). The TUI owns the runtime
    # from here — it can switch models — and stops it on exit.
    from kodo import chat_tui  # noqa: PLC0415

    chat_tui.run_interactive(
        runtime_proc=rt,
        servers=servers,
        system_prompt=system_prompt,
        images=images or [],
        audios=audios or [],
        max_tokens=max_tokens,
    )


def _export_serve_env(*, ui: bool, model: str | None, runtime_port: int | None, debug: bool) -> None:
    """The one place ``serve`` hands its config to the app — the deliberate env-as-API (A8).

    With ``--reload``, uvicorn imports the app in a *fresh subprocess*, where the CLI callback's
    in-process overrides (``--runtime-port``, ``--debug``) and the resolved locked model don't
    exist — so these ``KODO_*`` env vars are that subprocess's config channel. Centralized here so
    it's a single documented surface instead of scattered ``os.environ`` writes; without
    ``--reload`` it's harmless (the same process already has the overrides). The auth token is
    exported alongside these in ``serve`` once it's known.
    """
    if ui:
        os.environ["KODO_SERVE_UI"] = "true"
    if model is not None:
        os.environ["KODO_SERVE_MODEL"] = model
    if runtime_port is not None:
        os.environ["KODO_RUNTIME_PORT"] = str(runtime_port)
    if debug:
        os.environ["KODO_DEBUG"] = "true"


@app.command()
def serve(
    ui: Annotated[bool, typer.Option("--ui", help="Also serve the browser UI (single-page app).")] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Lock the server to one model (extension backend); no switching."),
    ] = None,
    host: Annotated[str | None, typer.Option("--host", help="Bind address (default 127.0.0.1).")] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Web server port (default: auto-pick a free port; pin for a stable URL)."),
    ] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes.")] = False,
) -> None:
    """Run the web server (browse API, plus the browser UI with --ui).

    With --model the server is locked to a single model (the Chrome-extension
    backend); otherwise the UI can switch models freely. The port defaults to a
    free auto-picked one (printed below); pass --port to pin a stable URL.
    """
    import os  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    library_ops.roots()  # fail fast + clean if no library is configured (rather than 500ing per request)

    # A project is a locked, purpose-built assistant: it binds the server to its model
    # (no picker, like --model), so `kodo serve` in a project == serve --model <project.model>.
    # An explicit --model overrides. No project (or a project without a model) => free-play.
    proj = project.load()
    locked_model = model or (proj.model if proj else None)
    locked_by_project = model is None and locked_model is not None
    if locked_model is not None:
        # Validate the locked model up front (like `kodo chat`) so a bad name gives a clean message
        # here, not a uvicorn "Application startup failed" traceback from the app lifespan.
        _resolve_library_model(locked_model, None)
    # Hand the serve config to the (possibly reloaded) worker process — one documented env API.
    _export_serve_env(
        ui=ui,
        model=locked_model,
        runtime_port=config.runtime_port_override(),
        debug=config.debug_enabled(),
    )

    get_settings.cache_clear()
    settings = get_settings()
    # Precedence: --host/--port > KODO_HOST/KODO_PORT/kodo.toml > auto-pick a free port.
    bind_host = host or settings.host
    bind_port = port or settings.port or runtime.find_free_port()
    base = f"http://{bind_host}:{bind_port}"

    # Binding a non-loopback address exposes model control + MCP tool execution (arbitrary code
    # via kodo-mcp-exec) to the LAN. Never leave that unauthenticated: auto-generate a bearer
    # token if one isn't configured, so a client must present it (V-14). An explicitly-set
    # KODO_AUTH_TOKEN is honored as-is (and enforced even on loopback, for deliberate opt-in).
    loopback = bind_host in ("127.0.0.1", "localhost", "::1", "")
    auth_token = settings.auth_token
    if not loopback and not auth_token:
        auth_token = secrets.token_urlsafe(24)
        os.environ["KODO_AUTH_TOKEN"] = auth_token  # part of the serve→worker env API (_export_serve_env)
        get_settings.cache_clear()

    console.print("\n[bold]kodo[/]")
    if not loopback:
        console.print(
            f"  [yellow]Exposed on {bind_host}[/] — anyone who can reach this host can control models and run tools."
        )
    if auth_token:
        console.print("  Auth:     [bold]bearer token required[/] [dim](Authorization: Bearer <token>)[/]")
    if locked_model is not None:
        console.print(f"  Locked:   [bold]{locked_model}[/] [dim]· {'project' if locked_by_project else '--model'}[/]")
    # A tokenized URL lets the user just open the SPA: it captures ?token= into the browser and
    # sends it as a bearer header thereafter (like Jupyter). Non-browser clients send the header.
    ui_url = f"{base}/?token={auth_token}" if auth_token else base
    if ui:
        if settings.frontend_dir.is_dir():
            console.print(f"  UI:       [link={ui_url}]{ui_url}[/]")
        else:
            console.print(f"  [yellow]UI not built[/] — expected at [dim]{settings.frontend_dir}[/]; serving API only")
    console.print(f"  API:      [link={base}]{base}[/]")
    console.print(f"  Docs:     [link={base}/docs]{base}/docs[/]")
    if auth_token:
        console.print(f"  [dim]Token:[/]    [dim]{auth_token}[/]")
    console.print("  [dim]Ctrl-C to stop[/]\n")
    uvicorn.run(
        "kodo.app:app",
        host=bind_host,
        port=bind_port,
        reload=reload,
    )


# --- voice -----------------------------------------------------------------


@voice_app.command("setup")
def voice_setup() -> None:
    """Make Dia self-contained: seed its DAC codec into the (drive) HF cache.

    Dia decodes through ``descript-audio-codec-44khz``, which mlx-audio fetches by repo
    id at synth time — separately from Dia's own weights. With the HF cache redirected
    onto the library drive, this downloads the codec there once so Dia works offline and
    travels with the drive.
    """
    from kodo import hfcache  # noqa: PLC0415
    from kodo.voice import dac  # noqa: PLC0415

    cache = hfcache.drive_cache_dir()
    where = f"[green]{cache}[/] (on the drive)" if cache else "[yellow]~/.cache/huggingface[/] (machine-local)"
    console.print(f"HF cache: {where}")
    if not cache:
        console.print("[dim]Set[/] KODO_LIBRARY_ROOT [dim]to a real library drive so the codec travels with it.[/]")
    if dac.codec_present():
        console.print(f"[green]DAC codec already present[/] [dim]({dac.DAC_REPO}).[/]")
        return
    console.print(f"[dim]Downloading[/] {dac.DAC_REPO} [dim](~293 MB)…[/]")
    try:
        path = dac.seed_codec(token=get_settings().hf_token)
    except Exception as exc:  # noqa: BLE001 - a network/hub failure is user-facing, not a crash
        console.print(f"[red]Failed to seed the DAC codec:[/] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Seeded[/] the DAC codec [dim]→ {path}[/]. Dia is now self-contained.")


@voice_app.command("list")
@voice_app.command("ls", hidden=True)  # alias, matching `kodo library ls`
def voice_list() -> None:
    """List known voice models (TTS/STT) and where each lives — HF cache or a project library.

    Presence is checked across the project's libraries (project-local + ``@shared``), like
    ``kodo library ls`` — so a model on the shared drive shows as ``library`` here too.
    """
    from kodo import voice  # noqa: PLC0415

    # Merge presence across every library in scope: a model counts as "in library" if it's in
    # any of them (project-local or @shared). in_cache is machine-global (the HF cache).
    merged: dict[str, voice.VoicePresence] = {}
    for r in library_ops.roots():
        for p in voice.discover(r):
            cur = merged.get(p.spec.id)
            if cur is None or (p.in_library and not cur.in_library):
                merged[p.spec.id] = p

    table = Table(box=box.SIMPLE, header_style="bold")
    for col in ("ID", "KIND", "VOICE", "BACKEND", "WHERE", "SIZE"):
        table.add_column(col, style="cyan" if col == "ID" else None)
    for p in merged.values():
        s = p.spec
        where = "[green]library[/]" if p.in_library else ("[yellow]hf-cache[/]" if p.in_cache else "[dim]—[/]")
        table.add_row(
            s.id, s.kind.value, s.voice_mode.value, s.backend.value, where, p.size_human if p.available else "—"
        )
    console.print(table)
    console.print(
        "[dim]Add one to the project library:[/] kodo library pull voice <id>  [dim](downloads if needed).[/]"
    )


@voice_app.command("import")
def voice_import(
    models: Annotated[list[str], typer.Argument(help="Voice model id(s) to import; omit with --all.")] = [],
    all_: Annotated[bool, typer.Option("--all", help="Import every voice model already in the HF cache.")] = False,
    prune: Annotated[bool, typer.Option("--prune", help="Delete the HF-cache copy after a verified import.")] = False,
    shared: Annotated[
        bool, typer.Option("--shared", help="Import into the shared/default library instead of the project-local one.")
    ] = False,
) -> None:
    """Import voice models into a library — a project-aware alias for ``kodo library pull voice``.

    Targets the project-local library by default (``--shared`` for the archive). A named model
    not yet in the HF cache is downloaded; ``--all`` imports only what's already cached.
    """
    if all_ and models:  # like `kodo library pull`, reject the contradictory combination
        console.print("[red]Give voice model id(s) OR --all, not both.[/]")
        raise typer.Exit(2)
    root = library_ops.default_root() if shared else library_ops.roots()[0]
    if all_:
        _pull_voice_all(root, prune)
        return
    if not models:
        console.print("[red]Give a model id or --all[/] (see [bold]kodo voice list[/]).")
        raise typer.Exit(1)
    failed = False
    for vid in models:
        typer.echo(f"Pulling voice:{vid} -> {root} …")
        try:
            result = catalog_ops.pull(ModelSource.voice, vid, library_root=root, move=prune)
        except Exception as exc:  # noqa: BLE001 - unknown id / download / disk; surface cleanly
            console.print(f"  [red]failed[/]: {exc}")
            failed = True
            continue
        console.print(f"  [green]done[/] {result.size_human} → {result.destination}")
    console.print("[dim]Same thing, project-aware:[/] kodo library pull voice <id>")
    if failed:
        raise typer.Exit(1)


# --- plugins ---------------------------------------------------------------


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
        import shlex  # noqa: PLC0415

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


_mount_plugins()


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
