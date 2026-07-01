"""Command-line interface for browsing, pulling, and running local models."""

from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from kodo import catalog as catalog_ops
from kodo import library as library_ops
from kodo import runtime
from kodo.config import get_settings
from kodo.models import ModelFormat, ModelSource, _human_size
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

SourceOption = Annotated[
    ModelSource | None,
    typer.Option("--source", "-s", help="Limit to a single source."),
]
FormatOption = Annotated[
    ModelFormat | None,
    typer.Option("--format", "-f", help="Disambiguate when a model exists in multiple formats."),
]


def _fmt_cell(model_format: ModelFormat) -> str:
    """Render a format value with its color style."""
    return f"[{_FORMAT_STYLE[model_format]}]{model_format.value}[/]"


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

    The library spans your drive (``KODO_BACKUP_ROOT``) plus an always-local
    root, so models kept locally still work when the drive is unplugged. To
    browse models in your app caches that you *could* pull, use ``kodo sources``.
    """
    settings = get_settings()
    models = [m for m in library_ops.scan() if m.generative]
    drive_off = not settings.backup_root.is_dir()
    if not models:
        console.print("Your library is empty.")
        if drive_off:
            console.print(f"[yellow]Drive offline:[/] [dim]{settings.backup_root}[/] is not mounted.")
        console.print("[dim]Pull one with[/] kodo pull [dim](or[/] --local[dim]) · browse with[/] kodo sources")
        return

    total = _human_size(sum(m.size_bytes for m in models))
    console.print(f"\n[bold]{len(models)} models · {total}[/] in your library\n")
    if drive_off:
        console.print(f"[yellow]Note: drive offline[/] ([dim]{settings.backup_root}[/]) — showing local models only.\n")
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


@app.command()
def pull(
    source: Annotated[ModelSource, typer.Argument(help="Source the model belongs to.")],
    name: Annotated[str, typer.Argument(help="Model id (HF repo, Ollama model:tag, LM Studio path).")],
    move: Annotated[
        bool,
        typer.Option("--move", help="Delete the local source after a verified copy (frees local disk)."),
    ] = False,
    local: Annotated[
        bool,
        typer.Option("--local", help="Pull into the always-local root (works when the drive is unplugged)."),
    ] = False,
) -> None:
    """Pull (or move) a single model into the library (the drive, or --local)."""
    root = get_settings().local_root if local else None
    verb = "Moving" if move else "Pulling"
    typer.echo(f"{verb} {source.value}:{name}{' (local)' if local else ''} ...")
    try:
        result = catalog_ops.pull(source, name, backup_root=root, move=move)
    except NotImplementedError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
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
            console.print(f"[red]{name!r} is not in the library[/] ([dim]{get_settings().backup_root}[/]).")
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
    if model.is_ollama:
        # Ollama's GGUFs (e.g. gemma3) don't load in stock llama.cpp — run via Ollama.
        store = get_settings().backup_root / "ollama"
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
    """Serve a library model (OpenAI-compatible API + web chat UI for GGUF).

    GGUF runs on llama.cpp's llama-server; MLX runs on mlx_lm.server.
    """
    model = _resolve_library_model(name, model_format)
    console.print(f"\nServing [bold]{_fmt_cell(model.model_format)}[/] {model.name}")
    if runtime.serves_web_ui(model):
        console.print(f"  Chat UI:     [link=http://{host}:{port}]http://{host}:{port}[/]")
    else:
        console.print("  Chat UI:     [dim]none for MLX — use[/] kodo chat")
    console.print(f"  OpenAI API:  http://{host}:{port}/v1")
    console.print("  [dim]Ctrl-C to stop[/]\n")
    try:
        runtime.run(model, host, port)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@app.command()
def chat(
    name: Annotated[str, typer.Argument(help="Library model name (full path or bare repo name).")],
    prompt: Annotated[
        str | None,
        typer.Option("-p", "--prompt", help="One-shot prompt, prints just the answer (Claude-style -p)."),
    ] = None,
    model_format: FormatOption = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", "-n", help="Cap generated tokens.")] = None,
) -> None:
    """Chat with a library model: a clean streaming REPL, or one-shot with ``-p``.

    ``kodo chat <model>`` opens an interactive REPL; ``kodo chat <model> -p "..."``
    prints only the completion to stdout (pipeable). Both talk to the model's
    OpenAI ``/v1`` — same clean path for GGUF and MLX.
    """
    model = _resolve_library_model(name, model_format)
    try:
        if prompt is not None:
            # Scripted: print only the reply to stdout (errors go to stderr).
            print(runtime.generate(model, prompt, max_tokens))  # noqa: T201
        else:
            runtime.chat_repl(model, max_tokens)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


@app.command()
def serve(
    ui: Annotated[bool, typer.Option("--ui", help="Also serve the browser UI (single-page app).")] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Lock the server to one model (extension backend); no switching."),
    ] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes.")] = False,
) -> None:
    """Run the web server (browse API, plus the browser UI with --ui).

    With --model the server is locked to a single model (the Chrome-extension
    backend); otherwise the UI can switch models freely.
    """
    import os

    import uvicorn

    # Propagate to the (possibly reloaded) worker process via env vars.
    if ui:
        os.environ["KODO_SERVE_UI"] = "true"
    if model is not None:
        os.environ["KODO_SERVE_MODEL"] = model

    get_settings.cache_clear()
    settings = get_settings()
    base = f"http://{settings.host}:{settings.port}"
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
        host=settings.host,
        port=settings.port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
