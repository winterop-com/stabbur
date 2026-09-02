"""`stabbur init` - scaffold a project assistant - and `stabbur project` - inspect one."""

import sys
from pathlib import Path
from typing import Annotated

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
    _UvOpt,
    console,
)
from stabbur.cli.init_wizard import CHAT_PROMPT, DEFAULT_VOICE, ModelChoice, run_wizard
from stabbur.models import ModelSource, ProjectTemplate
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
    template: ProjectTemplate | None = None


def _model_choices() -> list[ModelChoice]:
    """The models the wizard offers: the curated starters, best-first for a new project.

    Deliberately not the machine library: a project downloads its own copy (see
    :func:`_provision_model`), so offering what happens to be on this machine's drive would
    suggest a link between the two that a self-contained project does not have.
    """
    return [ModelChoice(name=c.id, detail=c.note) for c in _CURATED]


def _gather_choices(name: str, model: str | None, template: str | None) -> _WizardChoices:
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
        return _WizardChoices(model=model, mcp=[], system_prompt=CHAT_PROMPT, chat_voice=DEFAULT_VOICE)

    from stabbur import plugins  # noqa: PLC0415 - plugin discovery is slow; only the wizard needs it

    result = run_wizard(name=name, models=_model_choices(), servers=plugins.advertised_servers(plugins.manager()))
    if result is None:
        console.print("[dim]Cancelled — nothing was created.[/]")
        raise typer.Exit(1)
    return _WizardChoices(
        model=model or result.model,
        mcp=result.mcp,
        system_prompt=result.system_prompt,
        chat_voice=result.chat_voice,
    )


def _provision_model(target: Path, model: str) -> None:
    """Download ``model`` into the project's own ``library/``.

    Always a fresh download, never a copy out of the machine library: a project is meant to be
    self-contained — zip the directory, move it to another machine, and it still runs — so it owns
    its weights outright rather than inheriting a copy whose provenance is this one drive.
    """
    local_lib = target / _LOCAL_LIBRARY
    local_lib.mkdir(parents=True, exist_ok=True)
    console.print(f"\nDownloading [bold]{model}[/] into {_LOCAL_LIBRARY}/ …")
    _pull_or_exit(model, local_lib)


def _write_project(target: Path, choices: _WizardChoices, *, uv: bool) -> None:
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
            scaffold.render_pyproject(target.resolve().name, choices.mcp, mlx, extras)
        )
        (target / "README.md").write_text(scaffold.render_readme(target.resolve().name))
    if choices.template is not None:
        for rel, content in choices.template.files.items():
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)


def _print_scaffold_summary(proj: Path, choices: _WizardChoices, *, uv: bool, git: bool, target: Path) -> None:
    """Print the post-scaffold summary (model/tools/voice + uv/git status + template next-steps)."""
    console.print(f"\n[green]Created[/] {proj}")
    console.print(f"  [dim]model:[/] {choices.model}")
    console.print(f"  [dim]tools:[/] {', '.join(n for n, _ in choices.mcp) if choices.mcp else 'none'}")
    console.print(f"  [dim]voice:[/] {choices.chat_voice}")
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
    choices = _gather_choices(target.name, model, template)
    target.mkdir(parents=True, exist_ok=True)
    _provision_model(target, choices.model)
    _write_project(target, choices, uv=uv)
    _print_scaffold_summary(target / "stabbur.toml", choices, uv=uv, git=git, target=target)


@app.command("init")
def init(
    path: Annotated[Path, typer.Argument(help="Directory to create for the new project.")],
    model: _ModelOpt = None,
    force: _ForceOpt = False,
    git: _GitOpt = False,
    uv: _UvOpt = True,
    template: _TemplateOpt = None,
) -> None:
    """Create a self-contained project assistant in a new directory.

    A project is a purpose-built assistant that owns everything it needs: its model (downloaded
    into `<path>/library/`), its system prompt, its tools, and its own uv environment. `stabbur
    serve`/`chat` run inside it bind to that model and ignore this machine's library and default
    model — so the directory can be zipped up and moved to another machine as it stands.

    Refuses an existing directory (`--force` overrides). `--template dhis2` presets a
    reproducible DHIS2 assistant (model + prompt + bridge + example files).
    """
    _scaffold_project(path, model, force, git, uv, template)
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
