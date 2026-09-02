"""`stabbur doctor` and `stabbur setup` - machine health checks and first-run setup."""

from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.markup import escape
from rich.table import Table

from stabbur import (
    config,
    doctor,
    mcp_catalog,
    mcpservers,
    userconfig,
)
from stabbur import library as library_ops
from stabbur.cli._app import app
from stabbur.cli._common import (
    _count,
    console,
)

_DOCTOR_STYLE = {
    doctor.CheckStatus.ok: ("green", "ok"),
    doctor.CheckStatus.warn: ("yellow", "warn"),
    doctor.CheckStatus.fail: ("red", "fail"),
}


def _print_doctor_table(report: doctor.DoctorReport) -> None:
    """Render a doctor report as the shared status table (used by `doctor` and `setup`).

    A check's own text is *data*, escaped before it meets Rich's markup. The rows carry install
    commands and filesystem paths, and an unescaped ``[...]`` is read as a style tag and swallowed:
    the MLX hint's ``uv tool install -e ".[mlx]"`` printed as ``-e "."`` — an install command that
    silently installs the wrong thing, in the row whose whole job is to fix a missing runtime.
    """
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    table.add_column("", width=4)
    table.add_column("Check", style="bold")
    table.add_column("Detail", overflow="fold")
    for check in report.checks:
        color, label = _DOCTOR_STYLE[check.status]
        detail = escape(check.detail)
        if check.hint:
            detail += f"\n[dim]{escape(check.hint)}[/]"
        # A grouped row belongs under its parent, not beside it (Check.group). The table has no
        # tree, so indent the name — the terminal's version of the nesting the web UI renders.
        name = escape(check.name)
        table.add_row(f"[{color}]{label}[/]", f"  {name}" if check.group else name, detail)
    console.print(table)


@app.command("doctor")
def doctor_() -> None:  # doctor_ to avoid shadowing the imported doctor module
    """Check system health: runtimes, backend, library, and the current project.

    A quick pre-flight: are the runtime binaries stabbur spawns installed, is the
    backend up (a remote `/v1` when STABBUR_UPSTREAM is set, else local runtimes),
    is the library reachable and non-empty, and does the project (if any) point
    at a model that's present. Exits non-zero if any check fails.
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
    # The machine config is the lowest-priority source, so a STABBUR_LIBRARY_ROOT env var or a cwd
    # .env still shadows what we just wrote. Say so rather than let doctor's differing value baffle.
    effective = config.Settings().library_root
    if effective is not None and effective != root:
        console.print(
            f"[yellow]Note[/]  a higher-priority source (STABBUR_LIBRARY_ROOT env or ./.env) still "
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
        console.print("[dim]Model[/]  no runnable models yet — pull one with `stabbur library pull`.")
        return
    # Pick for the user only where the pick is obvious: the recommended model if they have it,
    # or the single model in a fresh library. A one-model library is not a choice, it is the
    # answer — and leaving no default sends the next command (`stabbur chat`) into "no model".
    from stabbur import curated  # noqa: PLC0415

    obvious = next((m.name for m in models if m.name == curated.MAIN_MODEL), None)
    if obvious is None and len(models) == 1:
        obvious = models[0].name
    if obvious is not None:
        userconfig.set_value("default_model", obvious)
        config.get_settings.cache_clear()
        console.print(f"[green]Set[/]  default model -> {obvious}")
        return
    if yes:
        console.print("[dim]Model[/]  no default set — `stabbur config set model <name>` to pick one.")
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
        # Report what is true, not what this run did: an already-built UI was built by an earlier
        # run or by packaging, and saying "built ->" after `--no-build-ui` claimed a build that was
        # explicitly declined.
        console.print(f"[green]Web UI[/]  already built -> {dist}")
        return
    if build_ui is False:
        # Declined explicitly: say the UI is missing and how to get it, rather than going quiet and
        # leaving `serve --ui` to 404 with no explanation.
        console.print("[dim]Web UI[/]  not built (--no-build-ui) — run `make frontend` when you want it.")
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
    """Seed the global mcp.json with stabbur's default-on toolset (datetime) if it has none."""
    existing = mcpservers.read_global()
    if existing:
        console.print(f"[green]Tools[/]  global default -> {', '.join(s.name for s in existing)}")
        return
    # The same minimal default `stabbur serve` seeds on a fresh machine (mcp_catalog.DEFAULT_ENABLED):
    # models otherwise don't know the current date/time. More via `stabbur mcp add --global <name>`.
    if not yes and not typer.confirm(
        f"Enable the default {', '.join(mcp_catalog.DEFAULT_ENABLED)!r} tool for chats?", default=True
    ):
        console.print("[dim]Tools[/]  none — add with `stabbur mcp add --global <name>`.")
        return
    seeded = mcp_catalog.seed_global_defaults(only_if_absent=False)  # asked already; fill an empty file too
    if not seeded:
        return
    console.print(
        f"[green]Set[/]  default tools -> {', '.join(seeded)}  [dim](stabbur mcp add --global <name> for more)[/]"
    )


def _setup_voice(download: bool) -> None:
    """Fetch the Kokoro assets now, rather than mid-conversation on the first "Listen".

    Not a question: stabbur speaks out of the box, so the voice is part of a working install and
    setup is the moment to get it. It is also the only download stabbur otherwise starts on its
    own, several minutes into a chat, with no way for the user to have seen it coming.
    ``--no-download`` is the way out.

    The assets land in the library (``<root>/tts/kokoro``), so this is a per-library step, not a
    machine one.
    """
    from stabbur.voice import kokoro  # noqa: PLC0415 - keep the voice deps off the import path

    if not kokoro.available():
        console.print("[yellow]Voice[/]  Kokoro engine unavailable — reinstall stabbur (`uv sync`).")
        return
    if kokoro.assets_present():
        console.print("[green]Voice[/]  in-chat voice ready (Kokoro)")
        return
    if not download:
        console.print("[dim]Voice[/]  skipped (--no-download) — it downloads on first use of Listen.")
        return
    try:
        with console.status("[cyan]Downloading Kokoro voices (~310 MB)…", spinner="dots"):
            kokoro.ensure_assets()
    except Exception as exc:  # noqa: BLE001 - network/disk: report it, don't abort the rest of setup
        console.print(f"[yellow]Voice[/]  download failed ({exc}) — it will retry on first use.")
        return
    console.print("[green]Set[/]  in-chat voice -> Kokoro")


def _setup_models(download: bool) -> None:
    """Pull the small starting set (transcription + one basic chat model) if the library lacks it.

    An empty library is the state a first run actually lands in, and every next step — `chat`,
    `serve --ui` — is useless in it. So setup fills it: not the whole catalog, just enough to have
    something to talk to. Already-present models are skipped, so re-running costs nothing.
    """
    from stabbur import curated, wantlist  # noqa: PLC0415

    try:
        root = library_ops.default_root()
        plan = wantlist.plan(list(curated.SETUP_DEFAULTS), library_ops.scan())
    except Exception as exc:  # noqa: BLE001 - an unreadable library is reported by doctor below
        console.print(f"[yellow]Models[/]  couldn't check the library ({exc}).")
        return
    if not plan.missing:
        console.print(f"[green]Models[/]  starting set present ({_count(len(plan.present), 'model')})")
        return
    if not download:
        console.print("[dim]Models[/]  skipped (--no-download) — `stabbur library sync starter` when you want them.")
        return
    for want in plan.missing:
        console.print(f"[cyan]Pulling[/] {want.name} [dim]({want.note})[/]")
        try:
            result = wantlist.pull_entry(want, root)
        except Exception as exc:  # noqa: BLE001 - one failed pull must not abort setup
            console.print(f"  [yellow]failed[/] — {exc}")
            continue
        console.print(f"  [green]done[/] {result.size_human}")


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
    download: Annotated[
        bool,
        typer.Option("--download/--no-download", help="Fetch the voice + a starting model (default: yes)."),
    ] = True,
) -> None:
    """First-run machine setup: configure the library + default model, build the UI, check runtimes.

    The write-mode companion to `stabbur doctor`, at machine scope (whereas `stabbur project init`
    scaffolds a single project). It persists per-machine defaults to ~/.config/stabbur/config.toml,
    builds the browser UI if bun is available, and fixes what it can — printing an OS-specific
    hint for what it can't (installing the llama.cpp binary). Safe to re-run.
    """
    console.rule("[bold]stabbur setup")
    _setup_library_root(library_root, yes)
    _setup_voice(download)
    _setup_models(download)
    # After the pull, not before: on a fresh machine the library is empty until `_setup_models`
    # fills it, and a default-model step that ran first could only say "no runnable models yet"
    # about models it was about to download.
    _setup_default_model(model, yes)
    _setup_default_tools(yes)
    _setup_ui(build_ui, yes)
    console.print()
    report = doctor.run_checks()
    _print_doctor_table(report)
    if report.status is doctor.CheckStatus.fail:
        console.print("\n[yellow]Setup done, but some checks still fail[/] (see above — likely the llama.cpp binary).")
    else:
        console.print("\n[green]Setup complete.[/] Try `stabbur chat` or `stabbur serve --ui`.")
    console.print("[dim]More models:[/] `stabbur library sets` lists the curated sets to pull from.")
