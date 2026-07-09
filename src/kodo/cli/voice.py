"""`kodo voice` - list, speak, import, and set up TTS/STT voice models."""

import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.table import Table

from kodo import catalog as catalog_ops
from kodo import (
    host,
)
from kodo import library as library_ops
from kodo.cli._app import voice_app
from kodo.cli._common import (
    _pull_voice_all,
    console,
)
from kodo.config import get_settings
from kodo.models import ModelSource


@voice_app.command("voices")
def voices() -> None:
    """List the built-in Kokoro voices (the always-available in-chat TTS)."""
    from kodo.voice import kokoro  # noqa: PLC0415

    if not kokoro.available():
        typer.secho("Kokoro TTS is unavailable — reinstall kodo (`uv sync`).", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    table = Table(title="Kokoro voices", box=None, header_style="bold")
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column("language")
    table.add_column("gender")
    for v in kokoro.voices():
        table.add_row(v.id, v.name, v.language, v.gender)
    console.print(table)
    console.print(f'\n[dim]{len(kokoro.voices())} voices — use with[/] kodo voice speak --voice <id> "…"')


@voice_app.command("speak")
def speak(
    words: Annotated[list[str], typer.Argument(help="Text to synthesize into speech.")],
    voice: Annotated[
        str | None,
        typer.Option("--voice", "-v", help="Kokoro voice id (e.g. af_heart; see `kodo voice voices`)."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Voice model: an mlx-audio model (dia, qwen3-tts) or a library OuteTTS."),
    ] = None,
    ref_audio: Annotated[
        Path | None,
        typer.Option("--ref-audio", help="Reference clip to clone a voice from (Dia; pair with --ref-text)."),
    ] = None,
    ref_text: Annotated[
        str | None,
        typer.Option("--ref-text", help="Exact transcript of --ref-audio (required for a good clone)."),
    ] = None,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Pin Dia's otherwise-random voice for a reproducible result."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: wav, mp3, flac, opus, ogg, aac (non-wav needs ffmpeg)."),
    ] = "wav",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write audio here (default: a temp file, played aloud)."),
    ] = None,
    play: Annotated[
        bool,
        typer.Option("--play/--no-play", help="Play the audio after generating (via the OS audio player)."),
    ] = True,
) -> None:
    """Text-to-speech: synthesize ``text`` to audio.

    ``--voice`` picks one of Kokoro's built-in voices (the lightweight default engine).
    ``--model dia`` (or ``qwen3-tts``) uses the mlx-audio runtime — with ``--ref-audio`` +
    ``--ref-text`` Dia clones the voice in that clip, or ``--seed`` pins its random voice.
    Any other ``--model`` uses ``llama-tts``/OuteTTS. ``--format`` transcodes the result
    (ffmpeg); with ``-o`` writes there, otherwise a temp file is played.
    """
    from kodo.voice import audio as audio_export  # noqa: PLC0415
    from kodo.voice import registry as voice_registry  # noqa: PLC0415
    from kodo.voice import tts  # noqa: PLC0415

    text = tts.speech_text(" ".join(words))  # accept an unquoted phrase; strip any Markdown
    if not audio_export.is_supported(fmt):
        typer.secho(
            f"Unknown format {fmt!r}; use one of {', '.join(audio_export.FORMATS)}.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    if voice is not None and model is not None:
        console.print(
            f"[yellow]--voice takes precedence[/] — ignoring `--model {model}` (that's for mlx-audio/OuteTTS)."
        )
    spec = voice_registry.get(model) if model else None
    spec = spec or (voice_registry.by_repo(model) if model else None)
    # Enforce the registry's supported flag at the action (A6/VO-M3): reject an unsupported model
    # (e.g. Qwen3-TTS) upfront with a clear reason instead of loading it and failing on empty audio.
    if spec is not None and not spec.supported:
        typer.secho(f"{spec.display_name} isn't supported for synthesis in kodo yet.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        if voice is not None:  # Kokoro (ONNX) — the lightweight preset engine
            data = _synth_kokoro(text, voice)
        elif spec is not None and spec.backend == voice_registry.Backend.mlx_audio:  # Dia / Qwen3-TTS
            data = _synth_mlx(spec, text, ref_audio=ref_audio, ref_text=ref_text, seed=seed)
        else:  # llama-tts / OuteTTS
            data = _synth_oute(model, text)
        data = audio_export.convert(data, fmt)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    _finish_speak(data, fmt, output, play)


def _synth_kokoro(text: str, voice: str) -> bytes:
    """Synthesize with Kokoro (ONNX) — the lightweight built-in engine; fetches its model on first use."""
    from kodo.voice import kokoro  # noqa: PLC0415

    if not kokoro.available():
        typer.secho("Kokoro TTS is unavailable — reinstall kodo (`uv sync`).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not kokoro.assets_present():
        with console.status("[cyan]Downloading Kokoro voices (~310 MB, first run only)…", spinner="dots"):
            kokoro.ensure_assets()
    with console.status(f"[cyan]Synthesizing speech ({voice})…", spinner="dots"):
        return kokoro.synthesize(text, voice, None).read_bytes()


def _synth_mlx(spec: Any, text: str, *, ref_audio: Path | None, ref_text: str | None, seed: int | None) -> bytes:
    """Synthesize with the mlx-audio runtime (Dia / Qwen3-TTS), supporting voice cloning + a pinned seed."""
    from kodo.voice import runtime as voice_runtime  # noqa: PLC0415

    if not voice_runtime.available():
        typer.secho("mlx-audio not installed. Run `uv sync --extra voice`.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    matches = [m for m in library_ops.find(spec.repo) if m.voice_kind == "tts"]
    if not matches:
        typer.secho(f"{spec.display_name} is not in the library (`kodo voice import`).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    extra: dict[str, Any] = {"seed": seed} if seed is not None else {}
    with console.status(f"[cyan]Synthesizing speech ({spec.display_name})…", spinner="dots"):
        return voice_runtime.synthesize(matches[0].load_target, text, ref_audio=ref_audio, ref_text=ref_text, **extra)


def _synth_oute(model: str | None, text: str) -> bytes:
    """Synthesize with llama-tts / OuteTTS — the default when no ``--voice`` or mlx-audio model is given."""
    from kodo.voice import tts  # noqa: PLC0415

    model_path = vocoder_path = None
    if model is not None:
        matches = [m for m in library_ops.find(model) if m.tts]
        if not matches:
            typer.secho(f"No TTS model matches {model!r} (see `kodo library ls`).", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        model_path, vocoder_path = matches[0].load_target, matches[0].vocoder
    with console.status("[cyan]Synthesizing speech…", spinner="dots"):
        return tts.synthesize(text, None, model_path, vocoder_path).read_bytes()


def _finish_speak(data: bytes, fmt: str, output: Path | None, play: bool) -> None:
    """Write synthesized audio (to ``output`` or a temp file) and optionally play it.

    A temp file (no ``--output``) is unlinked once playback returns — it exists only to hand a
    path to the OS audio player — so repeated ``kodo voice speak`` calls don't litter ``/tmp``.
    """
    if output is not None:
        dest = output
    else:
        fd, name = tempfile.mkstemp(suffix=f".{fmt}")
        os.close(fd)
        dest = Path(name)
    dest.write_bytes(data)
    console.print(f"[green]Wrote[/] {dest}")
    if output is not None:
        if play:
            console.print("[dim](not auto-played — audio was written to your -o file)[/]")
        return
    try:
        if not play:
            return
        cmd = host.audio_play_command(dest)
        if cmd is None:
            console.print("[dim](no audio player found; install one to auto-play, e.g. ffmpeg)[/]")
            return
        import subprocess  # noqa: PLC0415

        subprocess.run(cmd)  # noqa: S603 - local playback of our own file
    finally:
        dest.unlink(missing_ok=True)  # the temp file existed only to feed the player


# Attachment helpers live in kodo.attach (shared with the Textual chat); alias the
# names used below.


@voice_app.command("setup")
def voice_setup() -> None:
    """Make Dia self-contained: seed its DAC codec into the (drive) HF cache.

    Dia decodes through ``descript-audio-codec-44khz``, which mlx-audio fetches by repo
    id at synth time — separately from Dia's own weights. With the HF cache redirected
    onto the library drive, this downloads the codec there once so Dia works offline and
    travels with the drive.
    """
    from kodo import hfcache  # noqa: PLC0415
    from kodo.voice import dac  # noqa: PLC0415

    cache = hfcache.drive_cache_dir()
    where = f"[green]{cache}[/] (on the drive)" if cache else "[yellow]~/.cache/huggingface[/] (machine-local)"
    console.print(f"HF cache: {where}")
    if not cache:
        console.print("[dim]Set[/] KODO_LIBRARY_ROOT [dim]to a real library drive so the codec travels with it.[/]")
    if dac.codec_present():
        console.print(f"[green]DAC codec already present[/] [dim]({dac.DAC_REPO}).[/]")
        return
    console.print(f"[dim]Downloading[/] {dac.DAC_REPO} [dim](~293 MB)…[/]")
    try:
        path = dac.seed_codec(token=get_settings().hf_token)
    except Exception as exc:  # noqa: BLE001 - a network/hub failure is user-facing, not a crash
        console.print(f"[red]Failed to seed the DAC codec:[/] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Seeded[/] the DAC codec [dim]→ {path}[/]. Dia is now self-contained.")


@voice_app.command("list")
@voice_app.command("ls", hidden=True)  # alias, matching `kodo library ls`
def voice_list() -> None:
    """List known voice models (TTS/STT) and where each lives — HF cache or a project library.

    Presence is checked across the project's libraries (project-local + ``@shared``), like
    ``kodo library ls`` — so a model on the shared drive shows as ``library`` here too.
    """
    from kodo import voice  # noqa: PLC0415

    # Merge presence across every library in scope: a model counts as "in library" if it's in
    # any of them (project-local or @shared). in_cache is machine-global (the HF cache).
    merged: dict[str, voice.VoicePresence] = {}
    for r in library_ops.roots():
        for p in voice.discover(r):
            cur = merged.get(p.spec.id)
            if cur is None or (p.in_library and not cur.in_library):
                merged[p.spec.id] = p

    table = Table(box=box.SIMPLE, header_style="bold")
    for col in ("ID", "KIND", "VOICE", "BACKEND", "WHERE", "SIZE"):
        table.add_column(col, style="cyan" if col == "ID" else None)
    for p in merged.values():
        s = p.spec
        where = "[green]library[/]" if p.in_library else ("[yellow]hf-cache[/]" if p.in_cache else "[dim]—[/]")
        table.add_row(
            s.id, s.kind.value, s.voice_mode.value, s.backend.value, where, p.size_human if p.available else "—"
        )
    console.print(table)
    console.print(
        "[dim]Add one to the project library:[/] kodo library pull voice <id>  [dim](downloads if needed).[/]"
    )


@voice_app.command("import")
def voice_import(
    models: Annotated[list[str], typer.Argument(help="Voice model id(s) to import; omit with --all.")] = [],
    all_: Annotated[bool, typer.Option("--all", help="Import every voice model already in the HF cache.")] = False,
    prune: Annotated[bool, typer.Option("--prune", help="Delete the HF-cache copy after a verified import.")] = False,
    shared: Annotated[
        bool, typer.Option("--shared", help="Import into the shared/default library instead of the project-local one.")
    ] = False,
) -> None:
    """Import voice models into a library — a project-aware alias for ``kodo library pull voice``.

    Targets the project-local library by default (``--shared`` for the archive). A named model
    not yet in the HF cache is downloaded; ``--all`` imports only what's already cached.
    """
    if all_ and models:  # like `kodo library pull`, reject the contradictory combination
        console.print("[red]Give voice model id(s) OR --all, not both.[/]")
        raise typer.Exit(2)
    root = library_ops.default_root() if shared else library_ops.roots()[0]
    if all_:
        _pull_voice_all(root, prune)
        return
    if not models:
        console.print("[red]Give a model id or --all[/] (see [bold]kodo voice list[/]).")
        raise typer.Exit(1)
    failed = False
    for vid in models:
        typer.echo(f"Pulling voice:{vid} -> {root} …")
        try:
            result = catalog_ops.pull(ModelSource.voice, vid, library_root=root, move=prune)
        except Exception as exc:  # noqa: BLE001 - unknown id / download / disk; surface cleanly
            console.print(f"  [red]failed[/]: {exc}")
            failed = True
            continue
        console.print(f"  [green]done[/] {result.size_human} → {result.destination}")
    console.print("[dim]Same thing, project-aware:[/] kodo library pull voice <id>")
    if failed:
        raise typer.Exit(1)


# --- plugins ---------------------------------------------------------------
