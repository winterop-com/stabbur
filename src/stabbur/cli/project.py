"""`stabbur init` - scaffold a project assistant - and `stabbur project` - inspect one."""

import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel, ConfigDict, ValidationError
from rich.markup import escape
from rich.panel import Panel

from stabbur import (
    cards,
    mcpservers,
    project,
    tags,
)
from stabbur import catalog as catalog_ops
from stabbur import library as library_ops
from stabbur.cli import configure_tui
from stabbur.cli._app import app, project_app
from stabbur.cli._common import (
    _CURATED,
    _LOCAL_LIBRARY,
    _ForceOpt,
    _GitOpt,
    _ModelOpt,
    _print_model_card,
    _TemplateOpt,
    _to_mcp_server,
    _UpstreamOpt,
    _UvOpt,
    _VoicesOpt,
    console,
)
from stabbur.cli.init_wizard import CHAT_PROMPT, DEFAULT_VOICE, ModelChoice, run_wizard
from stabbur.models import ModelSource, ProjectTemplate, _human_size
from stabbur.project import scaffold
from stabbur.project.templates import TEMPLATES


def _pull_or_exit(model: str, library_root: Path | None) -> None:
    """Pull ``model`` from Hugging Face (into ``library_root`` or the shared library), or exit."""
    try:
        if library_root is None:
            catalog_ops.pull(ModelSource.huggingface, model)
        else:
            catalog_ops.pull(ModelSource.huggingface, model, library_root=library_root)
    except Exception as exc:  # noqa: BLE001 - surface pull/network failures
        console.print(f"[red]Pull failed:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc


class _WizardChoices(BaseModel):
    """The choices that define a scaffolded project — from a template preset or the wizard."""

    model_config = ConfigDict(frozen=True)

    model: str
    mcp: list[tuple[str, str]]  # (name, command) MCP servers to write into .mcp.json
    system_prompt: str
    chat_voice: str
    upstream: str = ""  # an OpenAI-compatible server the models run on; "" = they run here
    voice: bool = False  # a voice project: only the system prompt differs
    template: ProjectTemplate | None = None


def _model_choices() -> list[ModelChoice]:
    """The models the wizard offers: the curated starters, best-first for a new project.

    Deliberately not the machine library: a project downloads its own copy (see
    :func:`_provision_model`), so offering what happens to be on this machine's drive would
    suggest a link between the two that a self-contained project does not have.
    """
    return [ModelChoice(name=c.id, detail=c.note, size_gb=c.size_gb) for c in _CURATED]


def _gather_choices(
    name: str, model: str | None, template: str | None, voices_gb: float = 0.0, upstream: str | None = None
) -> _WizardChoices:
    """Resolve the scaffolding choices: a named template, the wizard, or flags.

    Gathered up front so quitting leaves nothing behind — the caller creates the project
    directory only after this returns.
    """
    if template is not None:
        tmpl = TEMPLATES.get(template)
        if tmpl is None:
            console.print(f"[red]Unknown template {template!r}[/] — available: {', '.join(sorted(TEMPLATES))}")
            raise typer.Exit(1)
        # A template presets the whole wizard, so scaffolding is reproducible in one command.
        console.print(f"\nUsing the [bold]{template}[/] template.")
        return _WizardChoices(
            model=model or tmpl.model,
            mcp=list(tmpl.mcp),
            system_prompt=tmpl.system_prompt,
            chat_voice=tmpl.chat_voice or DEFAULT_VOICE,
            template=tmpl,
        )
    # No terminal (a pipe, a script, CI) means no TUI: fall back to the flags and the defaults
    # rather than failing, so `stabbur init x --model <name>` stays scriptable.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        if model is None:
            console.print("[red]No terminal for the wizard[/] — pass --model (and --template) to scaffold here.")
            raise typer.Exit(1)
        return _WizardChoices(
            model=model, mcp=[], system_prompt=CHAT_PROMPT, chat_voice=DEFAULT_VOICE, upstream=upstream or ""
        )

    from stabbur import plugins  # noqa: PLC0415 - plugin discovery is slow; only the wizard needs it

    result = run_wizard(
        name=name,
        models=_model_choices(),
        servers=plugins.advertised_servers(plugins.manager()),
        voices_gb=voices_gb,
    )
    if result is None:
        console.print("[dim]Cancelled — nothing was created.[/]")
        raise typer.Exit(1)
    return _WizardChoices(
        model=model or result.model,
        mcp=result.mcp,
        system_prompt=result.system_prompt,
        chat_voice=DEFAULT_VOICE,  # which voice speaks is a UI choice; the manifest just has a default
        upstream=result.upstream,
        voice=result.voice,
    )


# The voice package every project gets: the good voice, and the one that listens. Kokoro is
# fetched separately — it is engine assets rather than a library model.
_STARTER_VOICES = ("voxcpm2", "whisper")
# What that package costs, for the wizard to show before it fetches anything (Kokoro + the two).
_VOICE_PACKAGE_GB = 5.0


def _provision(target: Path, model: str, *, voices: bool = True, upstream: str | None = None) -> None:
    """Download what the project asked for into its own ``library/``.

    Always a fresh download, never a copy out of the machine library: a project is meant to be
    self-contained — zip the directory, move it to another machine, and it still runs — so it owns
    its weights outright rather than inheriting a copy whose provenance is this one drive.

    Every project gets the voices — Kokoro to speak with, VoxCPM2 for when it matters, Whisper to
    listen — so even one that binds no model yet is useful the day it is made. That is the point of
    the default; ``--no-voices`` is for someone who means it. ``model`` may be empty (a project that
    binds one later), which is also a real answer and leaves nothing half-made.
    """
    from stabbur.voice import kokoro  # noqa: PLC0415

    local_lib = target / _LOCAL_LIBRARY
    local_lib.mkdir(parents=True, exist_ok=True)
    if upstream:
        # The weights live on the other box; pulling a copy here would defeat the point of
        # pointing at it. The voices are still local — they run in-process, not on the remote.
        console.print(f"\n[dim]Models run on[/] {upstream} [dim]— nothing to download.[/]")
    elif model:
        console.print(f"\nDownloading [bold]{model}[/] into {_LOCAL_LIBRARY}/ …")
        _pull_or_exit(model, local_lib)
    else:
        console.print("\n[dim]No model bound[/] — add one with `stabbur configure` or `stabbur library pull`.")

    # The in-chat voice: assets, not a library model, and they must land in *this* project so a
    # moved copy can still speak. Bundled whenever anything is being fetched, since it is small
    # and it is what makes "replies can be spoken" true.
    if not voices:
        console.print("[dim]No voices[/] (--no-voices) — add them later with `stabbur configure`.")
        return

    console.print("Adding the in-chat voice [bold]Kokoro[/] …")
    try:
        kokoro.ensure_assets(local_lib)
    except Exception as exc:  # noqa: BLE001 - a voice is not worth failing a scaffold over
        console.print(f"  [yellow]skipped[/] — {exc}")

    from stabbur import host  # noqa: PLC0415

    if not host.is_apple_silicon():
        # The mlx-audio models only run there; shipping them into a project built elsewhere would
        # be gigabytes that cannot speak. Kokoro (ONNX) is cross-platform and already in.
        console.print("[dim]Skipping the mlx-audio voices[/] — that runtime is Apple Silicon only.")
        return
    for vid in _STARTER_VOICES:
        console.print(f"Adding [bold]{vid}[/] …")
        try:
            catalog_ops.pull(ModelSource.voice, vid, library_root=local_lib)
        except Exception as exc:  # noqa: BLE001 - one voice model failing must not lose the project
            console.print(f"  [yellow]skipped[/] — {escape(str(exc))}")


def _write_project(
    target: Path, choices: _WizardChoices, *, uv: bool, voices: bool = True, upstream: str | None = None
) -> None:
    """Write ``stabbur.toml`` + ``.mcp.json`` (and, for a uv project, pyproject/README + template files)."""
    # A template may carry [assistant] target metadata (opaque dict) — a single ``assistant`` block or
    # a multi-target ``assistants`` list — validated here to AssistantInfo / an AssistantRegistry so
    # render_manifest (the single writer) emits it, or refuses a malformed one up front with the CLI's
    # clean error contract (ProjectError), not a pydantic traceback mid-scaffold.
    assistant = None
    registry = None
    tmpl = choices.template
    try:
        if tmpl is not None and tmpl.assistants is not None:
            targets = [project.AssistantInfo.model_validate(a) for a in tmpl.assistants]
            registry = project.AssistantRegistry(targets=targets)
        elif tmpl is not None and tmpl.assistant is not None:
            assistant = project.AssistantInfo.model_validate(tmpl.assistant)
    except ValidationError as exc:
        raise project.ProjectError(f"template assistant metadata is invalid: {exc}") from exc
    (target / "stabbur.toml").write_text(
        project.render_manifest(
            model=choices.model,
            system_prompt=choices.system_prompt,
            local_library_dir=_LOCAL_LIBRARY,
            upstream=upstream,
            chat_voice=choices.chat_voice,
            assistant=assistant,
            registry=registry,
        )
    )
    # Tools go in the standard .mcp.json (not stabbur.toml). In a uv project the servers are pinned
    # deps that run straight off PATH, so drop the runtime `uvx ` fetch from each command.
    scaffold_mcp = [(name, scaffold.strip_uvx(command)) for name, command in choices.mcp] if uv else choices.mcp
    for entry_name, command in scaffold_mcp:
        mcpservers.add(_to_mcp_server(entry_name, command), glob=False, project_dir=target)
    if uv:
        mlx = "mlx" in choices.model.lower()
        # Pass the original (uvx-bearing) mcp so pip deps are extracted before uvx is stripped.
        extras = choices.template.extras if choices.template is not None else []
        (target / "pyproject.toml").write_text(
            scaffold.render_pyproject(target.resolve().name, choices.mcp, mlx, extras, voices=voices)
        )
        (target / "README.md").write_text(scaffold.render_readme(target.resolve().name))
    scaffold.write_gitignore(target)
    # The manifest carries what this project uses; the example carries everything it *could*.
    # Shipped with every project because the alternative is reading the docs to discover that
    # remote backends, a bind address, or a tool timeout are configurable at all.
    (target / "stabbur.example.toml").write_text(project.render_example_manifest())
    if choices.template is not None:
        for rel, content in choices.template.files.items():
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)


def _print_scaffold_summary(proj: Path, choices: _WizardChoices, *, uv: bool, git: bool, target: Path) -> None:
    """Print the post-scaffold summary (model/tools/voice + uv/git status + template next-steps)."""
    console.print(f"\n[green]Created[/] {proj}")
    console.print(f"  [dim]model:[/] {choices.model or 'none yet'}")
    console.print(f"  [dim]tools:[/] {', '.join(n for n, _ in choices.mcp) if choices.mcp else 'none'}")
    console.print(f"  [dim]voice:[/] {choices.chat_voice}")
    console.print("  [dim]every option:[/] stabbur.example.toml")
    if uv:
        console.print("  [dim]uv:[/] pyproject.toml (run [bold]uv sync[/] to build the environment)")
    if git:
        ok, status = scaffold.git_init(target)
        console.print(f"  [{'dim' if ok else 'yellow'}]git:[/] {status}")
    if choices.template is not None and choices.template.next_steps:
        console.print(f"\n[bold]Next steps[/]\n{choices.template.next_steps}")


def _warn_if_nested(target: Path) -> None:
    """Warn when this scaffold lands *inside* an existing project, naming the one it will shadow.

    Nesting is allowed on purpose — ``init`` scaffolds where you stand, which is the whole point of
    it — but it is rarely what someone means: discovery stops at the **nearest** ``stabbur.toml``
    (:func:`stabbur.project.discover`), so from here down every command silently binds to the new
    assistant instead of the enclosing one. Searching from ``target``'s parent, so the manifest
    about to be written (or overwritten with ``--force``) is never mistaken for the outer project.
    """
    outer = project.discover(target.resolve().parent)
    if outer is not None:
        console.print(
            f"[yellow]Note:[/] this is inside an existing project ({outer}). "
            "Commands run from here will use the new project, not that one."
        )


def _scaffold_project(
    target: Path,
    model: str | None,
    force: bool,
    git: bool = False,
    uv: bool = True,
    template: str | None = None,
    voices: bool = True,
    upstream: str | None = None,
) -> None:
    """Create ``target`` and scaffold a self-contained project assistant in it.

    Walks the wizard (kind, model, tools, system prompt, spoken-reply voice), then writes
    ``target/stabbur.toml`` and downloads the model into ``target/library/``. The manifest lists
    that directory as the project's only library, so the project ignores this machine's — zip the
    directory up, move it to another machine, and it still runs.

    ``uv`` (default on) also makes it a **self-contained uv project**: a ``pyproject.toml``
    pinning ``stabbur`` + its MCP servers, so ``uv run stabbur serve`` uses the project's own
    environment instead of a global stabbur and runtime ``uvx`` fetches.

    The directory must not already exist: creating a project is making a new nest, and writing
    into a directory that already has things in it is how you end up with two assistants arguing
    over one ``stabbur.toml``. ``--force`` allows it for the case where you meant it.
    """
    if target.exists() and not force:
        console.print(
            f"[red]{target} already exists[/] — `stabbur init` creates a new directory.\n"
            "[dim]Use --force to scaffold into it anyway.[/]"
        )
        raise typer.Exit(1)
    _warn_if_nested(target)
    choices = _gather_choices(
        target.name, model, template, voices_gb=_VOICE_PACKAGE_GB if voices else 0.0, upstream=upstream
    )
    # The wizard's own field wins when the flag was not given: it is the same setting, typed in
    # the place the person was actually looking.
    upstream = upstream or choices.upstream or None
    target.mkdir(parents=True, exist_ok=True)
    _provision(target, choices.model, voices=voices, upstream=upstream)
    _write_project(target, choices, uv=uv, voices=voices, upstream=upstream)
    _print_scaffold_summary(target / "stabbur.toml", choices, uv=uv, git=git, target=target)


@app.command("init")
def init(
    path: Annotated[Path, typer.Argument(help="Directory to create for the new project.")],
    model: _ModelOpt = None,
    force: _ForceOpt = False,
    git: _GitOpt = False,
    uv: _UvOpt = True,
    template: _TemplateOpt = None,
    voices: _VoicesOpt = True,
    upstream: _UpstreamOpt = None,
) -> None:
    """Create a self-contained project assistant in a new directory.

    A project is a purpose-built assistant that owns everything it needs: its model (downloaded
    into `<path>/library/`), its system prompt, its tools, and its own uv environment. `stabbur
    serve`/`chat` run inside it bind to that model and ignore this machine's library and default
    model — so the directory can be zipped up and moved to another machine as it stands.

    It downloads a working package, not just weights: the chat model, the in-chat voice (Kokoro)
    and the good one (VoxCPM2) — a few GB more, and the project can actually speak. `--no-voices`
    skips them.

    `--upstream <url>` binds the project to an OpenAI-compatible server instead: the models run
    there and none are downloaded, while the prompt, tools, voices and UI stay here. `--model`
    then names a model that server already serves.

    Refuses an existing directory (`--force` overrides). `--template dhis2` presets a
    reproducible DHIS2 assistant (model + prompt + bridge + example files).
    """
    _scaffold_project(path, model, force, git, uv, template, voices, upstream)
    run = "uv sync && uv run stabbur serve --ui" if uv else "stabbur serve --ui"
    console.print(f"[dim]Next:[/] cd {path} && {run}")


def _connect_project_tools(
    mcp: list[mcpservers.McpServer],
) -> tuple[dict[str, list[tuple[str, str]]], str | None, list[tuple[str, str]]]:
    """Spawn the given MCP servers and return their real tools, grouped by server.

    Returns ``({server: [(tool, description), ...]}, error, failures)``: ``error`` is a message
    if the whole connect failed, else ``None``; ``failures`` is per-server ``(label, reason)``
    for servers that couldn't start (e.g. an uninstalled optional server) — the rest still work.
    """
    import asyncio  # noqa: PLC0415

    from stabbur import tools as mcp_tools  # noqa: PLC0415

    servers = [m.to_spec() for m in mcp]

    # connect() namespaces tools under a *slugged* prefix (`weather-yr` -> `weather_yr`), so map
    # each assigned prefix back to the raw server name, so the grouped result keys match the names
    # the caller passed in. Straight through assign_prefixes — the one owner of the slug +
    # collision rule — rather than a second copy of it that can drift out of step with connect.
    prefix_to_name = dict(zip(mcp_tools.assign_prefixes(servers), [m.name for m in mcp], strict=True))

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
        prefix, _, tool = name.partition("__")  # tools are namespaced <prefix>__<tool>
        grouped.setdefault(prefix_to_name.get(prefix, prefix), []).append((tool or name, desc))
    return grouped, None, failures


@project_app.command("show")
def project_(
    card: Annotated[
        bool, typer.Option("--card", "-c", help="Also render the bound model's model card (README).")
    ] = False,
) -> None:  # project_ to avoid shadowing the imported project module
    """Show the active project (stabbur.toml): model + card, system prompt, and live tools.

    A project is a reproducible assistant — `stabbur chat` and `stabbur serve --ui` here
    default to this model, system prompt, and MCP tool servers. This connects to
    those servers to list the tools they actually expose (``--card`` also prints
    the bound model's model card).

    The manifest is the nearest one at or above this directory, so this is also the answer to
    "which project am I in?" — when it was found further up, its full path is printed.
    """
    proj = project.load()
    if proj is None:
        console.print(
            "[yellow]No stabbur.toml here or in any parent directory.[/] "
            "Run [bold]stabbur project init[/] to scaffold a project."
        )
        raise typer.Exit(1)

    # Say *which* manifest applies whenever it isn't the one in this directory: from a subdirectory
    # the commands bind to a project you can't see in `ls`, and a silent "Project" header would
    # leave you guessing which one. In the project root the header stays exactly as it was.
    found = proj.manifest_path
    where = "stabbur.toml" if found is None or found.resolve().parent == Path.cwd() else str(found)
    console.print(f"\n[bold]Project[/] [dim]({where})[/]\n")

    # Model — full detail card if it's in the library, else just the (missing) name.
    model = None
    if proj.model:
        matches = library_ops.find(proj.model)
        if matches:
            model = matches[0]
            _print_model_card(model, tags.tags_for(model.library_root, model.name))
        else:
            console.print(f"[bold]Model[/]  {proj.model}  [yellow]not in library — run stabbur project init[/]\n")
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
            console.print(f"  [red]could not connect:[/] [dim]{escape(error)}[/]")
        failed = {label: reason for label, reason in failures}
        for m in servers:
            command = " ".join([m.command, *m.args])
            if m.name in failed:  # this one couldn't start; the others still work
                console.print(
                    f"  [yellow]{m.name}[/] [dim]({command})[/] — [red]failed:[/] [dim]{escape(failed[m.name])}[/]"
                )
                if m.command.startswith("stabbur-mcp-web"):
                    console.print(
                        "    [dim]hint:[/] the web reader is optional — install it with [bold]make install-web[/]"
                    )
                continue
            tools_here = grouped.get(m.name, [])
            console.print(f"  [cyan]{m.name}[/] [dim]({command})[/] — [bold]{len(tools_here)}[/] tool(s)")
            for tool, desc in tools_here:
                summary = desc.splitlines()[0][:80] if desc else ""
                console.print(f"    [white]{escape(tool)}[/]{f'  [dim]{escape(summary)}[/]' if summary else ''}")

    # Model card (README) — opt-in, since it can be long.
    if card and model is not None:
        card_path = cards.find_card(model.path)
        console.print("\n[bold]Model card[/]")
        if card_path and card_path.is_file():
            from rich.markdown import Markdown  # noqa: PLC0415

            console.print(Markdown(card_path.read_text(errors="replace")[:20_000]))
        else:
            console.print("  [dim]no model card found[/]")


def _configure_state(proj: project.Project) -> dict[str, Any]:
    """Everything the configure screen shows: what the project binds, holds, and could hold."""
    from stabbur import plugins  # noqa: PLC0415 - plugin discovery is slow; only this screen needs it
    from stabbur.voice import registry as voice_registry  # noqa: PLC0415

    scanned = library_ops.scan()
    in_library = {m.name for m in scanned}
    chat = [m for m in scanned if m.generative and not m.is_ollama]
    models = [
        configure_tui.ModelOption(name=m.name, detail=f"{m.model_format.value} · {m.size_human}", present=True)
        for m in sorted(chat, key=lambda m: m.name)
    ]
    # Plus the curated starters it doesn't have yet, so swapping to a better model is a choice
    # here rather than a separate `library pull` to look up.
    models += [
        configure_tui.ModelOption(name=c.id, detail=c.note, present=False) for c in _CURATED if c.id not in in_library
    ]
    voices = [
        configure_tui.VoiceOption(
            id=v.id,
            label=f"{v.display_name} — {v.kind.value}, {v.size_hint}",
            present=v.repo in in_library,
        )
        for v in voice_registry.BUILTIN
        if v.supported
    ]
    entries = [
        configure_tui.LibraryEntry(name=m.name, size_human=m.size_human) for m in sorted(scanned, key=lambda m: m.name)
    ]
    return {
        "name": proj.directory.name,
        "models": models,
        "current_model": proj.model or "",
        "system_prompt": proj.system_prompt,
        "chat_voice": proj.chat_voice,
        "voice_enabled": proj.voice_enabled,
        "servers": plugins.advertised_servers(plugins.manager()),
        "enabled_tools": {s.name for s in mcpservers.read_project(proj.directory)},
        "voices": voices,
        "library": entries,
    }


def _apply_plan(proj: project.Project, plan: configure_tui.ConfigurePlan) -> None:
    """Perform a configure plan: rewrite the manifest + tools, then pull and remove.

    Writes first, downloads second: the settings are what the user came for, and a pull that
    fails (or is interrupted) must not lose them. Each download and deletion reports itself, so
    a plan that changes gigabytes on disk says so while it happens.
    """
    from stabbur.voice import registry as voice_registry  # noqa: PLC0415

    manifest = proj.manifest_path
    assert manifest is not None  # a loaded project always has one
    manifest.write_text(
        project.render_manifest(
            model=plan.model,
            system_prompt=plan.system_prompt,
            libraries=proj.libraries,
            chat_voice=plan.chat_voice,
            voice_enabled=plan.voice_enabled,
            assistant=proj.assistant,
            registry=proj.registry,
        )
    )
    console.print(f"[green]Wrote[/] {manifest}")

    wanted = {name: command for name, command in plan.tools}
    for existing in mcpservers.read_project(proj.directory):
        if existing.name not in wanted:
            mcpservers.remove(existing.name, glob=False, project_dir=proj.directory)
            console.print(f"[dim]Removed tool[/] {existing.name}")
    have = {s.name for s in mcpservers.read_project(proj.directory)}
    for name, command in wanted.items():
        if name not in have:
            mcpservers.add(_to_mcp_server(name, command), glob=False, project_dir=proj.directory)
            console.print(f"[green]Added tool[/] {name}")

    root = library_ops.roots()[0]
    for vid in plan.pull_voices:
        console.print(f"Downloading [bold]{vid}[/] …")
        try:
            result = catalog_ops.pull(ModelSource.voice, vid, library_root=root)
        except Exception as exc:  # noqa: BLE001 - one failure must not abort the rest of the plan
            console.print(f"  [yellow]failed[/] — {escape(str(exc))}")
            continue
        console.print(f"  [green]done[/] {result.size_human}")

    for name in plan.remove_models:
        matches = [m for m in library_ops.scan() if m.name == name]
        if not matches:
            continue
        files, freed = library_ops.remove(matches[0])
        console.print(f"[green]Removed[/] {name} [dim]({files} files, {_human_size(freed)} freed)[/]")
    # A removed voice model can leave the manifest naming a voice that is gone; say so rather
    # than let the next Listen fail with a 404 from a setting the user just saved.
    if plan.chat_voice and plan.chat_voice.startswith("model:"):
        spec = voice_registry.get(plan.chat_voice.removeprefix("model:"))
        if spec is None or not any(m.name == spec.repo for m in library_ops.scan()):
            console.print(f"[yellow]Note[/] the reply voice {plan.chat_voice!r} is not in this project's library.")


@app.command("configure")
def configure() -> None:
    """Change this project's assistant: model, prompt, tools, voice, and library.

    The settings `stabbur init` asked for once, editable now that you know what you are
    building. Downloads a model or voice you add, removes what you deselect from the project's
    library, and rewrites `stabbur.toml` + `.mcp.json`. Nothing is written until you save.

    Project-scoped: run it inside a project (`stabbur init` makes one). The two machine-wide
    defaults live in `stabbur config`.
    """
    proj = project.load()
    if proj is None or proj.manifest_path is None:
        console.print("[red]No project here[/] — `stabbur init <dir>` creates one.")
        raise typer.Exit(1)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print("[red]No terminal for the configure screen[/] — edit stabbur.toml directly instead.")
        raise typer.Exit(1)
    plan = configure_tui.run_configure(**_configure_state(proj))
    if plan is None:
        console.print("[dim]Cancelled — nothing changed.[/]")
        return
    _apply_plan(proj, plan)
    console.print("\n[green]Done.[/] [dim]stabbur project show[/] to see the result.")
