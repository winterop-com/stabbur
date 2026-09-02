"""`stabbur library` - list, pull, remove, tag, verify, install, search, and sync the model library."""

import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.markup import escape
from rich.table import Table

from stabbur import (
    capabilities,
    cards,
    consumers,
    curated,
    fsatomic,
    tags,
    wantlist,
)
from stabbur import catalog as catalog_ops
from stabbur import library as library_ops
from stabbur.cli._app import library_app
from stabbur.cli._common import (
    _FORMAT_STYLE,
    IN_LIBRARY_OTHER_FORMAT,
    IN_LIBRARY_OTHER_QUANT,
    IN_LIBRARY_SAME,
    FormatOption,
    SourceOption,
    _caps_label,
    _count,
    _fmt_cell,
    _fmt_ctx,
    _in_library,
    _library_index,
    _print_model_card,
    _pull_voice_all,
    _resolve_library_model,
    console,
)
from stabbur.config import get_settings
from stabbur.models import ModelFormat, ModelSource, _human_size
from stabbur.sources import huggingface as hf


@library_app.command("ls")
def list_models(
    details: Annotated[
        bool,
        typer.Option("--details", "-d", help="Full-detail card per model: caps, context, location, path, files."),
    ] = False,
) -> None:
    """List the models in your library — what you've pulled, ready to run.

    Scans the libraries in scope (a project's ``libraries`` in ``stabbur.toml``, else
    the default ``STABBUR_LIBRARY_ROOT``). To browse models in your app caches that you
    *could* pull, use ``stabbur library sources``. Pass ``--details`` (``-d``) for a
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
        console.print("[dim]Pull one with[/] stabbur library pull [dim]· browse with[/] stabbur library sources")
        return

    total = _human_size(sum(m.size_bytes for m in all_models))
    voice_note = f" · [magenta]{len(voices)} voice[/]" if voices else ""
    console.print(f"\n[bold]{_count(len(models), 'model')}{voice_note} · {total}[/] in your library\n")
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
        console.print("[dim]Voice-specific ops:[/] stabbur voice ls / import")


@library_app.command("rm")
def remove_model(
    name: Annotated[str, typer.Argument(help="Library model to remove (full name or bare repo/tag).")],
    model_format: FormatOption = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove a model from the library — deletes its files from disk.

    Resolves like ``stabbur chat`` (use ``--format`` to disambiguate a model kept in
    more than one format). Ollama models keep any blobs still shared with other
    installed models.
    """
    copies = library_ops.find_copies(name, model_format=model_format)
    if not copies:
        typer.secho(f"No library model matches {name!r} (see `stabbur library ls`).", fg=typer.colors.RED, err=True)
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

    Older Hugging Face pulls landed in ``huggingface/<repo>``; stabbur now stores by format. This
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
    the single source of truth. **mlx_lm** needs no install step — ``mlx_lm.server`` /
    ``mlx_vlm.server`` run a loose MLX copy in place (stabbur serves MLX this way), so there's
    no ``--to mlx_lm``.
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
    if to == "ollama":
        # --to ollama pins the format to GGUF below, so a model held only as MLX/safetensors
        # resolves to nothing and the generic resolver says it "is not in the library" — which is
        # false, and sends the reader looking for a model that is sitting right there. Say the
        # true thing, in the same shape as the --format message above.
        other = {m.model_format for m in library_ops.find(model)}
        if other and ModelFormat.gguf not in other:
            fmts = ", ".join(sorted(f.value for f in other))
            console.print(
                f"[red]{model!r} has no GGUF build[/] — Ollama imports GGUF only, and the library has it "
                f"as {fmts}. Pull a GGUF build, or use `--to lmstudio`."
            )
            raise typer.Exit(1)
    resolved = _resolve_library_model(model, ModelFormat.gguf if to == "ollama" else model_format)
    try:
        if to == "ollama":
            result = consumers.install_ollama(resolved, name=name, system=system)
        else:
            result = consumers.install_lmstudio(resolved)
    except RuntimeError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
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


def _ollama_uninstall_name(query: str, known: library_ops.LibraryModel | None) -> str:
    """The Ollama name to remove for ``query``: a recorded install name Ollama still holds, else derived.

    ``install --to ollama --name custom`` registers a name nothing derives, so an uninstall that
    only ever guessed ``ollama_name(model)`` could not remove it. Prefer a recorded name that Ollama
    actually has; fall back to the derived one so the old behaviour (and models no longer in the
    library) still works.
    """
    if known is None:
        return consumers.ollama_name(query)
    present = consumers.ollama_installed_names()
    recorded = consumers.recorded_install_names(known, "ollama")
    return next((n for n in recorded if n in present), consumers.ollama_name(known.name))


@library_app.command("uninstall")
def uninstall(
    model: Annotated[str, typer.Argument(help="Library model name (or the Ollama name, for --from ollama).")],
    from_: Annotated[str, typer.Option("--from", help="Runtime to remove it from: ollama | lmstudio.")] = "ollama",
    model_format: FormatOption = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Ollama: the registered name to remove (default: derived from model).")
    ] = None,
) -> None:
    """Remove a model from a runtime (undo `stabbur library install`). The library copy is kept.

    ``--from lmstudio`` removes only stabbur's symlink (never a real LM Studio download); ``--from
    ollama`` runs ``ollama rm`` on the registered name — ``--name``, else a name the model's sidecar
    recorded at install time, else the one derived from the model's name.
    """
    if from_ not in ("ollama", "lmstudio"):
        console.print(f"[red]Unsupported runtime {from_!r}[/] — use [bold]ollama[/] or [bold]lmstudio[/].")
        raise typer.Exit(1)
    try:
        if from_ == "ollama":
            # Don't require the model to still be in the library — you may want to drop the Ollama
            # copy of a model you've already removed from the drive — but use it when it's there,
            # for its record of the name the install actually used.
            matches = library_ops.find(model, model_format=model_format)
            known = matches[0] if len(matches) == 1 else None
            target = name or _ollama_uninstall_name(model, known)
            result = consumers.uninstall_ollama(target)
            if known is not None:
                consumers.forget_install(known, "ollama", target)
        else:
            resolved = _resolve_library_model(model, model_format)
            result = consumers.uninstall_lmstudio(resolved)
    except RuntimeError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Removed[/] [bold]{result.name}[/] from {from_} [dim]({result.detail}); library copy kept.[/]"
    )


@library_app.command("installed")
def installed() -> None:
    """Show which runtimes each library model is currently installed into (Ollama / LM Studio).

    A read-only cross-reference: for each model on the drive, whether Ollama holds an import of it
    and whether LM Studio has a stabbur symlink to it. Undo any of these with `stabbur library uninstall`.
    """
    library_ops.roots()  # fail fast + clean if no library is configured
    models = library_ops.scan()
    ollama_names = consumers.ollama_installed_names()
    linked = consumers.lmstudio_linked_names(library_ops.roots())

    lines: list[str] = []
    for m in sorted(models, key=lambda x: x.name.lower()):
        targets = []
        # Ollama imports GGUF only. A match is our deterministic install name, or any name the
        # model's sidecar recorded — without the latter an `install --name custom` is invisible here.
        if m.model_format is ModelFormat.gguf and not m.is_ollama:
            candidates = {consumers.ollama_name(m.name), *consumers.recorded_install_names(m, "ollama")}
            hits = sorted(c for c in candidates if c in ollama_names)
            if hits:
                targets.append(f"ollama [dim]({', '.join(hits)})[/]")
        if m.name in linked:
            targets.append("lmstudio")
        if targets:
            lines.append(f"  [cyan]{m.name}[/] [dim]→[/] {', '.join(targets)}")

    if not lines:
        console.print(
            "No library models are installed into a runtime yet.\n"
            "[dim]Feed one to a runtime with[/] stabbur library install <model> --to ollama|lmstudio"
        )
        return
    console.print("[bold]Installed into runtimes[/] [dim](library keeps the canonical copy)[/]")
    for line in lines:
        console.print(line)


# Display-format column order for `stabbur library formats`. Ollama's content-addressed store is
# its own column (a distinct consumer copy), not folded into gguf. gguf/mlx are the ready-to-run
# quants stabbur serves; safetensors is the convert/fine-tune source (2-4x a quant, not runnable).
_FORMAT_COLUMNS = ("gguf", "mlx", "safetensors", "ollama", "unknown")


def _format_key(model: library_ops.LibraryModel) -> str:
    """The display-format column a model belongs to (ollama store vs its on-disk format)."""
    return "ollama" if model.is_ollama else model.model_format.value


@library_app.command("formats")
def formats() -> None:
    """Show each model's on-disk formats and flag redundant safetensors / missing quants.

    One row per model name, a column per format present (gguf / mlx / safetensors / ollama) with
    that copy's size, and a NOTE flagging the policy cases (see ``stabbur library ls`` for the full
    per-model listing). Two things get called out: a **redundant** safetensors copy — a GGUF or MLX
    build of the same model already exists, so safetensors is just the convert/fine-tune source and
    can be dropped — and a model that's **only** safetensors, which llama.cpp/mlx_lm can't serve
    (pull a GGUF or MLX build to run it). The footer totals the space reclaimable by removing every
    redundant safetensors copy.
    """
    library_ops.roots()  # fail fast + clean if no library is configured
    models = [m for m in library_ops.scan() if m.generative]  # chat LLMs; voice is a separate family
    by_name: dict[str, dict[str, library_ops.LibraryModel]] = defaultdict(dict)
    for m in models:
        by_name[m.name][_format_key(m)] = m
    if not by_name:
        console.print("No chat models in your library.")
        console.print("[dim]Pull one with[/] stabbur library pull [dim]· see[/] stabbur library ls")
        return

    present = [f for f in _FORMAT_COLUMNS if any(f in fmts for fmts in by_name.values())]
    console.print(f"\n[bold]{_count(len(by_name), 'model')}[/] by format\n")
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("NAME", style="white")
    for f in present:
        style = _FORMAT_STYLE.get(ModelFormat(f)) if f in ModelFormat.__members__ else None
        table.add_column(f.upper(), justify="right", style=style)
    table.add_column("NOTE")

    reclaimable = 0
    for name in sorted(by_name):
        fmts = by_name[name]
        cells = [fmts[f].size_human if f in fmts else "" for f in present]
        note = ""
        has_quant = "gguf" in fmts or "mlx" in fmts
        if "safetensors" in fmts and has_quant:
            sft = fmts["safetensors"]
            reclaimable += sft.size_bytes
            note = (
                f"[yellow]redundant safetensors ({sft.size_human})[/] "
                f"[dim]· stabbur library rm {name} --format safetensors[/]"
            )
        elif "safetensors" in fmts and "ollama" not in fmts:
            note = "[yellow]no ready-to-run quant[/] [dim](safetensors only — pull a GGUF/MLX build to run it)[/]"
        table.add_row(name, *cells, note)
    console.print(table)

    if reclaimable:
        console.print(
            f"\n[bold]{_human_size(reclaimable)} reclaimable[/] — remove the redundant safetensors "
            "copies above (a GGUF/MLX build of each already exists)."
        )
    else:
        console.print("\n[dim]No redundant safetensors copies — nothing to reclaim.[/]")


@library_app.command("cards")
def cards_backfill(
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-fetch even models that already have a card.")] = False,
) -> None:
    """Backfill missing Hugging Face model cards into library models' ``.stabbur/`` sidecars.

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

    Verifies each model's total size, file count, and card against its ``.stabbur/metadata.json``
    (catches truncated/incomplete pulls or deleted files). Ollama models are content-addressed,
    so their blobs are checked for existence — and with ``--deep``, re-hashed against their sha256.

    A model whose sidecar merely *counted* differently — recorded before stabbur stopped counting
    download bookkeeping — is reported as a note and still passes; only real damage exits non-zero.
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
    ok_count = noted = 0
    for m in sorted(models, key=lambda x: x.name):
        r = library_ops.verify(m, deep=deep)
        ok_count += r.ok
        noted += bool(r.notes)
        if not r.ok:
            mark, detail = "[red]✗[/]", f"[red]{'; '.join(r.issues)}[/]"
        elif r.notes:
            mark, detail = "[yellow]~[/]", f"[yellow]{'; '.join(r.notes)}[/]"
        else:
            mark, detail = "[green]✓[/]", "[green]ok[/]"
        table.add_row(mark, m.name, r.checked, detail)
    console.print(table)
    bad = len(models) - ok_count
    summary = f"\n[bold]{ok_count}/{len(models)} ok[/]"
    if noted:
        summary += f" · [yellow]{noted} with stale recorded counts[/]"
    if bad:
        summary += f" · [red]{bad} with issues[/]"
    console.print(summary)
    if bad:
        raise typer.Exit(1)


@library_app.command()
def manifest(
    save: Annotated[
        Path | None,
        typer.Option("--save", help="Write the want list to this file (default: print TOML to stdout)."),
    ] = None,
) -> None:
    """Export your library as a re-pullable want list (TOML) — the input for `stabbur library sync`.

    Reads each model's recorded source (its ``.stabbur/`` sidecar, or inferred for older pulls) and
    emits a portable ``[[model]]`` list. Keep the file wherever you like — commit it, or copy it to
    a new drive — then ``stabbur library sync <file>`` re-downloads everything in it that's missing. No
    state is kept in the library; the manifest is generated on demand.
    """
    library_ops.roots()  # fail fast + clean if no library is configured
    entries, comments = wantlist.collect(library_ops.scan())
    text = wantlist.render(entries, comments)
    if save is None:
        typer.echo(text, nl=False)  # raw TOML, pipeable
        return
    fsatomic.write_text(save, text)
    console.print(f"[green]Wrote[/] {_count(len(entries), 'model')} [dim]→[/] {save}")
    if comments:
        console.print(f"[dim]{len(comments)} model(s) noted as comments (not source-re-pullable).[/]")


@library_app.command("sets")
def curated_sets() -> None:
    """List the curated model sets — validated groups you can pull in one go with `library sync`.

    A set is the catalog as data rather than a page of copy-paste pull commands: `library sync
    <set>` diffs it against your library and downloads only what's missing, so re-running it after
    a partial download costs nothing.
    """
    table = Table(box=box.SIMPLE, header_style="bold")
    for col in ("SET", "MODELS", "SIZE", "WHAT IT IS"):
        table.add_column(col, style="cyan" if col == "SET" else None)
    for s in curated.SETS:
        table.add_row(s.name, str(len(s.entries)), s.size_hint, s.description)
    console.print(table)
    console.print("[dim]Pull one:[/] stabbur library sync <set>  [dim](--dry-run to see the plan first).[/]")


@library_app.command()
def sync(
    wantfile: Annotated[
        str,
        typer.Argument(
            help="A want list (TOML) from `stabbur library manifest`, or a curated set name "
            "(see `stabbur library sets`)."
        ),
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="List what would be pulled and exit; download nothing.")
    ] = False,
    shared: Annotated[
        bool,
        typer.Option("--shared", help="Pull into the shared/default library instead of the project-local one."),
    ] = False,
    repair: Annotated[
        bool,
        typer.Option("--repair", help="Also re-pull models that are present but fail verification."),
    ] = False,
    deep: Annotated[
        bool,
        typer.Option("--deep", help="With --repair, re-hash content instead of checking size/count (slow)."),
    ] = False,
) -> None:
    """Re-download every model in a want list that's missing from your library.

    Diffs the file against your library (models already present are skipped) and pulls the rest via
    the normal per-source paths. Ollama entries need the model in your local Ollama store; LM Studio
    backups are recorded as their Hugging Face equivalent. One model failing doesn't stop the others
    — the command exits non-zero if any failed. ``--dry-run`` shows the plan without downloading.
    """
    # A curated set is resolved first, but only when no such file exists: a file on disk always
    # wins, so a local `voice.toml` can never be shadowed by a set that happens to share its name.
    path = Path(wantfile)
    chosen = None if path.is_file() else curated.get(wantfile)
    if chosen is not None:
        wants = list(chosen.entries)
        console.print(f"[bold]{chosen.name}[/] [dim]— {chosen.description}[/]")
    elif not path.is_file():
        console.print(f"[red]No such want list or curated set:[/] {wantfile}")
        console.print(f"[dim]Sets:[/] {', '.join(curated.names())}  [dim](stabbur library sets)[/]")
        raise typer.Exit(2)
    else:
        try:
            wants = wantlist.parse(path.read_text(encoding="utf-8"))
        except (ValueError, tomllib.TOMLDecodeError) as exc:
            console.print(f"[red]Invalid want list[/] ({wantfile}): {escape(str(exc))}")
            raise typer.Exit(2) from exc

    if deep and not repair:
        console.print("[yellow]--deep only applies with --repair[/] — verification is what it deepens.")
    root = library_ops.default_root() if shared else library_ops.roots()[0]

    # --repair treats a model that fails verification as absent, so the pull below rewrites it.
    # Re-pulling genuinely repairs: the HF snapshot re-fetches any file whose size/etag no longer
    # matches, rather than seeing a directory and skipping.
    damaged: list[str] = []

    def _damaged(model: library_ops.LibraryModel) -> bool:
        if library_ops.verify(model, deep=deep).ok:
            return False
        damaged.append(model.name)
        return True

    scanned = library_ops.scan()
    if repair:
        with console.status("[cyan]Verifying library…", spinner="dots"):
            sp = wantlist.plan(wants, scanned, unhealthy=_damaged)
    else:
        sp = wantlist.plan(wants, scanned)
    for w in sp.present:
        console.print(f"[dim]— have[/] {w.name} [dim]({w.source})[/]")
    for name in sorted(damaged):
        console.print(f"[yellow]! damaged[/] {name} [dim](failed verification — will re-pull)[/]")
    if not sp.missing:
        suffix = " and verified" if repair else ""
        console.print(f"\n[green]Nothing to sync[/] — all {_count(len(wants), 'model')} present{suffix}.")
        return
    if dry_run:
        console.print(f"\n[bold]{len(sp.missing)} to pull[/] [dim](dry run)[/]:")
        for w in sp.missing:
            fmt = f" [dim]{w.model_format}[/]" if w.model_format else ""
            globs = f" [dim]include {' '.join(w.include)}[/]" if w.include else ""
            console.print(f"  [cyan]{w.source}[/] {w.name}{fmt}{globs}")
        console.print("\n[dim]Re-run without --dry-run to download them.[/]")
        return

    pulled = failed = 0
    for w in sp.missing:
        try:
            result = wantlist.pull_entry(w, root)
        except Exception as exc:  # noqa: BLE001 - one bad model must not abort the sync
            failed += 1
            console.print(f"[red]✗ fail[/] {w.name} [dim]— {escape(str(exc))}[/]")
            continue
        pulled += 1
        console.print(f"[green]✓ pull[/] {w.name} [dim]({result.size_human})[/]")
    console.print(f"\n[bold]{pulled} pulled[/] · {len(sp.present)} already present · {failed} failed")
    if failed:
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

    Assignments stay plain name references (``stabbur library tag``); this stores the tag's *style*
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
    scanned = library_ops.scan()
    lib_names = {m.name.lower() for m in scanned}
    index = _library_index(scanned)

    imported = skipped = failed = 0
    for entry in sorted(entries, key=lambda e: e.name):
        if entry.name.lower() in lib_names:
            skipped += 1
            # A source copy that is a *different* quant of a model already on the drive is still
            # skipped: it imports to the same library path, so pulling it would replace the copy
            # that's there. Say which case this is instead of implying stabbur already has it.
            verdict = _in_library(entry.name, entry.model_format, entry.size_bytes, index)
            why = (
                "already in library"
                if verdict == IN_LIBRARY_SAME
                else "a different quant/format of it is in the library — pull it by name to replace that copy"
            )
            console.print(f"[dim]— skip[/] {entry.name} [dim]({why})[/]")
            continue
        try:
            result = catalog_ops.pull(source, entry.name, library_root=root, move=move)
        except Exception as exc:  # noqa: BLE001 - one bad model must not abort the batch
            failed += 1
            console.print(f"[red]✗ fail[/] {entry.name} [dim]— {escape(str(exc))}[/]")
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
    ``stabbur library pull voice kokoro``) into ``<root>/voice/`` — downloading it if it
    isn't already in the Hugging Face cache. This is the project-aware way to add a
    voice model (``stabbur voice import`` is the older cache-only alias).
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
    if result.source_removed:
        suffix = " (local copy removed)"
    elif move and result.already_present:
        # Nothing was copied, so there was nothing to verify — saying the copy failed verification
        # here reads as a warning about a model that is simply already where it belongs.
        suffix = " [dim](already in the library — local copy kept)[/]"
    elif move:
        suffix = " [yellow](local copy KEPT — copy could not be verified)[/]"
    else:
        suffix = ""
    console.print(f"Done: {result.file_count} files, {result.size_human} -> {result.destination}{suffix}")


# What the IN LIBRARY column shows per :func:`_in_library` verdict. A plain tick is reserved for
# the copy the library actually holds; "~" means the same model is there in another shape.
_IN_LIBRARY_MARK = {
    IN_LIBRARY_SAME: "[green]✓[/]",
    IN_LIBRARY_OTHER_QUANT: "[yellow]~ other quant[/]",
    IN_LIBRARY_OTHER_FORMAT: "[yellow]~ other format[/]",
    "": "[dim]—[/]",
}


@library_app.command()
def sources(
    source: SourceOption = None,
    show_all: Annotated[bool, typer.Option("--all", "-a", help="Include embedding/vision/partial entries.")] = False,
) -> None:
    """Browse models in your app caches (HF / Ollama / LM Studio) you can pull.

    These live in caches on this machine (e.g. ~/.cache/huggingface) — *not* your
    library. The IN LIBRARY column marks what you've already pulled; pull leftover
    local models onto the drive with ``stabbur library pull --move`` to free local disk.
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

    index = _library_index()
    status = {e.name: _in_library(e.name, e.model_format, e.size_bytes, index) for e in shown}

    pulled = sum(1 for e in shown if status[e.name] == IN_LIBRARY_SAME)
    differing = sum(1 for e in shown if status[e.name] in (IN_LIBRARY_OTHER_QUANT, IN_LIBRARY_OTHER_FORMAT))
    shown_total = _human_size(sum(e.size_bytes for e in shown))
    differs_note = f" · {differing} a different quant/format" if differing else ""
    console.print(
        f"\n[bold]{_count(len(shown), 'model')} · {shown_total}[/] in local app caches "
        f"[dim]· {pulled} already in your library{differs_note} · {len(shown) - pulled} to pull[/]"
    )
    console.print(
        "[dim]Caches on this machine, not your library — see[/] stabbur library ls [dim]for your library.[/]\n"
    )
    for src in sorted({e.source for e in shown}, key=lambda s: s.value):
        rows = sorted((e for e in shown if e.source is src), key=lambda e: e.name)
        table = Table(box=box.SIMPLE_HEAD, title=f"[bold]{src.value}[/]", title_justify="left", pad_edge=False)
        table.add_column("IN LIBRARY")
        table.add_column("FORMAT")
        table.add_column("SIZE", justify="right")
        table.add_column("NAME", style="white")
        if show_all:
            table.add_column("CHAT?", justify="center")
        for e in rows:
            mark = _IN_LIBRARY_MARK[status[e.name]]
            extra = (["[green]chat[/]" if e.generative else "[dim]no[/]"]) if show_all else []
            table.add_row(mark, _fmt_cell(e.model_format), e.size_human, e.name, *extra)
        console.print(table)
    if differing:
        console.print(
            "\n[dim]~ = the library has this model under a different quant or format, not this copy. "
            "Pulling it would replace the copy already there — remove that one first if you mean to.[/]"
        )
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
        "Pull one with[/] stabbur library pull huggingface <model>"
    )
