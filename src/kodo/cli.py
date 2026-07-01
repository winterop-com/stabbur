"""Command-line interface for browsing, pulling, and running local models."""

from collections.abc import Awaitable
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from kodo import catalog as catalog_ops
from kodo import config, project, runtime
from kodo import library as library_ops
from kodo.config import get_settings
from kodo.models import CuratedModel, ModelFormat, ModelSource, _human_size
from kodo.sources import huggingface as hf

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


# Curated starter models for `kodo init` (verified GGUF repos; kept small).
_CURATED: list[CuratedModel] = [
    CuratedModel(id="unsloth/SmolLM2-360M-Instruct-GGUF", note="tiny — runs on anything (~350 MB)"),
    CuratedModel(id="unsloth/Qwen3.5-4B-GGUF", note="compact + good at tools (~2.5 GB)"),
]


def _fmt_cell(model_format: ModelFormat) -> str:
    """Render a format value with its color style."""
    return f"[{_FORMAT_STYLE[model_format]}]{model_format.value}[/]"


def _project_toml(model: str, library_root: Path) -> str:
    """Render a kodo.toml: library config + the assistant bound to ``model``.

    This is kodo's primary config file — no ``.env`` needed. Top-level keys
    configure the library/runtime; ``[project]`` and ``[[mcp]]`` define the
    assistant. Any value can be overridden per machine with a ``KODO_*`` env var.
    """
    return (
        "# kodo config — library location + this project's assistant, in one file.\n"
        "# kodo's primary config (no .env needed). Override any value per machine\n"
        "# with a KODO_* env var (e.g. KODO_LIBRARY_ROOT).\n\n"
        "# Where the model library lives (often the external drive).\n"
        f'library_root = "{library_root}"\n\n'
        "# The assistant this project defines.\n"
        "[project]\n"
        f'model = "{model}"\n'
        'system_prompt = ""\n\n'
        "# Tools via MCP (repeatable; uncomment and point at an MCP server):\n"
        "# [[mcp]]\n"
        '# name = "dhis2"\n'
        '# command = "dhis2w-mcp-bridge"\n'
    )


def _library_names() -> set[str]:
    """Names of models already in the library, plus their bare repo/tag forms."""
    names: set[str] = set()
    for m in library_ops.scan():
        names.add(m.name.lower())
        names.add(m.name.rsplit("/", 1)[-1].lower())
    return names


@app.command("list")
def list_models() -> None:
    """List the models in your library — what you've pulled, ready to run.

    The library spans your drive (``KODO_LIBRARY_ROOT``) plus an always-local
    root, so models kept locally still work when the drive is unplugged. To
    browse models in your app caches that you *could* pull, use ``kodo sources``.
    """
    settings = get_settings()
    models = [m for m in library_ops.scan() if m.generative]
    drive_off = not settings.library_root.is_dir()
    if not models:
        console.print("Your library is empty.")
        if drive_off:
            console.print(f"[yellow]Drive offline:[/] [dim]{settings.library_root}[/] is not mounted.")
        console.print("[dim]Pull one with[/] kodo pull [dim](or[/] --local[dim]) · browse with[/] kodo sources")
        return

    total = _human_size(sum(m.size_bytes for m in models))
    console.print(f"\n[bold]{len(models)} models · {total}[/] in your library\n")
    if drive_off:
        console.print(
            f"[yellow]Note: drive offline[/] ([dim]{settings.library_root}[/]) — showing local models only.\n"
        )
    for fmt in sorted({m.model_format for m in models}, key=lambda f: f.value):
        rows = sorted((m for m in models if m.model_format is fmt), key=lambda m: m.name)
        subtotal = _human_size(sum(m.size_bytes for m in rows))
        title = f"[{_FORMAT_STYLE[fmt]}][bold]{fmt.value}[/][/]  [dim]{len(rows)} · {subtotal}[/]"
        table = Table(box=box.SIMPLE_HEAD, title=title, title_justify="left", pad_edge=False)
        table.add_column("SIZE", justify="right")
        table.add_column("NAME", style="white")
        for m in rows:
            table.add_row(m.size_human, m.name)
        console.print(table)


# Hidden short alias: `kodo ls` == `kodo list`.
app.command("ls", hidden=True)(list_models)


def _pull_all(source: ModelSource, root: Path | None, move: bool) -> None:
    """Import every model from ``source``'s local store into the library.

    Idempotent (skips models already in the library) and resilient (one failing
    model logs and the batch continues).
    """
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


@app.command()
def pull(
    source: Annotated[ModelSource, typer.Argument(help="Source the model belongs to.")],
    name: Annotated[
        str | None,
        typer.Argument(help="Model id (HF repo, Ollama model:tag, LM Studio path). Omit with --all."),
    ] = None,
    all_: Annotated[
        bool,
        typer.Option("--all", "-a", help="Import every model from this source's local store (idempotent)."),
    ] = False,
    move: Annotated[
        bool,
        typer.Option("--move", help="Delete the local source after a verified copy (frees local disk)."),
    ] = False,
    local: Annotated[
        bool,
        typer.Option("--local", help="Pull into the always-local root (works when the drive is unplugged)."),
    ] = False,
) -> None:
    """Pull (or move) a model into the library (the drive, or --local).

    Give a model name for one model, or --all to import everything from this
    source's local store (skipping models already in the library).
    """
    root = get_settings().local_root if local else None
    if all_ == (name is not None):
        console.print("[red]Provide either a model name or --all, not both.[/]")
        raise typer.Exit(2)

    if all_:
        _pull_all(source, root, move)
        return

    assert name is not None  # narrowed by the guard above
    verb = "Moving" if move else "Pulling"
    typer.echo(f"{verb} {source.value}:{name}{' (local)' if local else ''} ...")
    try:
        result = catalog_ops.pull(source, name, library_root=root, move=move)
    except Exception as exc:  # noqa: BLE001 - a pull can fail many ways (disk, network, HF Hub); surface it cleanly
        typer.secho(f"Pull failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    suffix = " (local copy removed)" if move else ""
    typer.echo(f"Done: {result.file_count} files, {result.size_human} -> {result.destination}{suffix}")


@app.command()
def sources(
    source: SourceOption = None,
    show_all: Annotated[bool, typer.Option("--all", "-a", help="Include embedding/vision/partial entries.")] = False,
) -> None:
    """Browse models in your app caches (HF / Ollama / LM Studio) you can pull.

    These live in caches on this machine (e.g. ~/.cache/huggingface) — *not* your
    library. The IN LIBRARY column marks what you've already pulled; pull leftover
    local models onto the drive with ``kodo pull --move`` to free local disk.
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
    console.print("[dim]Caches on this machine, not your library — see[/] kodo list [dim]for your library.[/]\n")
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


@app.command()
def init(
    model: Annotated[str | None, typer.Option("--model", help="Model to bind (skips the curated picker).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing kodo.toml.")] = False,
) -> None:
    """Scaffold a project here (kodo.toml) and ensure its model is in the library.

    Idempotent: only pulls the model if it's missing. When no --model is given,
    offers a small curated set and pulls the chosen one into the always-local
    library so it works even without the drive.
    """
    proj = Path("kodo.toml")
    if proj.exists() and not force:
        console.print("[red]kodo.toml already exists[/] here — use --force to overwrite.")
        raise typer.Exit(1)

    if model is None:
        console.print("[bold]Pick a starter model:[/]")
        for i, curated in enumerate(_CURATED, 1):
            console.print(f"  {i}. {curated.id}  [dim]{curated.note}[/]")
        choice = typer.prompt("Number", default="1")
        try:
            model = _CURATED[int(choice) - 1].id
        except (ValueError, IndexError):
            console.print(f"[red]Not a valid choice: {choice!r}[/]")
            raise typer.Exit(1) from None

    if library_ops.find(model):
        console.print(f"[green]✓[/] {model} is already in your library")
    else:
        console.print(f"Pulling [bold]{model}[/] into your local library …")
        try:
            catalog_ops.pull(ModelSource.huggingface, model, library_root=get_settings().local_root)
        except Exception as exc:  # noqa: BLE001 - surface pull/network failures
            console.print(f"[red]Pull failed:[/] {exc}")
            raise typer.Exit(1) from exc

    proj.write_text(_project_toml(model, get_settings().library_root))
    console.print(f"\n[green]Created kodo.toml[/] (model: {model})")
    console.print(f"[dim]Next:[/] kodo serve --ui  [dim]· or[/] kodo chat {model.rsplit('/', 1)[-1]}")


@app.command()
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
    table.add_column("MODEL", style="white")
    for r in results:
        table.add_row(f"{r.downloads:,}", f"{r.likes:,}", r.id)
    console.print(table)
    console.print("\n[dim]Pull one with[/] kodo pull huggingface <model>")


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
                f"kodo pull {hit.source.value} {hit.name}"
            )
        else:
            console.print(f"[red]{name!r} is not in the library[/] ([dim]{get_settings().library_root}[/]).")
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
        store = get_settings().library_root / "ollama"
        console.print(f"[red]{model.name!r} is an Ollama model[/] — kodo runs GGUF/MLX, not Ollama's format.")
        console.print(f"[yellow]Run it with Ollama:[/]  OLLAMA_MODELS={store} ollama run {model.name}")
        raise typer.Exit(1)
    return model


@app.command()
def run(
    name: Annotated[str, typer.Argument(help="Library model name (full path or bare repo name).")],
    model_format: FormatOption = None,
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port.")] = 8080,
) -> None:
    """Expose a library model's raw runtime server (foreground, OpenAI-compatible).

    GGUF runs on llama.cpp's llama-server; MLX on mlx_lm.server. This is the raw
    runtime (no kodo proxy) on a fixed port — handy for pointing an external
    OpenAI client at one model. For chatting, use ``kodo chat``; for the browser
    UI and model switching, ``kodo serve``.
    """
    model = _resolve_library_model(name, model_format)
    console.print(f"\nServing [bold]{_fmt_cell(model.model_format)}[/] {model.name}")
    console.print(f"  OpenAI API:  http://{host}:{port}/v1")
    console.print("  [dim]Ctrl-C to stop[/]\n")
    try:
        runtime.run(model, host, port)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


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
    render: Annotated[
        bool,
        typer.Option("--render", help="Render each reply as Markdown (code highlighting etc); no live streaming."),
    ] = False,
) -> None:
    """Chat with a library model: clean REPL, one-shot with ``-p``, tools with ``--mcp``.

    In a project dir, ``kodo.toml`` supplies the default model, its MCP tool
    servers, and a system prompt; ``--mcp`` flags add to (not replace) those.
    ``--render`` prints formatted Markdown instead of streaming; it's ignored for
    ``-p`` so scripted output stays plain.
    """
    proj = project.load()
    model_name = name or (proj.model if proj else None)
    if model_name is None:
        console.print("[red]No model given[/] — pass one, or set [project].model in kodo.toml.")
        raise typer.Exit(1)
    model = _resolve_library_model(model_name, model_format)
    mcp_commands = list(mcp) + [m.command for m in (proj.mcp if proj else [])]
    system_prompt = proj.system_prompt if proj else ""
    render_reply = render and prompt is None  # -p stays plain for scripting
    try:
        if prompt is not None and not mcp_commands:
            # Scripted one-shot, no tools: print only the reply to stdout (clean for
            # piping); errors go to stderr. Everything else goes through the one
            # interactive/agent path below (tools optional, empty list = plain chat).
            print(runtime.generate(model, prompt, max_tokens, system_prompt))  # noqa: T201
        else:
            _chat_with_tools(model, mcp_commands, prompt, max_tokens, system_prompt, render=render_reply)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


async def _run_cancelable(coro: Awaitable[str]) -> tuple[str | None, bool]:
    """Await ``coro``, cancelling it if the user presses ESC. Returns (result, canceled).

    asyncio cancellation interrupts the in-flight request even when it's produced no
    output yet (a buffered tool-mode reply), which a between-iterations flag can't.
    ESC watching needs a TTY (raw stdin); piped/non-interactive runs just await.
    """
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415
    import sys  # noqa: PLC0415

    task: asyncio.Task[str] = asyncio.ensure_future(coro)
    try:
        import termios  # noqa: PLC0415
        import tty  # noqa: PLC0415
    except ImportError:  # no raw-tty support (e.g. Windows)
        return await task, False
    if not sys.stdin.isatty():
        return await task, False

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    loop = asyncio.get_running_loop()

    def _on_key() -> None:
        try:
            if os.read(fd, 1) == b"\x1b":  # ESC
                task.cancel()
        except OSError:
            pass

    try:
        tty.setcbreak(fd)
        loop.add_reader(fd, _on_key)
        try:
            return await task, False
        except asyncio.CancelledError:
            return None, True
    finally:
        loop.remove_reader(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _chat_with_tools(
    model: library_ops.LibraryModel,
    mcp_commands: list[str],
    prompt: str | None,
    max_tokens: int | None,
    system_prompt: str = "",
    render: bool = False,
) -> None:
    """Run the tool-calling agent loop over one or more MCP servers (streamed reply).

    ``render`` buffers each reply and prints it as Markdown when done (no live
    token stream); tool activity still shows live.
    """
    import asyncio  # noqa: PLC0415
    import shlex  # noqa: PLC0415

    from rich.progress import Progress  # noqa: PLC0415

    from kodo import (
        agent,  # noqa: PLC0415
        chatui,  # noqa: PLC0415
    )
    from kodo import tools as mcp_tools  # noqa: PLC0415

    commands = [shlex.split(c) for c in mcp_commands]
    # Tool activity is meta → stderr, so `-p` stdout stays just the answer.
    err = Console(stderr=True)

    # Per-turn state: a "thinking" spinner shown until the first token/tool-call
    # arrives (model prefill latency otherwise looks dead), and whether the reply
    # label has been printed yet.
    turn_status: Progress | None = None
    turn_labeled = True

    def _first_output() -> None:
        nonlocal turn_status, turn_labeled
        if turn_status is not None:
            turn_status.stop()
            turn_status = None
        if not turn_labeled:
            chatui.assistant_prefix(err, inline=False)
            turn_labeled = True

    def _think() -> None:
        nonlocal turn_status
        turn_status = chatui.thinking(err)
        turn_status.start()

    def on_event(kind: str, detail: str) -> None:
        _first_output()
        icon = "[cyan]⚙[/]" if kind == "call" else "[grey62]↳[/]"
        err.print(f"  {icon} [grey62]{detail[:200]}[/]")

    def on_token(text: str) -> None:
        _first_output()
        print(text, end="", flush=True)  # noqa: T201

    def seed() -> list[dict[str, object]]:
        return [{"role": "system", "content": system_prompt}] if system_prompt else []

    async def _run(base: str) -> None:
        nonlocal turn_labeled
        async with mcp_tools.connect(commands) as toolset:
            if prompt is not None:
                turn_labeled = True  # -p mode: stdout is just the answer, no label
                _think()
                await agent.run(
                    base, [*seed(), {"role": "user", "content": prompt}], toolset, max_tokens, on_event, on_token
                )
                _first_output()
                print()  # noqa: T201 - newline after streamed answer
            else:
                chatui.header(
                    console,
                    model=model.name,
                    model_format=model.model_format.value,
                    tools=toolset.names,
                    server=base,
                    esc_cancel=True,
                )
                try:
                    import readline  # noqa: PLC0415 - up-arrow recall + line editing for input()

                    readline.set_history_length(1000)
                except ImportError:
                    pass
                history: list[dict[str, object]] = seed()
                while True:
                    try:
                        user = input(chatui.USER_PROMPT).strip()
                    except (EOFError, KeyboardInterrupt):
                        print()  # noqa: T201
                        break
                    if not user or user in ("/exit", "/quit", "exit", "quit"):
                        if user in ("/exit", "/quit", "exit", "quit"):
                            break
                        continue
                    mark = len(history)  # roll-back point if the turn is canceled
                    history.append({"role": "user", "content": user})
                    turn_labeled = render  # render mode labels+renders at the end, not inline
                    _think()
                    # In render mode use the returned text (no live tokens); the spinner
                    # keeps spinning until the reply is complete, then we render Markdown.
                    # ESC cancels the turn (returns to the prompt) — better than Ctrl-C.
                    reply, canceled = await _run_cancelable(
                        agent.run(base, history, toolset, max_tokens, on_event, None if render else on_token)
                    )
                    _first_output()
                    if canceled:
                        del history[mark:]  # drop the user turn + any partial tool turns
                        err.print("[grey62](canceled)[/]")
                        continue
                    if not (reply or "").strip():
                        err.print("[grey62](no response)[/]")
                    elif render:
                        chatui.render_reply(console, reply or "")
                    else:
                        print("\n")  # noqa: T201

    with runtime._serve(model) as base:
        asyncio.run(_run(base))


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

    # Propagate to the (possibly reloaded) worker process via env vars — with
    # --reload, uvicorn imports the app in a fresh process where the CLI callback's
    # in-memory overrides (--runtime-port, --debug) don't exist.
    if ui:
        os.environ["KODO_SERVE_UI"] = "true"
    if model is not None:
        os.environ["KODO_SERVE_MODEL"] = model
    if config.runtime_port_override() is not None:
        os.environ["KODO_RUNTIME_PORT"] = str(config.runtime_port_override())
    if config.debug_enabled():
        os.environ["KODO_DEBUG"] = "true"

    get_settings.cache_clear()
    settings = get_settings()
    # Precedence: --host/--port > KODO_HOST/KODO_PORT/kodo.toml > auto-pick a free port.
    bind_host = host or settings.host
    bind_port = port or settings.port or runtime.find_free_port()
    base = f"http://{bind_host}:{bind_port}"
    console.print("\n[bold]kodo[/]")
    if model is not None:
        console.print(f"  Locked:   [bold]{model}[/]")
    if ui:
        if settings.frontend_dir.is_dir():
            console.print(f"  UI:       [link={base}]{base}[/]")
        else:
            console.print(f"  [yellow]UI not built[/] — expected at [dim]{settings.frontend_dir}[/]; serving API only")
    console.print(f"  API:      [link={base}]{base}[/]")
    console.print(f"  Docs:     [link={base}/docs]{base}/docs[/]")
    console.print("  [dim]Ctrl-C to stop[/]\n")
    uvicorn.run(
        "kodo.app:app",
        host=bind_host,
        port=bind_port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
