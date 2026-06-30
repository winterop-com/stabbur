"""Command-line interface for browsing, pulling, and running local models."""

from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from local_llm import catalog as catalog_ops
from local_llm import library as library_ops
from local_llm import runtime
from local_llm.config import get_settings
from local_llm.models import ModelFormat, ModelSource, _human_size

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
def list_models(source: SourceOption = None) -> None:
    """List models available in local source stores (and whether each is pulled).

    These are candidates to `llm pull` into the library; the IN LIBRARY column
    shows which you already have. Use `llm library` to see the library itself.
    """
    result = catalog_ops.list_models(source)
    if not result.entries:
        console.print("No models found in local source stores.")
        return

    lib = _library_names()

    def in_library(name: str) -> bool:
        return name.lower() in lib or name.rsplit("/", 1)[-1].lower() in lib

    pulled = sum(1 for e in result.entries if in_library(e.name))
    console.print(
        f"\n[bold]{len(result.entries)} models[/] in local sources "
        f"[dim]· {pulled} already in library · {len(result.entries) - pulled} to pull[/]\n"
    )
    for src in sorted({e.source for e in result.entries}, key=lambda s: s.value):
        rows = sorted((e for e in result.entries if e.source is src), key=lambda e: e.name)
        table = Table(box=box.SIMPLE_HEAD, title=f"[bold]{src.value}[/]", title_justify="left", pad_edge=False)
        table.add_column("IN LIBRARY", justify="center")
        table.add_column("FORMAT")
        table.add_column("SIZE", justify="right")
        table.add_column("NAME", style="white")
        for e in rows:
            mark = "[green]✓[/]" if in_library(e.name) else "[dim]—[/]"
            table.add_row(mark, _fmt_cell(e.model_format), e.size_human, e.name)
        console.print(table)


@app.command()
def pull(
    source: Annotated[ModelSource, typer.Argument(help="Source the model belongs to.")],
    name: Annotated[str, typer.Argument(help="Model id (HF repo, Ollama model:tag, LM Studio path).")],
    move: Annotated[
        bool,
        typer.Option("--move", help="Delete the local source after a verified copy (frees local disk)."),
    ] = False,
) -> None:
    """Pull (or move) a single model into the library."""
    verb = "Moving" if move else "Pulling"
    typer.echo(f"{verb} {source.value}:{name} ...")
    try:
        result = catalog_ops.pull(source, name, move=move)
    except NotImplementedError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    suffix = " (local copy removed)" if move else ""
    typer.echo(f"Done: {result.file_count} files, {result.size_human} -> {result.destination}{suffix}")


@app.command("library")
def library_list() -> None:
    """List models stored in the on-drive library, grouped by format."""
    root = get_settings().backup_root
    models = library_ops.scan()
    if not models:
        console.print(f"Library is empty: [dim]{root}[/]")
        return

    total = _human_size(sum(m.size_bytes for m in models))
    console.print(f"\n[bold]{len(models)} models[/], {total} in [dim]{root}[/]\n")
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


def _resolve_library_model(name: str | None, model_format: ModelFormat | None) -> library_ops.LibraryModel:
    """Resolve a library model by name, or (when name is None) via a Textual picker."""
    if name is None:
        from local_llm.tui import pick_model

        models = library_ops.scan()
        if not models:
            console.print(f"Library is empty: [dim]{get_settings().backup_root}[/]")
            raise typer.Exit(1)
        chosen = pick_model(models)
        if chosen is None:
            raise typer.Exit(0)  # cancelled
        return chosen

    matches = library_ops.find(name, model_format=model_format)
    if not matches:
        console.print(f"[red]{name!r} is not in the library[/] ([dim]{get_settings().backup_root}[/]).")
        q = name.lower()
        in_sources = [
            e for e in catalog_ops.list_models().entries if q in (e.name.lower(), e.name.rsplit("/", 1)[-1].lower())
        ]
        if in_sources:
            src = in_sources[0].source.value
            console.print(f"[yellow]It is in {src}; pull it first:[/]  llm pull {src} {in_sources[0].name}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[yellow]{name!r} is ambiguous; narrow with --format:[/]")
        for m in matches:
            console.print(f"  {m.model_format.value:<6} {m.name}")
        raise typer.Exit(1)
    return matches[0]


@app.command()
def run(
    name: Annotated[str | None, typer.Argument(help="Library model (omit to pick interactively).")] = None,
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
        console.print("  Chat UI:     [dim]none for MLX — use[/] llm chat")
    console.print(f"  OpenAI API:  http://{host}:{port}/v1")
    console.print("  [dim]Ctrl-C to stop[/]\n")
    try:
        runtime.run(model, host, port)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@app.command()
def chat(
    name: Annotated[str | None, typer.Argument(help="Library model (omit to pick interactively).")] = None,
    model_format: FormatOption = None,
) -> None:
    """Open an interactive terminal chat with a library model.

    GGUF uses llama.cpp's llama-cli; MLX uses mlx_lm.chat.
    """
    model = _resolve_library_model(name, model_format)
    console.print(
        f"Chatting with [bold]{_fmt_cell(model.model_format)}[/] {model.name}  [dim](Ctrl-C / Ctrl-D to exit)[/]"
    )
    try:
        runtime.chat(model)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@app.command()
def serve(
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes.")] = False,
) -> None:
    """Run the FastAPI browse server."""
    import uvicorn

    settings = get_settings()
    base = f"http://{settings.host}:{settings.port}"
    console.print("\n[bold]local-llm browse API[/]")
    console.print(f"  API:      [link={base}]{base}[/]")
    console.print(f"  Docs:     [link={base}/docs]{base}/docs[/]")
    console.print("  [dim]Ctrl-C to stop[/]\n")
    uvicorn.run(
        "local_llm.app:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
