"""`heim doctor` and `heim setup` - machine health checks and first-run setup."""

from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.table import Table

from heim import (
    config,
    doctor,
    mcpservers,
    userconfig,
)
from heim import library as library_ops
from heim.cli._app import app
from heim.cli._common import (
    _to_mcp_server,
    console,
)

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

    A quick pre-flight: are the runtime binaries heim spawns installed, is the
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
    # The machine config is the lowest-priority source, so a HEIM_LIBRARY_ROOT env var or a cwd
    # .env still shadows what we just wrote. Say so rather than let doctor's differing value baffle.
    effective = config.Settings().library_root
    if effective is not None and effective != root:
        console.print(
            f"[yellow]Note[/]  a higher-priority source (HEIM_LIBRARY_ROOT env or ./.env) still "
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
        console.print("[dim]Model[/]  no runnable models yet — pull one with `heim library pull`.")
        return
    if yes:
        console.print("[dim]Model[/]  no default set — `heim config set model <name>` to pick one.")
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
    from heim import plugins  # noqa: PLC0415

    # Minimal, safe default: datetime — models otherwise don't know the current date/time. More
    # via `heim mcp add --global <name>` (see `heim mcp list`).
    server = next((s for s in plugins.advertised_servers(plugins.manager()) if s.name == "datetime"), None)
    if server is None:
        return
    if not yes and not typer.confirm("Enable the default 'datetime' tool for chats?", default=True):
        console.print("[dim]Tools[/]  none — add with `heim mcp add --global <name>`.")
        return
    mcpservers.add(_to_mcp_server(server.name, server.command), glob=True)
    console.print(f"[green]Set[/]  default tools -> {server.name}  [dim](heim mcp add --global <name> for more)[/]")


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

    The write-mode companion to `heim doctor`, at machine scope (whereas `heim project init`
    scaffolds a single project). It persists per-machine defaults to ~/.config/heim/config.toml,
    builds the browser UI if bun is available, and fixes what it can — printing an OS-specific
    hint for what it can't (installing the llama.cpp binary). Safe to re-run.
    """
    console.rule("[bold]heim setup")
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
        console.print("\n[green]Setup complete.[/] Try `heim chat` or `heim serve --ui`.")
