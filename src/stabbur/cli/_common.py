"""Shared CLI helpers: the console, formatting, option types, and model/media resolution."""

import shlex
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from stabbur import (
    attach,
    capabilities,
    mcp_catalog,
    mcpservers,
)
from stabbur import catalog as catalog_ops
from stabbur import library as library_ops
from stabbur.models import CuratedModel, ModelFormat, ModelSource
from stabbur.project import scaffold

console = Console()

_FORMAT_STYLE = {
    ModelFormat.gguf: "cyan",
    ModelFormat.mlx: "magenta",
    ModelFormat.safetensors: "yellow",
    ModelFormat.unknown: "dim",
}


# The MCP catalog (curated + optional first-party servers) lives in `stabbur.mcp_catalog` so the
# web layer can share it. Aliases keep the existing call sites terse.
_CURATED_MCP = mcp_catalog.CURATED
_OPTIONAL_FIRST_PARTY = mcp_catalog.OPTIONAL_FIRST_PARTY
_uninstalled_optional = mcp_catalog.uninstalled_optional

SourceOption = Annotated[
    ModelSource | None,
    typer.Option("--source", "-s", help="Limit to a single source."),
]
FormatOption = Annotated[
    ModelFormat | None,
    typer.Option("--format", "-f", help="Disambiguate when a model exists in multiple formats."),
]


# Curated starter models for `stabbur init` (verified GGUF repos; small but capable — sub-1B toy
# models are too weak to be useful defaults, so the floor is ~3B).
# Best first: a list ordered by size makes the weakest model the default pick, and the one most
# people should take is the capable all-rounder, not the 3B kept around for testing.
_CURATED: list[CuratedModel] = [
    CuratedModel(
        id="lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        note="recommended — capable all-rounder, vision + audio (~6.7 GB)",
    ),
    CuratedModel(id="unsloth/Qwen3.5-4B-GGUF", note="compact + good at tools (~2.5 GB)"),
    CuratedModel(id="unsloth/Llama-3.2-3B-Instruct-GGUF", note="tiny + fast, kinda works — good for testing (~2 GB)"),
    CuratedModel(id="unsloth/gpt-oss-20b-GGUF", note="strong reasoning + tools (~10.8 GB)"),
    CuratedModel(id="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", note="coding specialist (~17 GB)"),
]


def _fmt_cell(model_format: ModelFormat) -> str:
    """Render a format value with its color style."""
    return f"[{_FORMAT_STYLE[model_format]}]{model_format.value}[/]"


def _count(n: int, noun: str, plural: str | None = None) -> str:
    """A count and its noun, agreeing in number ("1 model", "2 models")."""
    return f"{n} {noun}" if n == 1 else f"{n} {plural or noun + 's'}"


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


#: The project-local library directory that `stabbur project init` scaffolds (owned by stabbur.scaffold).
_LOCAL_LIBRARY = scaffold.LOCAL_LIBRARY


def _to_mcp_server(name: str, command: str) -> mcpservers.McpServer:
    """Turn a CLI ``(name, command)`` pair into a standard :class:`stabbur.mcpservers.McpServer`.

    Splits a leading ``env VAR=val …`` prefix into the entry's ``env`` table, then the remaining
    command into ``command`` + ``args`` (the ``mcpServers`` JSON shape).
    """
    cmd, env = scaffold.split_env_prefix(command)
    argv = shlex.split(cmd)
    return mcpservers.McpServer(name=name, command=argv[0], args=argv[1:], env=env)


def _normalize_server_url(url: str | None) -> str | None:
    """Normalize a ``--server`` value to the base URL the runtime expects (or None).

    Strips a trailing slash and a trailing ``/v1`` (the client appends ``/v1/chat/completions``),
    so ``http://host:8000``, ``http://host:8000/`` and ``http://host:8000/v1`` all resolve the same.
    """
    if not url or not url.strip():
        return None
    base = url.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _cli_mcp_spec(value: str) -> tuple[str | None, list[str], dict[str, str]]:
    """Resolve a ``--mcp`` value to the ``(name, argv, env)`` spec :func:`stabbur.tools.connect` wants.

    An advertised server *name* (or *command*) resolves to that server; anything else is used
    verbatim as a command. A leading ``env VAR=val`` prefix is lifted into ``env``.
    """
    from stabbur import plugins  # noqa: PLC0415

    name, command = plugins.resolve_mcp(value)
    cmd, env = scaffold.split_env_prefix(command)
    return name, shlex.split(cmd), env


# How far two measurements of the *same* copy may drift and still count as one artifact. A source
# store and the library count slightly different things (bookkeeping files, AppleDouble noise), so
# the gap is never exactly zero; two quants of one repo are far further apart than this — even the
# closest neighbours (Q4_K_S vs Q4_K_M) differ by several percent.
_SAME_COPY_TOLERANCE = 0.02

IN_LIBRARY_SAME = "same"
IN_LIBRARY_OTHER_QUANT = "other-quant"
IN_LIBRARY_OTHER_FORMAT = "other-format"


def _library_index(models: "list[library_ops.LibraryModel] | None" = None) -> dict[str, list[tuple[str, int]]]:
    """``{name: [(format, size_bytes), ...]}`` for every library copy, under full and bare names.

    Pass an already-scanned list to reuse it: a scan measures every file on the drive, so a caller
    that has one must not pay for a second.
    """
    index: dict[str, list[tuple[str, int]]] = {}
    for m in library_ops.scan() if models is None else models:
        for key in {m.name.lower(), m.name.rsplit("/", 1)[-1].lower()}:
            index.setdefault(key, []).append((m.model_format.value, m.size_bytes))
    return index


def _in_library(name: str, model_format: ModelFormat, size_bytes: int, index: dict[str, list[tuple[str, int]]]) -> str:
    """Classify a source-store model against the library index.

    Returns :data:`IN_LIBRARY_SAME`, :data:`IN_LIBRARY_OTHER_QUANT`, :data:`IN_LIBRARY_OTHER_FORMAT`,
    or ``""`` for nothing. Matching on the name alone used to tick a 17 GB source copy against a
    26 GB library copy of the same repo — a *different quant*, which the library does not have and
    which the tick said it did.
    """
    copies = index.get(name.lower()) or index.get(name.rsplit("/", 1)[-1].lower()) or []
    if not copies:
        return ""
    fmt = model_format.value
    same_format = [size for copy_format, size in copies if model_format is ModelFormat.unknown or copy_format == fmt]
    if not same_format:
        return IN_LIBRARY_OTHER_FORMAT
    if not size_bytes or any(
        abs(size - size_bytes) <= _SAME_COPY_TOLERANCE * max(size, size_bytes) for size in same_format
    ):
        return IN_LIBRARY_SAME  # a match, or a source that reports no size — fall back to name+format
    return IN_LIBRARY_OTHER_QUANT


def _pull_voice_all(root: Path | None, move: bool) -> None:
    """Import every registry voice model already in the HF cache into the target library.

    Mirrors the old ``stabbur voice import --all`` (no mass downloads): only cached models are
    pulled. Fetch a not-yet-downloaded one by name, e.g. ``stabbur library pull voice kokoro``.
    """
    from stabbur.voice import importer as voice_importer  # noqa: PLC0415

    target = root or library_ops.default_root()
    ids = voice_importer.cached_voice_ids(target)
    if not ids:
        console.print("No cached voice models to import. Pull one by name, e.g. stabbur library pull voice kokoro.")
        return
    failed = 0
    for vid in ids:
        try:
            result = catalog_ops.pull(ModelSource.voice, vid, library_root=root, move=move)
        except Exception as exc:  # noqa: BLE001 - one bad model must not abort the batch
            failed += 1
            console.print(f"[red]✗ fail[/] {vid} [dim]— {escape(str(exc))}[/]")
            continue
        console.print(f"[green]✓ pull[/] {vid} [dim]({result.size_human})[/]")
    console.print(f"\n[bold]{len(ids) - failed} imported[/] · {failed} failed")
    if failed:
        raise typer.Exit(1)


_ModelOpt = Annotated[str | None, typer.Option("--model", help="Model to bind (skips the model picker).")]
_ForceOpt = Annotated[
    bool, typer.Option("--force", help="Scaffold into a directory that already exists (refused otherwise).")
]
_GitOpt = Annotated[
    bool,
    typer.Option("--git", help="git init the project + write a .gitignore (excludes the local library/ + .env)."),
]
_VoicesOpt = Annotated[
    bool,
    typer.Option(
        "--voices/--no-voices",
        help="Include the voice package (Kokoro + VoxCPM2) in the project; --no-voices for a text-only nest.",
    ),
]
_UvOpt = Annotated[
    bool,
    typer.Option("--uv/--no-uv", help="Make it a self-contained uv project (pyproject.toml pinning stabbur + tools)."),
]
_TemplateOpt = Annotated[
    str | None,
    typer.Option("--template", "-t", help="Start from a preset (e.g. 'dhis2'): model + prompt + tools + files."),
]


def _not_chat_msg(name: str, model_format: ModelFormat) -> str:
    return f"[red]{name!r} is not a chat model[/] ({model_format.value}) — stabbur runs generative LLMs only."


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
                f"stabbur library pull {hit.source.value} {hit.name}"
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
        console.print(f"[red]{model.name!r} is an Ollama model[/] — stabbur runs GGUF/MLX, not Ollama's format.")
        console.print(f"[yellow]Run it with Ollama:[/]  OLLAMA_MODELS={store} ollama run {model.name}")
        raise typer.Exit(1)
    return model


def _maybe_library_model(name: str, model_format: ModelFormat | None) -> library_ops.LibraryModel | None:
    """Resolve a runnable generative library model by name, or ``None`` where :func:`_resolve_library_model` exits.

    For the remote-attach path (``chat --server``): the model may exist only on the server,
    so an unknown / ambiguous / not-locally-runnable name degrades to server-side metadata
    instead of aborting.
    """
    matches = library_ops.find(name, model_format=model_format)
    if len(matches) != 1:
        return None
    model = matches[0]
    if not model.generative or model.model_format is ModelFormat.safetensors or model.is_ollama:
        return None
    return model


_media_data_url = attach.media_data_url


def _load_media(
    paths: list[Path], model: library_ops.LibraryModel | None, *, kind: str, default_mime: str, capable: bool
) -> list[str]:
    """Read image/audio files into data URLs; warn if the model lacks that modality.

    ``model`` may be ``None`` (remote attach with no local copy) — capabilities are unknown
    then, so no warning is possible; pass ``capable=True``.
    """
    if not paths:
        return []
    if not capable and model is not None:
        console.print(f"[yellow]Note:[/] {model.name!r} isn't detected as a {kind} model; {kind} may be ignored.")
    urls: list[str] = []
    for p in paths:
        if not p.is_file():
            console.print(f"[red]{kind.capitalize()} not found:[/] {p}")
            raise typer.Exit(1)
        urls.append(_media_data_url(p, default_mime))
    return urls
