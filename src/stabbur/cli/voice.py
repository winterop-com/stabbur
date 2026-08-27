"""`stabbur voice` - list, speak, and import TTS/STT voice models."""

import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.markup import escape
from rich.table import Table

from stabbur import catalog as catalog_ops
from stabbur import (
    host,
)
from stabbur import library as library_ops
from stabbur.cli._app import voice_app
from stabbur.cli._common import (
    _pull_voice_all,
    console,
)
from stabbur.models import ModelSource


@voice_app.command("voices")
def voices() -> None:
    """List the built-in Kokoro voices (the always-available in-chat TTS)."""
    from stabbur.voice import kokoro  # noqa: PLC0415

    if not kokoro.available():
        typer.secho("Kokoro TTS is unavailable — reinstall stabbur (`uv sync`).", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    table = Table(title="Kokoro voices", box=None, header_style="bold")
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column("language")
    table.add_column("gender")
    for v in kokoro.voices():
        table.add_row(v.id, v.name, v.language, v.gender)
    console.print(table)
    console.print(f'\n[dim]{len(kokoro.voices())} voices — use with[/] stabbur voice speak --voice <id> "…"')


@voice_app.command("speak")
def speak(
    words: Annotated[list[str], typer.Argument(help="Text to synthesize into speech.")],
    voice: Annotated[
        str | None,
        typer.Option("--voice", "-v", help="Kokoro voice id (e.g. af_heart; see `stabbur voice voices`)."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="A voice model from the registry (see `stabbur voice list`)."),
    ] = None,
    ref_audio: Annotated[
        Path | None,
        typer.Option(
            "--ref-audio", help="Reference clip to clone a voice from (cloneable models; pair with --ref-text)."
        ),
    ] = None,
    ref_text: Annotated[
        str | None,
        typer.Option("--ref-text", help="Exact transcript of --ref-audio (required for a good clone)."),
    ] = None,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Pin a seeded model's otherwise-random voice for a reproducible result."),
    ] = None,
    speed: Annotated[
        float,
        typer.Option("--speed", help="Playback speed multiplier, 0.25-2.0 (default 1.0)."),
    ] = 1.0,
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
    ``--model`` uses a registry voice model via the mlx-audio runtime — with ``--ref-audio``
    + ``--ref-text`` a cloneable model mimics the voice in that clip, and ``--seed`` pins a
    seeded model's random voice. ``--format`` transcodes the result (ffmpeg); with ``-o``
    writes there, otherwise a temp file is played.
    """
    from stabbur.voice import audio as audio_export  # noqa: PLC0415
    from stabbur.voice import registry as voice_registry  # noqa: PLC0415
    from stabbur.voice import tts  # noqa: PLC0415

    text = tts.speech_text(" ".join(words))  # accept an unquoted phrase; strip any Markdown
    if not audio_export.is_supported(fmt):
        typer.secho(
            f"Unknown format {fmt!r}; use one of {', '.join(audio_export.FORMATS)}.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    if voice is not None and model is not None:
        console.print(f"[yellow]--voice takes precedence[/] — ignoring `--model {model}` (that's for mlx-audio).")
        model = None  # it lost; don't then fail resolving it (two contradictory messages)
    spec = voice_registry.get(model) if model else None
    spec = spec or (voice_registry.by_repo(model) if model else None)
    if model is not None and spec is None:
        typer.secho(f"No voice model matches {model!r} (see `stabbur voice list`).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    # Enforce the registry's supported flag at the action (A6/VO-M3): reject an unsupported model
    # upfront with a clear reason instead of loading it and failing on empty audio.
    if spec is not None and not spec.supported:
        typer.secho(f"{spec.display_name} isn't supported for synthesis in stabbur yet.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        if voice is not None or spec is None:  # Kokoro (ONNX) — the lightweight default engine
            data = _synth_kokoro(text, voice or "af_heart", speed=speed)
        else:  # a registry voice model via the mlx-audio runtime
            data = _synth_mlx(spec, text, ref_audio=ref_audio, ref_text=ref_text, seed=seed, speed=speed)
        data = audio_export.convert(data, fmt)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    _finish_speak(data, fmt, output, play)


def _synth_kokoro(text: str, voice: str, *, speed: float = 1.0) -> bytes:
    """Synthesize with Kokoro (ONNX) — the lightweight built-in engine; fetches its model on first use."""
    from stabbur.voice import kokoro  # noqa: PLC0415

    if not kokoro.available():
        typer.secho("Kokoro TTS is unavailable — reinstall stabbur (`uv sync`).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not kokoro.assets_present():
        with console.status("[cyan]Downloading Kokoro voices (~310 MB, first run only)…", spinner="dots"):
            kokoro.ensure_assets()
    with console.status(f"[cyan]Synthesizing speech ({voice})…", spinner="dots"):
        return kokoro.synthesize(text, voice, None, speed=speed).read_bytes()


def _synth_mlx(
    spec: Any, text: str, *, ref_audio: Path | None, ref_text: str | None, seed: int | None, speed: float = 1.0
) -> bytes:
    """Synthesize with the mlx-audio runtime, supporting voice cloning + a pinned seed."""
    from stabbur.voice import runtime as voice_runtime  # noqa: PLC0415

    if not voice_runtime.available():
        typer.secho("mlx-audio not installed. Run `uv sync --extra voice`.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    matches = [m for m in library_ops.find(spec.repo) if m.voice_kind == "tts"]
    if not matches:
        typer.secho(
            f"{spec.display_name} is not in the library (`stabbur voice import`).", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)
    extra: dict[str, Any] = {"seed": seed} if seed is not None else {}
    if speed != 1.0:
        extra["speed"] = speed  # honored by models that support it; ignored otherwise
    with console.status(f"[cyan]Synthesizing speech ({spec.display_name})…", spinner="dots"):
        return voice_runtime.synthesize(matches[0].load_target, text, ref_audio=ref_audio, ref_text=ref_text, **extra)


def _finish_speak(data: bytes, fmt: str, output: Path | None, play: bool) -> None:
    """Write synthesized audio (to ``output`` or a temp file) and optionally play it.

    A temp file (no ``--output``) is unlinked once playback returns — it exists only to hand a
    path to the OS audio player — so repeated ``stabbur voice speak`` calls don't litter ``/tmp``.
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


# Attachment helpers live in stabbur.attach (shared with the Textual chat); alias the
# names used below.


@voice_app.command("list")
@voice_app.command("ls", hidden=True)  # alias, matching `stabbur library ls`
def voice_list() -> None:
    """List known voice models (TTS/STT) and where each lives — HF cache or a project library.

    Presence is checked across the project's libraries (project-local + ``@shared``), like
    ``stabbur library ls`` — so a model on the shared drive shows as ``library`` here too.
    """
    from stabbur import voice  # noqa: PLC0415

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
        "[dim]Add one to the project library:[/] stabbur library pull voice <id>  [dim](downloads if needed).[/]"
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
    """Import voice models into a library — a project-aware alias for ``stabbur library pull voice``.

    Targets the project-local library by default (``--shared`` for the archive). A named model
    not yet in the HF cache is downloaded; ``--all`` imports only what's already cached.
    """
    if all_ and models:  # like `stabbur library pull`, reject the contradictory combination
        console.print("[red]Give voice model id(s) OR --all, not both.[/]")
        raise typer.Exit(2)
    root = library_ops.default_root() if shared else library_ops.roots()[0]
    if all_:
        _pull_voice_all(root, prune)
        return
    if not models:
        console.print("[red]Give a model id or --all[/] (see [bold]stabbur voice list[/]).")
        raise typer.Exit(1)
    failed = False
    for vid in models:
        typer.echo(f"Pulling voice:{vid} -> {root} …")
        try:
            result = catalog_ops.pull(ModelSource.voice, vid, library_root=root, move=prune)
        except Exception as exc:  # noqa: BLE001 - unknown id / download / disk; surface cleanly
            console.print(f"  [red]failed[/]: {escape(str(exc))}")
            failed = True
            continue
        console.print(f"  [green]done[/] {result.size_human} → {result.destination}")
    console.print("[dim]Same thing, project-aware:[/] stabbur library pull voice <id>")
    if failed:
        raise typer.Exit(1)


# --- plugins ---------------------------------------------------------------
