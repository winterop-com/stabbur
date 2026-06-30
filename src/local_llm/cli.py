"""Command-line interface for browsing, pulling, and running local models."""

from typing import Annotated

import typer

from local_llm import catalog as catalog_ops
from local_llm import library as library_ops
from local_llm import runtime
from local_llm.config import get_settings
from local_llm.models import ModelFormat, ModelSource, _human_size

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


@app.command("list")
def list_models(source: SourceOption = None) -> None:
    """List discovered models across sources."""
    result = catalog_ops.list_models(source)
    if not result.entries:
        typer.echo("No models found.")
        return

    src_w = max(len("SOURCE"), *(len(e.source.value) for e in result.entries))
    fmt_w = max(len("FORMAT"), *(len(e.model_format.value) for e in result.entries))
    header = f"{'SOURCE':<{src_w}}  {'FORMAT':<{fmt_w}}  {'SIZE':>10}  NAME"
    typer.secho(header, bold=True)
    typer.secho("-" * len(header), dim=True)
    for entry in sorted(result.entries, key=lambda e: (e.source.value, e.name)):
        typer.echo(
            f"{entry.source.value:<{src_w}}  {entry.model_format.value:<{fmt_w}}  {entry.size_human:>10}  {entry.name}"
        )
    typer.echo(f"\n{len(result.entries)} models, {result.total_human} total")


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
    """List models stored in the on-drive library."""
    models = library_ops.scan()
    if not models:
        typer.echo(f"Library is empty: {get_settings().backup_root}")
        return

    fmt_w = max(len("FORMAT"), *(len(m.model_format.value) for m in models))
    header = f"{'FORMAT':<{fmt_w}}  {'SIZE':>10}  NAME"
    typer.secho(header, bold=True)
    typer.secho("-" * len(header), dim=True)
    for model in sorted(models, key=lambda m: (m.model_format.value, m.name)):
        typer.echo(f"{model.model_format.value:<{fmt_w}}  {model.size_human:>10}  {model.name}")
    total = _human_size(sum(m.size_bytes for m in models))
    typer.echo(f"\n{len(models)} models, {total} in {get_settings().backup_root}")


@app.command()
def run(
    name: Annotated[str, typer.Argument(help="Library model name (full path or bare repo name).")],
    model_format: FormatOption = None,
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port.")] = 8080,
) -> None:
    """Serve a library model via its runtime (OpenAI-compatible API).

    GGUF runs on llama.cpp's llama-server; MLX runs on mlx_lm.server.
    """
    matches = library_ops.find(name, model_format=model_format)
    if not matches:
        typer.secho(f"{name!r} is not in the library ({get_settings().backup_root}).", fg=typer.colors.RED, err=True)
        # Hint if it exists in a local source store but hasn't been pulled yet.
        q = name.lower()
        in_sources = [
            e for e in catalog_ops.list_models().entries if q in (e.name.lower(), e.name.rsplit("/", 1)[-1].lower())
        ]
        if in_sources:
            src = in_sources[0].source.value
            typer.secho(
                f"It is in {src}; pull it first:  llm pull {src} {in_sources[0].name}", fg=typer.colors.YELLOW, err=True
            )
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.secho(f"{name!r} is ambiguous; narrow with --format:", fg=typer.colors.YELLOW, err=True)
        for m in matches:
            typer.echo(f"  {m.model_format.value:<6} {m.name}", err=True)
        raise typer.Exit(1)

    model = matches[0]
    typer.echo(f"Serving {model.model_format.value} {model.name}")
    typer.echo(f"OpenAI endpoint: http://{host}:{port}/v1  (Ctrl-C to stop)")
    try:
        runtime.run(model, host, port)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


@app.command()
def serve(
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes.")] = False,
) -> None:
    """Run the FastAPI browse server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "local_llm.app:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
