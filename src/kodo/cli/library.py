"""`kodo library` - list, pull, remove, tag, verify, install, and search the model library."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich import box
from rich.table import Table

from kodo import (
    capabilities,
    cards,
    consumers,
    tags,
)
from kodo import catalog as catalog_ops
from kodo import library as library_ops
from kodo.config import get_settings
from kodo.models import ModelFormat, ModelSource, _human_size
from kodo.sources import huggingface as hf

if TYPE_CHECKING:
    pass

from kodo.cli._app import library_app
from kodo.cli._common import (
    _FORMAT_STYLE,
    FormatOption,
    SourceOption,
    _caps_label,
    _fmt_cell,
    _fmt_ctx,
    _library_names,
    _print_model_card,
    _pull_voice_all,
    _resolve_library_model,
    console,
)


@library_app.command("ls")
def list_models(
    details: Annotated[
        bool,
        typer.Option("--details", "-d", help="Full-detail card per model: caps, context, location, path, files."),
    ] = False,
) -> None:
    """List the models in your library — what you've pulled, ready to run.

    Scans the libraries in scope (a project's ``libraries`` in ``kodo.toml``, else
    the default ``KODO_LIBRARY_ROOT``). To browse models in your app caches that you
    *could* pull, use ``kodo library sources``. Pass ``--details`` (``-d``) for a
    stacked card per model with its full detail.
    """
    settings = get_settings()
    all_models = library_ops.scan()
    models = [m for m in all_models if m.generative]  # chat LLMs
    voices = [m for m in all_models if m.voice_kind]  # TTS/STT voice models
    lib_roots = library_ops.roots(settings)
    missing = [r for r in lib_roots if not r.is_dir()]
    if not models and not voices:
        console.print("Your library is empty.")
        if missing:
            console.print(f"[yellow]Library not mounted:[/] [dim]{', '.join(str(r) for r in missing)}[/]")
        console.print("[dim]Pull one with[/] kodo library pull [dim]· browse with[/] kodo library sources")
        return

    total = _human_size(sum(m.size_bytes for m in all_models))
    voice_note = f" · [magenta]{len(voices)} voice[/]" if voices else ""
    console.print(f"\n[bold]{len(models)} models{voice_note} · {total}[/] in your library\n")
    if missing:
        console.print(f"[yellow]Note:[/] not mounted: [dim]{', '.join(str(r) for r in missing)}[/]\n")
    if details:  # full-detail cards, one per model, stacked and grouped by format
        tag_maps: dict[Path, dict[str, list[str]]] = {}  # cache tags.json per library
        for fmt in sorted({m.model_format for m in models}, key=lambda f: f.value):
            for m in sorted((m for m in models if m.model_format is fmt), key=lambda m: m.name):
                tag_maps.setdefault(m.library_root, tags.load(m.library_root))
                _print_model_card(m, tag_maps[m.library_root].get(m.name, []))
        for m in sorted(voices, key=lambda m: (m.voice_kind, m.name)):
            tag_maps.setdefault(m.library_root, tags.load(m.library_root))
            _print_model_card(m, tag_maps[m.library_root].get(m.name, []))
        return
    for fmt in sorted({m.model_format for m in models}, key=lambda f: f.value):
        rows = sorted((m for m in models if m.model_format is fmt), key=lambda m: m.name)
        subtotal = _human_size(sum(m.size_bytes for m in rows))
        title = f"[{_FORMAT_STYLE[fmt]}][bold]{fmt.value}[/][/]  [dim]{len(rows)} · {subtotal}[/]"
        table = Table(box=box.SIMPLE_HEAD, title=title, title_justify="left", pad_edge=False)
        table.add_column("SIZE", justify="right")
        table.add_column("CAPS")
        table.add_column("CTX", justify="right")
        table.add_column("NAME", style="white")
        for m in rows:
            try:
                caps = capabilities.capabilities(m)
            except Exception:  # noqa: BLE001 - detection is best-effort; never break the listing
                caps = None
            table.add_row(m.size_human, _caps_label(caps), _fmt_ctx(caps.context_length if caps else None), m.name)
        console.print(table)

    if voices:  # the Voice category (TTS/STT) — a peer of the format groups
        subtotal = _human_size(sum(m.size_bytes for m in voices))
        vt = Table(
            box=box.SIMPLE_HEAD,
            title=f"[magenta][bold]voice[/][/]  [dim]{len(voices)} · {subtotal}[/]",
            title_justify="left",
            pad_edge=False,
        )
        vt.add_column("SIZE", justify="right")
        vt.add_column("KIND")
        vt.add_column("NAME", style="white")
        for m in sorted(voices, key=lambda m: (m.voice_kind, m.name)):
            kind = "[cyan]tts[/]" if m.voice_kind == "tts" else "[green]stt[/]"
            vt.add_row(m.size_human, kind, m.name)
        console.print(vt)
        console.print("[dim]Voice-specific ops:[/] kodo voice ls / import")


@library_app.command("rm")
def remove_model(
    name: Annotated[str, typer.Argument(help="Library model to remove (full name or bare repo/tag).")],
    model_format: FormatOption = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove a model from the library — deletes its files from disk.

    Resolves like ``kodo chat`` (use ``--format`` to disambiguate a model kept in
    more than one format). Ollama models keep any blobs still shared with other
    installed models.
    """
    copies = library_ops.find_copies(name, model_format=model_format)
    if not copies:
        typer.secho(f"No library model matches {name!r} (see `kodo library ls`).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    # Ambiguity is per distinct (name, format); multiple *copies* of the same model
    # (e.g. kept on both the local disk and the drive) are all removed together.
    distinct = {(m.name, m.model_format) for m in copies}
    if len(distinct) > 1:
        fmts = ", ".join(sorted({m.model_format.value for m in copies}))
        typer.secho(f"{name!r} is ambiguous across formats ({fmts}) — pass --format.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    model = copies[0]
    plural = f" ({len(copies)} copies)" if len(copies) > 1 else ""
    console.print(f"\n[bold]Remove[/] {model.name}  [dim]({model.model_format.value} · {model.size_human}){plural}[/]")
    for m in copies:
        console.print(f"  [dim]{m.path}[/]")
    if not yes and not typer.confirm("Delete these files?", default=False):
        console.print("[dim]Aborted.[/]")
        raise typer.Exit(0)
    total_files, total_freed, failed = 0, 0, 0
    for m in copies:
        count, freed = library_ops.remove(m)
        total_files += count
        total_freed += freed
        if count == 0 and m.file_count > 0:  # rmtree couldn't delete it (read-only drive, files in use)
            failed += 1
    if failed:
        console.print(
            f"[red]Could not remove {failed} of {len(copies)} copies[/] of {model.name} "
            "(read-only drive, or files held open by a running server?)."
        )
        if total_files:
            console.print(
                f"[green]Removed[/] the rest — freed [bold]{_human_size(total_freed)}[/] ({total_files} files)"
            )
        raise typer.Exit(1)
    console.print(f"[green]Removed[/] {model.name} — freed [bold]{_human_size(total_freed)}[/] ({total_files} files)")


@library_app.command("migrate")
def migrate(
    apply: Annotated[
        bool, typer.Option("--apply", help="Actually reorganize (default: a dry-run showing the plan).")
    ] = False,
) -> None:
    """Reorganize old ``huggingface/`` pulls into the format-centric layout (gguf/mlx/safetensors).

    Older Hugging Face pulls landed in ``huggingface/<repo>``; kodo now stores by format. This
    moves each into its bucket (a same-drive rename), and removes any that are already duplicated
    in a bucket. Dry-run by default — pass ``--apply`` to make the changes.
    """
    any_plan = False
    total_moved = total_deduped = total_freed = 0
    for root in library_ops.roots():
        actions = library_ops.plan_migration(root)
        if not actions:
            continue
        any_plan = True
        console.print(f"\n[bold]{root}[/]")
        for a in actions:
            if a.kind == "move":
                console.print(
                    f"  [cyan]move[/]  huggingface/{a.repo_id}  [dim]→[/]  {a.model_format.value}/{a.repo_id}"
                )
            else:
                console.print(
                    f"  [yellow]dedup[/] huggingface/{a.repo_id}  [dim]— already in {a.model_format.value}/,"
                    f" remove ({_human_size(a.size_bytes)})[/]"
                )
        if apply:
            moved, deduped, freed = library_ops.apply_migration(actions)
            total_moved += moved
            total_deduped += deduped
            total_freed += freed

    if not any_plan:
        console.print("[green]Nothing to migrate[/] — no old huggingface/ pulls with recognizable weights.")
        return
    if apply:
        console.print(
            f"\n[green]Done[/] — moved [bold]{total_moved}[/], deduped [bold]{total_deduped}[/] "
            f"(freed {_human_size(total_freed)})."
        )
    else:
        console.print("\n[dim]Dry run.[/] Re-run with [bold]--apply[/] to make these changes.")


@library_app.command("install")
def install(
    model: Annotated[str, typer.Argument(help="Library model name, e.g. Qwen3.5-4B-GGUF.")],
    to: Annotated[str, typer.Option("--to", help="Target runtime: ollama | lmstudio.")] = "ollama",
    model_format: FormatOption = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Ollama: name to register under (default: a sanitized repo tail).")
    ] = None,
    system: Annotated[
        str | None, typer.Option("--system", help="Ollama: default system prompt to bake into the model.")
    ] = None,
) -> None:
    """Install a canonical library model into a runtime (feed a *consumer*).

    The library keeps one canonical copy; this points a runtime at it. ``--to ollama``
    imports the GGUF into a running Ollama (``ollama create``); ``--to lmstudio`` symlinks
    the GGUF/MLX model into LM Studio's models dir (zero copy). Either way the drive stays
    the single source of truth.
    """
    if to not in ("ollama", "lmstudio"):
        console.print(f"[red]Unsupported target {to!r}[/] — use [bold]ollama[/] or [bold]lmstudio[/].")
        raise typer.Exit(1)
    # Ollama only imports GGUF; LM Studio takes GGUF or MLX (use --format to disambiguate).
    if to == "ollama" and model_format is not None and model_format is not ModelFormat.gguf:
        console.print(
            f"[red]Ollama imports GGUF only[/] — drop `--format {model_format.value}` or use `--to lmstudio`."
        )
        raise typer.Exit(1)
    resolved = _resolve_library_model(model, ModelFormat.gguf if to == "ollama" else model_format)
    try:
        if to == "ollama":
            result = consumers.install_ollama(resolved, name=name, system=system)
        else:
            result = consumers.install_lmstudio(resolved)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    if to == "ollama":
        console.print(
            f"[green]Installed[/] {resolved.name} [dim]→[/] ollama [bold]{result.name}[/].\n"
            f"[dim]Run it with[/]  ollama run {result.name}"
        )
    else:
        console.print(
            f"[green]Linked[/] {resolved.name} [dim]→[/] LM Studio [dim]({result.detail}).[/]\n"
            "[dim]Rescan/restart LM Studio if it doesn't appear.[/]"
        )


@library_app.command("uninstall")
def uninstall(
    model: Annotated[str, typer.Argument(help="Library model name (or the Ollama name, for --from ollama).")],
    from_: Annotated[str, typer.Option("--from", help="Runtime to remove it from: ollama | lmstudio.")] = "ollama",
    model_format: FormatOption = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Ollama: the registered name to remove (default: derived from model).")
    ] = None,
) -> None:
    """Remove a model from a runtime (undo `kodo library install`). The library copy is kept.

    ``--from lmstudio`` removes only kodo's symlink (never a real LM Studio download); ``--from
    ollama`` runs ``ollama rm`` on the registered name (derived from the model, or ``--name``).
    """
    if from_ not in ("ollama", "lmstudio"):
        console.print(f"[red]Unsupported runtime {from_!r}[/] — use [bold]ollama[/] or [bold]lmstudio[/].")
        raise typer.Exit(1)
    try:
        if from_ == "ollama":
            # Don't require the model to still be in the library — you may want to drop the Ollama
            # copy of a model you've already removed from the drive.
            result = consumers.uninstall_ollama(name or consumers.ollama_name(model))
        else:
            resolved = _resolve_library_model(model, model_format)
            result = consumers.uninstall_lmstudio(resolved)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Removed[/] [bold]{result.name}[/] from {from_} [dim]({result.detail}); library copy kept.[/]"
    )


@library_app.command("installed")
def installed() -> None:
    """Show which runtimes each library model is currently installed into (Ollama / LM Studio).

    A read-only cross-reference: for each model on the drive, whether Ollama holds an import of it
    and whether LM Studio has a kodo symlink to it. Undo any of these with `kodo library uninstall`.
    """
    library_ops.roots()  # fail fast + clean if no library is configured
    models = library_ops.scan()
    ollama_names = consumers.ollama_installed_names()
    linked = consumers.lmstudio_linked_names(library_ops.roots())

    lines: list[str] = []
    for m in sorted(models, key=lambda x: x.name.lower()):
        targets = []
        # Ollama imports GGUF only; a match is our deterministic install name being present.
        if m.model_format is ModelFormat.gguf and not m.is_ollama and consumers.ollama_name(m.name) in ollama_names:
            targets.append(f"ollama [dim]({consumers.ollama_name(m.name)})[/]")
        if m.name in linked:
            targets.append("lmstudio")
        if targets:
            lines.append(f"  [cyan]{m.name}[/] [dim]→[/] {', '.join(targets)}")

    if not lines:
        console.print(
            "No library models are installed into a runtime yet.\n"
            "[dim]Feed one to a runtime with[/] kodo library install <model> --to ollama|lmstudio"
        )
        return
    console.print("[bold]Installed into runtimes[/] [dim](library keeps the canonical copy)[/]")
    for line in lines:
        console.print(line)


@library_app.command("cards")
def cards_backfill(
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-fetch even models that already have a card.")] = False,
) -> None:
    """Backfill missing Hugging Face model cards into library models' ``.kodo/`` sidecars.

    A model's card is its README — the docs the UI/CLI info panel shows. Some LM Studio downloads
    (and older pulls) ship without one; this infers the HF repo from each model's
    ``<publisher>/<repo>`` name and fetches its README. Ollama models are skipped (their card is
    generated from the manifest). Best-effort: a model that isn't on HF is reported, not fatal.
    """
    library_ops.roots()  # fail fast + clean if no library is configured
    token = get_settings().hf_token
    fetched = skipped = unavailable = 0
    for m in library_ops.scan():
        if m.is_ollama:
            continue  # Ollama cards are generated from the manifest, not fetched
        if not refresh and cards.has_card(m.path):
            skipped += 1
            continue
        text = cards.fetch_hf_readme(m.name, token)
        if text is None:
            console.print(f"  [yellow]no card[/] {m.name} [dim](not on HF, or no README)[/]")
            unavailable += 1
            continue
        cards.write_card(m.path / cards.SIDECAR_DIR, text)
        console.print(f"  [green]card[/] {m.name}")
        fetched += 1
    console.print(f"\n[bold]{fetched}[/] fetched, [dim]{skipped} already had one, {unavailable} unavailable.[/]")


@library_app.command("verify")
def verify_library(
    query: Annotated[str | None, typer.Argument(help="Model to verify (full name or bare tail); omit for all.")] = None,
    deep: Annotated[
        bool, typer.Option("--deep", help="Re-hash Ollama blobs against their sha256 (slow; true content integrity).")
    ] = False,
) -> None:
    """Check library models on disk against their recorded metadata.

    Verifies each model's total size, file count, and card against its ``.kodo/metadata.json``
    (catches truncated/incomplete pulls or deleted files). Ollama models are content-addressed,
    so their blobs are checked for existence — and with ``--deep``, re-hashed against their sha256.
    """
    models = library_ops.find(query) if query else library_ops.scan()
    if not models:
        console.print(f"[yellow]No models to verify[/]{f' matching {query!r}' if query else ''}.")
        raise typer.Exit(1 if query else 0)

    table = Table(box=box.SIMPLE, header_style="bold")
    table.add_column("", justify="center")
    table.add_column("MODEL", style="cyan")
    table.add_column("CHECKED", style="dim")
    table.add_column("ISSUES")
    ok_count = 0
    for m in sorted(models, key=lambda x: x.name):
        r = library_ops.verify(m, deep=deep)
        ok_count += r.ok
        mark = "[green]✓[/]" if r.ok else "[red]✗[/]"
        table.add_row(mark, m.name, r.checked, "[green]ok[/]" if r.ok else f"[red]{'; '.join(r.issues)}[/]")
    console.print(table)
    bad = len(models) - ok_count
    console.print(f"\n[bold]{ok_count}/{len(models)} ok[/]" + (f" · [red]{bad} with issues[/]" if bad else ""))
    if bad:
        raise typer.Exit(1)


@library_app.command("tag")
def tag_model(
    name: Annotated[str, typer.Argument(help="Library model (full name or bare repo/tag).")],
    model_format: FormatOption = None,
    add: Annotated[list[str], typer.Option("--add", "-a", help="Tag(s) to add (repeatable).")] = [],
    remove: Annotated[list[str], typer.Option("--remove", "-r", help="Tag(s) to remove (repeatable).")] = [],
    clear: Annotated[bool, typer.Option("--clear", help="Remove all tags from the model.")] = False,
) -> None:
    """Add, remove, or list a model's user tags (tested, favorite, coding, ...).

    Tags are *your* labels, separate from the auto-detected vision/audio/tools
    capabilities. With no ``--add``/``--remove``/``--clear`` the current tags are
    just listed. They're stored inside the model's own library, so they travel with
    it, and show up as filter chips in the web Models view.
    """
    model = _resolve_library_model(name, model_format)
    root = model.library_root
    if clear:
        result = tags.set_tags(root, model.name, [])
    elif add or remove:
        result = tags.edit_tags(root, model.name, add, remove)
    else:
        result = tags.tags_for(root, model.name)
    label = "  ".join(f"[cyan]{t}[/]" for t in result) if result else "[dim](none)[/]"
    console.print(f"[white]{model.name}[/]\n  [dim]tags[/]  {label}")


@library_app.command("tag-style")
def tag_style(
    tag: Annotated[str, typer.Argument(help="Tag name to style (e.g. tested, coding).")],
    color: Annotated[str | None, typer.Option("--color", "-c", help="Hex color for the tag, e.g. '#22c55e'.")] = None,
    icon: Annotated[str | None, typer.Option("--icon", "-i", help="A short glyph/emoji for the tag.")] = None,
    description: Annotated[str | None, typer.Option("--description", "-d", help="Optional description.")] = None,
) -> None:
    """Give a tag a first-class color / icon (a per-library tag registry).

    Assignments stay plain name references (``kodo library tag``); this stores the tag's *style*
    once, keyed by name, so ``coding`` looks the same on every model. The web UI prefers a
    registered color over the name-derived one. With no options, prints the tag's current style.
    """
    if color is not None and not tags.valid_color(color):
        console.print(f"[red]{color!r} is not a hex color[/] — use '#rgb' or '#rrggbb' (e.g. '#22c55e').")
        raise typer.Exit(1)
    root = library_ops.roots()[0]  # the primary in-scope library (project-local or shared)
    key = tags.normalize(tag)
    meta = (
        tags.set_tag_meta(root, tag, color=color, icon=icon, description=description)
        if (color is not None or icon is not None or description is not None)
        else tags.load_registry(root).get(key, tags.TagMeta())
    )
    style = "  ".join(
        f"[dim]{f}[/] {v}" for f, v in (("color", meta.color), ("icon", meta.icon), ("desc", meta.description)) if v
    )
    console.print(f"[cyan]{key}[/]\n  {style or '[dim](no style set)[/]'}")


def _pull_all(source: ModelSource, root: Path | None, move: bool) -> None:
    """Import every model from ``source``'s local store into the library.

    Idempotent (skips models already in the library) and resilient (one failing
    model logs and the batch continues).
    """
    if source is ModelSource.voice:
        _pull_voice_all(root, move)
        return
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


@library_app.command()
def pull(
    source: Annotated[ModelSource, typer.Argument(help="Source the model belongs to.")],
    name: Annotated[
        str | None,
        typer.Argument(
            help="Model id (HF repo, Ollama model:tag, LM Studio path, or voice id e.g. kokoro). Omit with --all."
        ),
    ] = None,
    all_: Annotated[
        bool,
        typer.Option("--all", "-a", help="Import every model from this source's local store (idempotent)."),
    ] = False,
    move: Annotated[
        bool,
        typer.Option("--move", help="Delete the local source after a verified copy (frees local disk)."),
    ] = False,
    shared: Annotated[
        bool,
        typer.Option("--shared", help="Pull into the shared/default library instead of the project-local one."),
    ] = False,
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            help="HF only: filename glob(s) to fetch, e.g. --include '*Q4_K_M*' to grab one GGUF quant. Repeatable.",
        ),
    ] = None,
    vocoder: Annotated[
        str | None,
        typer.Option(
            "--vocoder",
            help="HF only: a vocoder repo to co-locate as a TTS model, e.g. --vocoder ggml-org/WavTokenizer.",
        ),
    ] = None,
) -> None:
    """Pull (or move) a model into a library.

    Targets the project-local library when you're in a project, else the default
    library; ``--shared`` forces the shared/default library. Give a model name for
    one model, or --all to import everything from this source's local store
    (skipping models already in the library).

    The ``voice`` source pulls a TTS/STT model by its registry id (e.g.
    ``kodo library pull voice kokoro``) into ``<root>/voice/`` — downloading it if it
    isn't already in the Hugging Face cache. This is the project-aware way to add a
    voice model (``kodo voice import`` is the older cache-only alias).
    """
    # Default target: the first library in scope (project-local if any, else the
    # default). --shared forces the machine's default (shared) library.
    root = library_ops.default_root() if shared else library_ops.roots()[0]
    if all_ == (name is not None):
        console.print("[red]Provide either a model name or --all, not both.[/]")
        raise typer.Exit(2)

    if all_:
        _pull_all(source, root, move)
        return

    assert name is not None  # narrowed by the guard above
    verb = "Moving" if move else "Pulling"
    typer.echo(f"{verb} {source.value}:{name} -> {root} ...")
    try:
        result = catalog_ops.pull(source, name, library_root=root, move=move, include=include, vocoder=vocoder)
    except Exception as exc:  # noqa: BLE001 - a pull can fail many ways (disk, network, HF Hub); surface it cleanly
        typer.secho(f"Pull failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if move and not result.source_removed:
        suffix = " [yellow](local copy KEPT — copy could not be verified)[/]"
    elif result.source_removed:
        suffix = " (local copy removed)"
    else:
        suffix = ""
    console.print(f"Done: {result.file_count} files, {result.size_human} -> {result.destination}{suffix}")


@library_app.command()
def sources(
    source: SourceOption = None,
    show_all: Annotated[bool, typer.Option("--all", "-a", help="Include embedding/vision/partial entries.")] = False,
) -> None:
    """Browse models in your app caches (HF / Ollama / LM Studio) you can pull.

    These live in caches on this machine (e.g. ~/.cache/huggingface) — *not* your
    library. The IN LIBRARY column marks what you've already pulled; pull leftover
    local models onto the drive with ``kodo library pull --move`` to free local disk.
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
    console.print("[dim]Caches on this machine, not your library — see[/] kodo library ls [dim]for your library.[/]\n")
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


@library_app.command()
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
    table.add_column("~PULL", justify="right")  # est. download (preferred quant, not the whole repo)
    table.add_column("MODEL", style="white")
    for r in results:
        size = _human_size(r.size_bytes) if r.size_bytes else "[dim]?[/]"
        table.add_row(f"{r.downloads:,}", f"{r.likes:,}", size, r.id)
    console.print(table)
    console.print(
        "\n[dim]~PULL = approx download for the preferred quant (not the full repo).\n"
        "Pull one with[/] kodo library pull huggingface <model>"
    )
