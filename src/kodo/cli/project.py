"""`kodo project` - scaffold (init/new) and inspect (show) a project assistant."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict
from rich.panel import Panel

from kodo import (
    cards,
    mcpservers,
    project,
    tags,
)
from kodo import catalog as catalog_ops
from kodo import library as library_ops
from kodo.cli._app import project_app
from kodo.cli._common import (
    _CURATED,
    _LOCAL_LIBRARY,
    _ForceOpt,
    _GitOpt,
    _LocalOpt,
    _ModelOpt,
    _print_model_card,
    _TemplateOpt,
    _to_mcp_server,
    _UvOpt,
    console,
)
from kodo.models import ModelSource, ProjectTemplate
from kodo.project import scaffold
from kodo.project.templates import TEMPLATES


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


class _WizardChoices(BaseModel):
    """The choices that define a scaffolded project — from a template preset or the wizard."""

    model_config = ConfigDict(frozen=True)

    model: str
    mcp: list[tuple[str, str]]  # (name, command) MCP servers to write into .mcp.json
    system_prompt: str
    chat_voice: str
    template: ProjectTemplate | None = None


def _gather_choices(model: str | None, template: str | None) -> _WizardChoices:
    """Resolve the scaffolding choices: a named template preset, or walk the interactive wizard.

    Gathered up front so canceling the wizard (Ctrl-C) leaves nothing behind — the caller creates
    the project directory only after this returns.
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
            chat_voice=tmpl.chat_voice or "kokoro:af_heart",
            template=tmpl,
        )
    console.print("\n[bold]0. Kind[/] [dim]— tailors the defaults[/]")
    console.print("  1. Chat  [dim]— text conversation (replies can still be spoken)[/]")
    console.print("  2. Voice  [dim]— talk to it: mic dictation in, spoken replies out[/]")
    voice_project = typer.prompt("Number", default="1").strip() == "2"
    default_prompt = (
        "You are a friendly voice assistant. Keep replies short and natural for speech."
        if voice_project
        else "You are a concise, helpful assistant."
    )
    return _WizardChoices(
        model=model or _pick_model_interactive(),
        mcp=_pick_tools_interactive(),
        system_prompt=typer.prompt("\n3. System prompt", default=default_prompt),
        # Kokoro is tiny, so every project can speak replies; a voice project picks which voice.
        chat_voice=(
            typer.prompt("\n4. Spoken-reply voice (Kokoro)", default="kokoro:af_heart")
            if voice_project
            else "kokoro:af_heart"
        ),
    )


def _provision_model(target: Path, model: str, local: bool) -> tuple[bool, list[library_ops.LibraryModel]]:
    """Make ``model`` available: use the shared library, or build a project-local ``library/``.

    Returns ``(use_local, existing)`` — whether the project ships its own library and the matching
    library models found (empty if none). Pulls (or local-copies) the model as needed.
    """
    shared = library_ops.configured()
    existing = library_ops.find(model) if shared else []
    use_local = local or not shared  # --local/--copy forces it; no KODO_LIBRARY_ROOT falls back to it
    if not use_local:
        if existing:
            console.print(f"\n[green]✓[/] {model} is in your library")
        else:
            console.print(f"\nPulling [bold]{model}[/] into your library …")
            _pull_or_exit(model, None)
        return use_local, existing
    local_lib = target / _LOCAL_LIBRARY
    local_lib.mkdir(parents=True, exist_ok=True)
    if existing and existing[0].is_ollama:
        # Ollama models live in a content-addressed store (manifest + shared blobs); they can't be
        # copied into a loose project-local library. Reject cleanly instead of crashing on copytree.
        console.print(
            f"[red]{model!r} is an Ollama model[/] — it can't be copied into a project-local library.\n"
            "Drop `--local`/`--copy` (use the shared library), or pick a GGUF/MLX model."
        )
        raise typer.Exit(1)
    if existing:
        console.print(f"\nCopying [bold]{model}[/] into [bold]{_LOCAL_LIBRARY}/[/] (local-disk copy) …")
        scaffold.copy_model_local(existing[0], local_lib)
    elif not shared:
        console.print(f"\n[yellow]No KODO_LIBRARY_ROOT set[/] — downloading [bold]{model}[/] into {_LOCAL_LIBRARY}/.")
        _pull_or_exit(model, local_lib)
    else:
        console.print(f"\nDownloading [bold]{model}[/] into {_LOCAL_LIBRARY}/ …")
        _pull_or_exit(model, local_lib)
    return use_local, existing


def _write_project(
    target: Path, choices: _WizardChoices, *, use_local: bool, existing: list[library_ops.LibraryModel], uv: bool
) -> None:
    """Write ``kodo.toml`` + ``.mcp.json`` (and, for a uv project, pyproject/README + template files)."""
    (target / "kodo.toml").write_text(
        project.render_manifest(
            model=choices.model,
            system_prompt=choices.system_prompt,
            local_library_dir=_LOCAL_LIBRARY if use_local else None,
            chat_voice=choices.chat_voice,
        )
    )
    # Tools go in the standard .mcp.json (not kodo.toml). In a uv project the servers are pinned
    # deps that run straight off PATH, so drop the runtime `uvx ` fetch from each command.
    scaffold_mcp = [(name, scaffold.strip_uvx(command)) for name, command in choices.mcp] if uv else choices.mcp
    for entry_name, command in scaffold_mcp:
        mcpservers.add(_to_mcp_server(entry_name, command), glob=False, project_dir=target)
    if uv:
        mlx = "mlx" in choices.model.lower() or bool(existing and existing[0].model_format == "mlx")
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
    choices = _gather_choices(model, template)
    target.mkdir(parents=True, exist_ok=True)
    use_local, existing = _provision_model(target, choices.model, local)
    _write_project(target, choices, use_local=use_local, existing=existing, uv=uv)
    _print_scaffold_summary(proj, choices, uv=uv, git=git, target=target)


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

    # connect() namespaces tools under a *slugged* prefix (`weather-yr` -> `weather_yr`), so map
    # each assigned prefix back to the raw server name — mirroring connect's slug + collision
    # disambiguation — so the grouped result keys match the names the caller passed in.
    prefix_to_name: dict[str, str] = {}
    used: dict[str, int] = {}
    for m in mcp:
        base = mcp_tools._server_prefix(m.name, [m.command, *m.args])
        n = used.get(base, 0)
        used[base] = n + 1
        prefix_to_name[base if n == 0 else f"{base}{n + 1}"] = m.name

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
