"""`stabbur chat` - the terminal chat: interactive TUI, one-shot -p, tools, and serve-attach."""

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import httpx
import typer
from rich.console import Console
from rich.markup import escape

from stabbur import (
    capabilities,
    mcpservers,
    project,
    runtime,
)
from stabbur import library as library_ops
from stabbur.cli._app import app
from stabbur.cli._common import (
    FormatOption,
    _cli_mcp_spec,
    _load_media,
    _maybe_library_model,
    _normalize_server_url,
    _resolve_library_model,
    console,
)
from stabbur.config import get_settings

if TYPE_CHECKING:
    from stabbur import agent
    from stabbur import tools as mcp_tools
    from stabbur.chat_tui.app import RemoteEndpoint
    from stabbur.project import AssistantInfo


def _confirm_policy(assistant: "AssistantInfo | None") -> Literal["all", "writes", "none"]:
    """The tool-confirmation policy for a session, derived from the resolved assistant target.

    A write-enabled target (an assistant that is not ``readonly``) gates its non-read-only tool calls
    behind confirmation (``"writes"``); no target, or a read-only one, gates nothing (``"none"`` —
    identical to the pre-confirmation behavior).
    """
    if assistant is None or assistant.readonly:
        return "none"
    return "writes"


def _resolve_target(proj: "project.Project | None", target_id: str | None) -> "tuple[AssistantInfo | None, str | None]":
    """Resolve a ``--target`` id against the project registry: ``(assistant, resolved_id)``.

    ``target_id=None`` picks the primary target (``resolved_id`` the primary's registry id); an explicit
    id must exist in the registry (else exit). Free-play (no project / no assistant targets) returns
    ``(None, None)`` — the full toolset — but an explicit ``--target`` there is a user error and exits.
    """
    registry = proj.registry if proj else None
    if registry is None or not registry.targets:
        if target_id is not None:
            console.print(f"[red]No assistant targets defined[/] — [cyan]--target {target_id}[/] has nothing to pick.")
            raise typer.Exit(1)
        return None, None
    if target_id is None:
        return registry.primary, registry.ids[0]
    resolved = registry.by_id(target_id)
    if resolved is None:
        known = ", ".join(registry.ids) or "(none)"
        console.print(f"[red]Unknown target {target_id!r}.[/] Known targets: {known}.")
        raise typer.Exit(1)
    return resolved, target_id


async def _approve_all(name: str, args: dict[str, Any]) -> bool:
    """A confirmation sink that approves every gated call (the ``--allow-writes`` opt-out)."""
    return True


# The leading bytes of the image formats a vision model can actually be sent. Checked instead of
# trusting the extension because the failure this prevents is silent and expensive: a non-image is
# base64'd into the request anyway, and the runtime answers with an HTTP error about a payload,
# never "that wasn't an image".
_IMAGE_MAGIC: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF87a",
    b"GIF89a",
    b"BM",  # BMP
    b"II*\x00",  # TIFF (little-endian)
    b"MM\x00*",  # TIFF (big-endian)
)


def _looks_like_image(path: Path) -> bool:
    """Whether ``path`` starts with a known image signature (RIFF/WebP handled separately)."""
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return False
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return True
    return any(head.startswith(magic) for magic in _IMAGE_MAGIC)


def _check_images(paths: list[Path]) -> None:
    """Refuse a ``--image`` attachment that isn't a readable image, before anything is sent.

    Nothing downstream checks: a PDF (or a typo'd path's neighbour) is base64'd into the request like
    any other file, and the only symptom is the runtime's own HTTP error — which, for a locally spawned
    runtime, names an ephemeral ``127.0.0.1`` port and says nothing about the attachment. Cheaper and
    kinder to say which file is not an image, and to say "image file not found" in words a reader
    recognizes (the shared media loader's ``kind`` names the *capability*, so it said "Vision not
    found", which is not a thing anyone has).
    """
    for path in paths:
        if not path.is_file():
            console.print(f"[red]image file not found:[/] {path}")
            raise typer.Exit(1)
        if not _looks_like_image(path):
            console.print(f"[red]not an image file:[/] {path} — expected PNG, JPEG, GIF, WebP, BMP or TIFF.")
            raise typer.Exit(1)


# A loopback URL in an error message: the runtime stabbur just spawned, on an ephemeral port. It is
# noise to the reader and leaks nothing useful, so it is stripped — while a URL the *user* typed
# (--server http://gpu-box:1234) is left alone, because there the host is the whole point.
_LOOPBACK_URL = re.compile(r"\s*(?:for url\s+)?['\"]?https?://(?:127\.0\.0\.1|localhost|\[::1\]):\d+[^\s'\"]*['\"]?")


def _clean_error(exc: Exception) -> str:
    """A failed turn as one readable line, without the internal runtime URL.

    httpx renders a status error as ``Client error '413 Payload Too Large' for url
    'http://127.0.0.1:<port>/v1/chat/completions'`` — a port number the user never chose, attached to a
    condition (an attachment the runtime won't accept) it never names. Answer with the condition.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 413:
            return "the model runtime rejected the request as too large — try a smaller attachment."
        return f"the model runtime returned HTTP {code} {exc.response.reason_phrase}".strip()
    return _LOOPBACK_URL.sub("", str(exc)).strip() or type(exc).__name__


def _extra_mcp_specs(
    values: list[str], resolved: "list[mcpservers.McpServer]"
) -> list[tuple[str | None, list[str], dict[str, str]]]:
    """The ``--mcp`` servers worth adding on top of the configured ones, warning about unspawnable ones.

    A ``--mcp`` naming a server the resolved ``mcp.json`` already configures **identically** (same
    command, args and env) is dropped: spawning a second copy costs a process, gives the model the same
    tools twice under two namespaces (``datetime__now`` and ``datetime2__now`` — one tester watched a
    model deliberate over which to call), and shifts the configured server's prefix out from under the
    ``--target`` routing table. Anything else is genuinely additional and is kept.

    A command that resolves to no executable is still kept (:func:`stabbur.tools.connect` records the
    real failure), but gets a warning on stderr first: a typo'd ``--mcp`` otherwise produces a session
    with no tools and no message at all.
    """
    from stabbur import tools as mcp_tools  # noqa: PLC0415

    configured = {(tuple([s.command, *s.args]), tuple(sorted(s.env.items()))) for s in resolved}
    extras: list[tuple[str | None, list[str], dict[str, str]]] = []
    for value in values:
        spec = _cli_mcp_spec(value)
        if (tuple(spec[1]), tuple(sorted(spec[2].items()))) in configured:
            continue  # already configured, identically: reuse that one rather than spawn a duplicate
        if spec[1] and not mcp_tools.command_found(spec[1][0]):
            typer.secho(f"--mcp {value!r}: no such command {spec[1][0]!r} — it will have no tools.", err=True)
        extras.append(spec)
    return extras


@app.command()
def chat(
    name: Annotated[
        str | None,
        typer.Argument(help="Library model (defaults to the project's model in stabbur.toml)."),
    ] = None,
    prompt: Annotated[
        str | None,
        typer.Option("-p", "--prompt", help="One-shot prompt, prints just the answer (Claude-style -p)."),
    ] = None,
    model_format: FormatOption = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", "-n", help="Cap generated tokens.")] = None,
    mcp: Annotated[
        list[str],
        typer.Option("--mcp", help="MCP server command(s) for tools; repeatable, e.g. --mcp stabbur-mcp-datetime."),
    ] = [],
    tools: Annotated[
        bool,
        typer.Option("--tools/--no-tools", help="Attach MCP tools. Use --no-tools for non-tool-trained models."),
    ] = True,
    system: Annotated[
        str | None,
        typer.Option("--system", help="System prompt for this session (overrides stabbur.toml)."),
    ] = None,
    image: Annotated[
        list[Path],
        typer.Option("--image", "-i", help="Attach image file(s) for a vision model (repeatable)."),
    ] = [],
    audio: Annotated[
        list[Path],
        typer.Option("--audio", "-a", help="Attach audio file(s) for an audio model (repeatable)."),
    ] = [],
    server: Annotated[
        str | None,
        typer.Option(
            "--server",
            help="Attach to a running `stabbur serve` (e.g. http://127.0.0.1:8000) instead of loading a "
            "model locally — the interactive TUI and one-shot -p both. Default from STABBUR_CHAT_SERVER / "
            "`stabbur config set server`.",
        ),
    ] = None,
    no_server: Annotated[
        bool,
        typer.Option(
            "--no-server",
            help="Load the model locally for this run: ignores a configured chat server and skips "
            "auto-attaching to a running `stabbur serve`.",
        ),
    ] = False,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="With -p, never render markdown; print raw text even to a terminal."),
    ] = False,
    save: Annotated[
        Path | None,
        typer.Option("--save", help="With -p, also write the exchange to a Markdown file."),
    ] = None,
    allow_writes: Annotated[
        bool,
        typer.Option(
            "--allow-writes",
            help="Auto-approve tool write-confirmations in this non-interactive run (a project with a "
            "write-enabled assistant otherwise DENIES un-confirmable writes).",
        ),
    ] = False,
    target: Annotated[
        str | None,
        typer.Option(
            "--target",
            help="Registry target id to route to (multi-target projects). Narrows tools to that target's "
            "servers plus shared ones and uses its confirm policy; defaults to the primary target.",
        ),
    ] = None,
) -> None:
    """Chat with a library model: full-screen TUI, one-shot with ``-p``, tools with ``--mcp``.

    Interactive chat opens a Textual app (markdown replies, live tool activity, a
    context footer); ``-p`` prints just the answer for scripting. In a project dir,
    ``stabbur.toml`` supplies the default model, its MCP tool servers, and a system
    prompt; ``--mcp`` flags add to (not replace) those, ``--system`` overrides the
    prompt, and ``--no-tools`` drops tools entirely (some models regurgitate the
    tool schema instead of calling it).
    """
    proj = project.load()
    # --target narrows the toolset (and derives the confirm policy) only on the scripted -p path for now;
    # the interactive Textual TUI connects its own toolset and would gate on the target while still
    # exposing sibling write tools, so refuse it there rather than half-apply it. (prompt is None == the
    # interactive TUI, local or --server attach.)
    if target is not None and prompt is None:
        console.print("[red]--target requires -p for now[/] — the interactive chat TUI always uses the primary target.")
        raise typer.Exit(1)
    model_name = project.resolve_model(name, proj)
    # Resolve the registry target this session routes to (--target, else the primary): its readonly flag
    # drives the confirm policy and its server set narrows the tools (below). A write-enabled target
    # gates non-read-only tool calls; in this non-interactive one-shot there is no human to confirm, so a
    # gated call is DENIED unless --allow-writes supplies a blanket auto-approve sink (with no project /
    # no assistant / a read-only target the policy is "none" — nothing is gated, identical to today).
    resolved_target, resolved_target_id = _resolve_target(proj, target)
    confirm_policy = _confirm_policy(resolved_target)
    # Attach to a running server (--server > STABBUR_CHAT_SERVER / machine config) instead of
    # spawning a runtime — the interactive TUI and one-shot -p both. For the interactive
    # attach the model may exist only on the server, so local resolution is best-effort
    # there; every other path still requires (and strictly resolves) a library model.
    if no_server and server is not None:
        console.print("[red]--server and --no-server are mutually exclusive.[/]")
        raise typer.Exit(2)
    # A configured server otherwise applies to every run, with no way back to a local load, so
    # --no-server is the per-run opt-out. ``server`` is checked against None rather than for
    # truthiness so an explicit ``--server ''`` also clears it instead of silently falling back.
    #
    # A project wins over that default. A project names the model it is *for*, and it owns the
    # copy: attaching to a machine-wide remote there ran someone else's build of a model the
    # project had just downloaded — the manifest said one thing and the session did another. The
    # machine default is what free-play chat gets; an explicit --server still overrides both.
    #
    # Silently, because running the project's own model *is* the expected thing here: the header
    # already names the model and says "local", and a line explaining the absence of a setting the
    # project never mentions is noise on every single run.
    configured = get_settings().chat_server
    if server is None and configured and proj is not None and proj.model:
        configured = None
    base_url = None if no_server else _normalize_server_url(server if server is not None else configured)
    interactive_remote = base_url is not None and prompt is None
    model: library_ops.LibraryModel | None
    remote_model_id: str | None = None
    if interactive_remote:
        # Only an explicitly typed name supplies local metadata: the server decides what runs,
        # so letting the machine/project *default* model label the session would mislead.
        model = _maybe_library_model(name, model_format) if name is not None else None
    elif base_url is not None:
        # Remote one-shot (-p against --server). The wire ``model`` field ALWAYS comes from the
        # server's own /v1/models listing, because the remote matches ids, not local filesystem
        # paths — and a name can exist both places.
        #
        # Only a name the user actually asked for is matched against the listing: an explicit
        # argument or the project's model. The MACHINE DEFAULT (`stabbur config set model`) is not
        # a request — it is what to load when nothing else says. Passing it here made every
        # `chat -p --server` fail with "does not serve <default>" on any remote holding other
        # models, and contradicted the documented "with no name, the remote's loaded model wins".
        requested = name if name is not None else (proj.model if proj else None)
        remote_model_id = _remote_model_id(base_url, requested)
        # Metadata (sampling, capabilities) is resolved against the id that will actually answer,
        # so it can never describe a different model than the one generating.
        model = _maybe_library_model(remote_model_id, model_format)
    else:
        if model_name is None:
            console.print(
                "[red]No model given.[/] Pass a model name (see [cyan]stabbur library ls[/]), "
                "set a machine default ([cyan]stabbur config set model <name>[/]), "
                "or define one in a project ([cyan]stabbur project init[/])."
            )
            raise typer.Exit(1)
        model = _resolve_library_model(model_name, model_format)

    # (name, argv, env) per server. A bare --mcp value resolves against advertised servers
    # (so `--mcp datetime` finds stabbur-mcp-datetime), else it's used verbatim as a command;
    # the rest come from the resolved mcp.json layers (global + project, see stabbur.mcpservers).
    #
    # CONFIGURED SERVERS COME FIRST, and a --mcp that duplicates one is dropped. Prefixes are assigned
    # in connect() order, so a leading --mcp copy of a configured server took the bare prefix and pushed
    # the configured one to `datetime2` — which is not what build_target_routing (built from the resolved
    # servers alone) predicts, so --target then scoped the target to the WRONG server, and the model saw
    # the same tools twice under two namespaces. Ordering them last means an extra can only ever take a
    # suffixed prefix; deduping means the ordinary `--mcp datetime` case adds nothing at all.
    resolved_servers = mcpservers.resolve() if tools else []
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]] = (
        [s.to_spec() for s in resolved_servers] + _extra_mcp_specs(mcp, resolved_servers) if tools else []
    )
    # Per-target routing table (id -> owned tool prefixes); --target narrows the connected toolset to its
    # servers + shared ones. Built by the one production helper the serve lifespan uses, so the CLI and
    # web narrow identically. Empty outside a project → no narrowing (full toolset).
    from stabbur import tools as mcp_tools  # noqa: PLC0415

    target_routing = (
        mcp_tools.build_target_routing(resolved_servers, proj.registry) if proj else mcp_tools.TargetRouting()
    )
    system_prompt = system if system is not None else (proj.system_prompt if proj else "")
    # Capabilities are unknowable without a local copy (remote attach): load media unwarned.
    caps = capabilities.capabilities(model) if model is not None else None
    _check_images(image)
    images = _load_media(image, model, kind="vision", default_mime="image/png", capable=caps.vision if caps else True)
    audios = _load_media(audio, model, kind="audio", default_mime="audio/wav", capable=caps.audio if caps else True)

    # With nothing configured, auto-attach to a running `stabbur serve` locked to this model (one-shot
    # only): reuse its resident weights instead of reloading. A stderr note keeps it non-surprising.
    # The interactive TUI does NOT auto-attach — silently disabling /model would surprise.
    # --no-server opts out of this too: it asks for a local load, and attaching here would not be one.
    if base_url is None and prompt is not None and not no_server:
        from stabbur.runtime import serve_registry  # noqa: PLC0415

        assert model is not None  # every non-interactive-remote path resolved strictly above
        found = serve_registry.discover(model.name)
        if found is not None:
            base_url = found.base_url
            typer.secho(f"↳ attaching to running stabbur serve at {base_url}", fg=typer.colors.BRIGHT_BLACK, err=True)

    # Render the reply as markdown only when -p writes to a real terminal (the git/bat/ls
    # convention): an interactive `stabbur chat -p "…"` gets tables/headings/code like the TUI,
    # while a pipe or redirect stays raw and clean. `--raw` forces raw even on a TTY. Rendering
    # buffers the whole reply (markdown needs the full text), so live token streaming is traded
    # for formatting; the raw path keeps streaming.
    render = prompt is not None and not raw and _isatty()

    if interactive_remote:
        # Interactive attach: probe the server for its loaded model, then hand the TUI a
        # RemoteEndpoint — no runtime spawn, and the server is left running on exit. Outside
        # the try below: _probe_remote exits with its own message (and typer.Exit IS a
        # RuntimeError, which that except would garble into a bare "1").
        assert base_url is not None
        _chat_attached(base_url, model, name, mcp_servers, system_prompt, images, audios, max_tokens)
        return

    try:
        if prompt is not None and not mcp_servers:
            # Scripted one-shot, no tools: the reply is the full string, so rendering is free.
            # Raw prints only the reply to stdout (clean for piping); errors go to stderr.
            assert model is not None or remote_model_id is not None
            reply = runtime.generate(
                model, prompt, max_tokens, system_prompt, images, audios, base_url, remote_model_id
            )
            if render:
                _render_markdown(reply)
            else:
                print(reply)  # noqa: T201
            _save_transcript(save, model, remote_model_id, system_prompt, prompt, reply)
        else:
            assert model is not None or remote_model_id is not None
            reply = _chat_with_tools(
                model,
                mcp_servers,
                prompt,
                max_tokens,
                system_prompt,
                images,
                audios,
                base_url,
                render=render,
                on_confirm=_approve_all if allow_writes else None,
                confirm_policy=confirm_policy,
                target_routing=target_routing,
                target_id=resolved_target_id,
                model_id=remote_model_id,
            )
            # This branch also serves the interactive TUI (prompt is None there), which owns
            # its own transcript via /export — only the one-shot has an exchange to save.
            if prompt is not None:
                _save_transcript(save, model, remote_model_id, system_prompt, prompt, reply)
    except (RuntimeError, httpx.HTTPError) as exc:
        typer.secho(_clean_error(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _save_transcript(
    dest: Path | None,
    model: "library_ops.LibraryModel | None",
    remote_model_id: str | None,
    system_prompt: str,
    prompt: str,
    reply: str,
) -> None:
    """Write a one-shot exchange to ``dest`` as Markdown (no-op when ``--save`` was omitted).

    Uses the same renderer as the TUI's ``/export`` so a saved transcript reads identically
    whichever surface produced it. Thinking is not included: the one-shot path has no
    reasoning channel to capture it from.
    """
    if dest is None:
        return
    from stabbur import transcript  # noqa: PLC0415 - keeps the import off the interactive path

    name = model.name if model is not None else (remote_model_id or "unknown model")
    turns = [transcript.TranscriptTurn(role="system", text=system_prompt)] if system_prompt else []
    turns += [
        transcript.TranscriptTurn(role="user", text=prompt),
        transcript.TranscriptTurn(role="assistant", text=reply),
    ]
    try:
        dest.write_text(transcript.render_markdown(name, turns), encoding="utf-8")
    except OSError as exc:
        # The answer already reached stdout, so a failed save must not fail the run — say so
        # on stderr and let the pipeline continue.
        typer.secho(f"--save failed: {exc}", fg=typer.colors.RED, err=True)
        return
    typer.secho(f"saved transcript → {dest}", fg=typer.colors.BRIGHT_BLACK, err=True)


def _isatty() -> bool:
    """Whether stdout is an interactive terminal (gates ``-p`` markdown rendering)."""
    return sys.stdout.isatty()


def _render_markdown(text: str) -> None:
    """Render ``text`` as Markdown to stdout (the interactive ``-p`` path, like the TUI does)."""
    from rich.markdown import Markdown  # noqa: PLC0415

    console.print(Markdown(text.strip()))


def _chat_attached(
    base_url: str,
    model: library_ops.LibraryModel | None,
    requested: str | None,
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]],
    system_prompt: str,
    images: list[str],
    audios: list[str],
    max_tokens: int | None,
) -> None:
    """Run the interactive TUI attached to a running server (no runtime spawn, no stop on exit)."""
    endpoint = _probe_remote(base_url, model, requested)
    # Imported lazily so `-p` and the non-chat commands never pay textual's import cost.
    from stabbur import chat_tui  # noqa: PLC0415

    chat_tui.run_interactive(
        endpoint=endpoint,
        servers=mcp_servers,
        system_prompt=system_prompt,
        images=images,
        audios=audios,
        max_tokens=max_tokens,
    )


def _probe_remote(base_url: str, model: library_ops.LibraryModel | None, requested: str | None) -> "RemoteEndpoint":
    """Probe a ``--server`` URL and build the TUI's endpoint, or exit if it can't chat.

    ``stabbur serve`` answers ``GET /api/status`` (loaded model name + context window); anything
    else OpenAI-compatible answers ``GET /v1/models``. An idle unlocked serve gets a model
    auto-loaded the way the web UI does on open (see :func:`_autoload_remote`). A locally-
    resolved ``model`` supplies sampling/capability metadata — unless a stabbur serve reports a
    *different* model, in which case the local metadata is dropped (it would describe the
    wrong model).
    """
    from stabbur.chat_tui.app import RemoteEndpoint  # noqa: PLC0415 - keeps textual off the non-TUI paths

    remote_name: str | None = None
    model_id: str | None = None
    n_ctx: int | None = None
    status = _probe_json(f"{base_url}/api/status")
    if status is not None:
        # stabbur serve: the status names the loaded model. With none loaded (an unlocked serve
        # starts empty), load one the way the web UI does on open — every chat turn would 409
        # otherwise.
        served = status.get("model")
        if not isinstance(served, str) or not served:
            status = _autoload_remote(base_url, status, model, requested)
            served = status.get("model")
            assert isinstance(served, str)  # _autoload_remote only returns a ready status
        remote_name = served
        ctx = status.get("n_ctx")
        n_ctx = ctx if isinstance(ctx, int) else None
        if model is not None and served != model.name:
            console.print(
                f"[yellow]{base_url} is serving {served!r}, not {model.name!r}[/] — using the server's model."
            )
            model = None
        elif model is None and requested:
            want = requested.lower()
            if want not in (served.lower(), served.rsplit("/", 1)[-1].lower()):
                console.print(
                    f"[yellow]{base_url} is serving {served!r}, not {requested!r}[/] — using the server's model."
                )
    else:
        # Not stabbur serve — plain OpenAI discovery (llama-server, mlx-lm, LM Studio, ...).
        listed = _probe_json(f"{base_url}/v1/models")
        if listed is None:
            console.print(f"[red]Nothing answering at {base_url}[/] — is the server running?")
            raise typer.Exit(1)
        model_rows = _model_rows(listed)
        # Prefer the model the server has LOADED: a router-mode server hot-swaps on request,
        # so starting the session on the first listed id would evict the loaded one (and
        # mislabel the session) the moment the first message goes out.
        loaded_first = next((rid for rid, is_loaded in model_rows if is_loaded), None)
        model_id = loaded_first if loaded_first is not None else (model_rows[0][0] if model_rows else None)
        remote_name = model_id
        # llama-server reports the window it actually loaded in the row's ``meta.n_ctx``. Without it
        # the TUI's context gauge simply vanished on every non-stabbur server, because ``n_ctx`` was
        # read only from stabbur serve's /api/status.
        n_ctx = _row_n_ctx(listed, model_id)

    display = model.name if model is not None else (remote_name or base_url)
    return RemoteEndpoint(base=base_url, model=model, model_name=display, model_id=model_id, n_ctx=n_ctx)


def _autoload_remote(
    base_url: str, status: dict[str, object], model: library_ops.LibraryModel | None, requested: str | None
) -> dict[str, object]:
    """Load a model into an idle ``stabbur serve``, mirroring what the web UI does on open.

    An unlocked serve starts empty and the SPA auto-loads the server's default
    (``project_model``: the project's bound model, else the machine default) when opened —
    so the TUI attach does the same. An explicitly requested model wins over that default.
    Returns the ready status (model guaranteed loaded), or exits with a message.
    """
    import time  # noqa: PLC0415

    if status.get("locked"):
        # A locked serve loads its model eagerly at startup; empty means that load failed.
        err = status.get("error")
        detail = f" ({err})" if isinstance(err, str) and err else ""
        console.print(f"[red]{base_url} is locked but has no model loaded[/]{escape(detail)} — check the serve logs.")
        raise typer.Exit(1)
    default = status.get("project_model")
    target = (model.name if model is not None else requested) or (default if isinstance(default, str) else None)
    if not target:
        console.print(
            f"[red]{base_url} has no model loaded[/] and no default to load — pass a model name "
            f"([cyan]stabbur chat <model> --server …[/]), set one ([cyan]stabbur config set model <name>[/]), "
            "or load one in the serve UI."
        )
        raise typer.Exit(1)

    try:
        # Returns right after spawning the runtime; readiness is polled via /api/status below.
        # The 30s bound covers a slow spawn, not the load itself.
        httpx.post(f"{base_url}/api/load/{target}", timeout=30).raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.json() if "json" in exc.response.headers.get("content-type", "") else {}
        detail = body.get("detail", str(exc)) if isinstance(body, dict) else str(exc)
        console.print(f"[red]{base_url} could not load {target!r}[/] — {escape(str(detail))}")
        raise typer.Exit(1) from exc
    except httpx.HTTPError as exc:
        console.print(f"[red]{base_url} could not load {target!r}[/] — {escape(str(exc))}")
        raise typer.Exit(1) from exc

    timeout = status.get("runtime_load_timeout")
    deadline = time.monotonic() + (timeout if isinstance(timeout, int) and timeout > 0 else 600)
    with console.status(f"loading {target} on {base_url} … (this can take a moment)"):
        while time.monotonic() < deadline:
            polled = _probe_json(f"{base_url}/api/status")
            if polled is not None:
                if polled.get("state") == "ready" and isinstance(polled.get("model"), str):
                    console.print(f"[grey50]loaded {polled['model']} on {base_url}[/]")
                    return polled
                if polled.get("state") == "stopped":
                    err = polled.get("error")
                    detail = f" — {err}" if isinstance(err, str) and err else ""
                    console.print(f"[red]loading {target!r} on {base_url} failed[/]{escape(detail)}")
                    raise typer.Exit(1)
            time.sleep(1)
    console.print(f"[red]timed out waiting for {target!r} to load on {base_url}[/]")
    raise typer.Exit(1)


def _probe_json(url: str) -> dict[str, object] | None:
    """GET ``url`` and return its JSON object, or ``None`` on any failure (probing, not fetching)."""
    try:
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _model_rows(listed: dict[str, object]) -> list[tuple[str, bool]]:
    """Parse a ``GET /v1/models`` body into ``(id, loaded)`` rows.

    ``loaded`` comes from llama-server router mode's per-model ``status``; servers that
    don't report one (LM Studio, mlx-lm, a plain llama-server) just read as not-loaded.
    """
    rows = listed.get("data")
    out: list[tuple[str, bool]] = []
    if isinstance(rows, list):
        for row in rows:
            rid = row.get("id") if isinstance(row, dict) else None
            if isinstance(rid, str):
                status = row.get("status") if isinstance(row, dict) else None
                out.append((rid, isinstance(status, dict) and status.get("value") == "loaded"))
    return out


def _row_n_ctx(listed: dict[str, object], model_id: str | None) -> int | None:
    """The loaded context window a ``GET /v1/models`` row reports for ``model_id``, if any.

    llama-server puts the window it loaded with in each row's ``meta.n_ctx`` (``n_ctx_train`` next
    to it is the model's maximum, not the running window — reading that would overstate the gauge).
    Servers that report neither just leave the footer without a context reading, as before.
    """
    rows = listed.get("data")
    if model_id is None or not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("id") == model_id:
            meta = row.get("meta")
            n_ctx = meta.get("n_ctx") if isinstance(meta, dict) else None
            return n_ctx if isinstance(n_ctx, int) and n_ctx > 0 else None
    return None


def _remote_model_id(base_url: str, requested: str | None) -> str:
    """Pick the model id to send to a remote ``/v1`` whose model isn't in the library.

    Probes ``GET /v1/models`` (llama-server — router mode included — LM Studio, mlx-lm, and
    stabbur serve all answer it). With ``requested``, matches it against the listed ids: exact,
    case-insensitive, then by basename (so a short router alias finds its full id and vice
    versa). With no name, the model the server currently has LOADED wins (a router hot-swaps
    on request, so defaulting to the first listed id would silently evict whatever the user
    had running); with nothing loaded, the first listed id (single-model servers list one).
    Exits with the available ids when nothing matches — clearer than the server's own 400.
    """
    listed = _probe_json(f"{base_url}/v1/models")
    model_rows = _model_rows(listed) if listed is not None else []
    ids = [rid for rid, _ in model_rows]
    loaded = [rid for rid, is_loaded in model_rows if is_loaded]
    if not ids:
        console.print(f"[red]{base_url} lists no models[/] — is the server running?")
        raise typer.Exit(1)
    if requested is None:
        return loaded[0] if loaded else ids[0]
    want = requested.lower()
    want_base = want.rsplit("/", 1)[-1]
    for cand in ids:
        if cand.lower() == want or cand.lower().rsplit("/", 1)[-1] == want_base:
            return cand
    console.print(f"[red]{base_url} does not serve {requested!r}[/] — available: {', '.join(ids)}")
    raise typer.Exit(1)


def _chat_with_tools(
    model: library_ops.LibraryModel | None,
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]],
    prompt: str | None,
    max_tokens: int | None,
    system_prompt: str = "",
    images: list[str] | None = None,
    audios: list[str] | None = None,
    base_url: str | None = None,
    render: bool = False,
    on_confirm: "agent.ConfirmSink | None" = None,
    confirm_policy: Literal["all", "writes", "none"] = "none",
    target_routing: "mcp_tools.TargetRouting | None" = None,
    target_id: str | None = None,
    model_id: str | None = None,
) -> str:
    """Serve the model, then chat: the Textual TUI interactively, or ``-p`` scripted.

    With ``prompt`` set (``-p``) this streams a single answer to stdout (tools still
    run); otherwise it hands off to the full-screen Textual chat. With ``base_url`` set
    (a running ``stabbur serve``), the one-shot path attaches to that server — reusing its
    loaded model instead of spawning a runtime. With ``render`` set (interactive ``-p``),
    the answer is buffered and printed as rendered markdown instead of streamed raw.

    ``target_routing`` + ``target_id`` (a multi-target ``--target``) narrow the connected toolset to that
    target's servers plus shared ones. This only reaches the scripted ``-p`` path — ``--target`` is refused
    for the interactive Textual TUI (it connects its own toolset), so that path always uses the full set.
    """
    import asyncio  # noqa: PLC0415

    from rich.progress import Progress  # noqa: PLC0415

    from stabbur import (
        agent,  # noqa: PLC0415
        capabilities,  # noqa: PLC0415
        chatui,  # noqa: PLC0415
    )
    from stabbur import tools as mcp_tools  # noqa: PLC0415
    from stabbur.runtime import sampling  # noqa: PLC0415

    servers = mcp_servers  # already (name, argv, env) specs
    # Model-recommended sampling (incl. the anti-loop repeat_penalty default), applied
    # to every CLI chat turn just like the web path does. A remote-only model (``model_id``
    # with no library copy) has no local metadata: keep the anti-loop default and treat it
    # as vision-capable, the same way the interactive remote attach loads media unwarned.
    if model is not None:
        rec = sampling.recommended(model)
        model_vision = capabilities.capabilities(model).vision  # feed tool-returned images back only if seen
    else:
        rec = sampling.defaults()
        model_vision = True
    if model_id is None:
        assert model is not None  # every caller supplies a library model or a remote id
        model_id = str(model.load_target)
    # Tool activity is meta → stderr, so `-p` stdout stays just the answer.
    err = Console(stderr=True)

    # Per-turn state: a "thinking" spinner shown until the first token/tool-call
    # arrives (model prefill latency otherwise looks dead), and whether the reply
    # label has been printed yet.
    turn_status: Progress | None = None
    turn_labeled = True
    # Reasoning (thinking) streams dim to stderr with no trailing newline; the
    # answer streams to stdout with no leading newline. Without a separator the
    # answer glues onto the last thinking line ("…structure.```json"). Track when
    # thinking happened so the first answer token starts on a fresh line.
    turn_reasoned = False
    turn_separated = True
    # When rendering (interactive -p to a TTY), the answer is buffered here and rendered as
    # markdown once complete instead of streamed token-by-token (markdown needs the full text).
    answer_parts: list[str] = []

    def _first_output() -> None:
        nonlocal turn_status, turn_labeled
        if turn_status is not None:
            turn_status.stop()
            turn_status = None
        if not turn_labeled:
            chatui.assistant_prefix(err, inline=False)
            turn_labeled = True

    def _think() -> None:
        nonlocal turn_status, turn_reasoned, turn_separated
        turn_reasoned = False
        turn_separated = True
        turn_status = chatui.thinking(err)
        turn_status.start()

    def _separate() -> None:
        # Break the reasoning block off the answer with a newline, once per turn.
        nonlocal turn_separated
        if turn_reasoned and not turn_separated:
            err.print()
            turn_separated = True

    def on_event(kind: str, detail: str) -> None:
        _first_output()
        _separate()  # break a preceding thinking block off the tool line
        icon = "[cyan]⚙[/]" if kind == "call" else "[grey62]↳[/]"
        err.print(f"  {icon} [grey62]{detail[:200]}[/]")

    def on_token(text: str) -> None:
        _first_output()
        _separate()
        # Always accumulate: --save needs the finished answer even while it streams to stdout.
        answer_parts.append(text)
        if not render:
            print(text, end="", flush=True)  # noqa: T201

    def on_reasoning(text: str) -> None:
        # Reasoning models' thinking → dim on stderr (keeps -p stdout the answer only).
        nonlocal turn_reasoned, turn_separated
        _first_output()
        turn_reasoned = True
        turn_separated = False
        err.print(text, end="", style="grey42")

    def seed() -> list[dict[str, object]]:
        return [{"role": "system", "content": system_prompt}] if system_prompt else []

    async def _run_oneshot(base: str) -> None:
        """The scripted ``-p`` path (with tools): stream the answer to stdout, no UI."""
        nonlocal turn_labeled
        assert prompt is not None  # only entered when -p supplied a prompt
        async with mcp_tools.connect(servers) as toolset:
            # connect() records a per-server failure instead of raising, and the one-shot path used to
            # drop those on the floor: a bad --mcp meant no tools, no message, and exit 0 — the model
            # simply answered without them. Meta, so stderr (stdout stays exactly the answer), one line
            # per server, matching what the TUI posts in its transcript and `stabbur mcp tools` prints.
            for label, why in toolset.errors:
                err.print(f"[yellow]MCP server {label!r} did not start:[/] [grey62]{escape(why)}[/]")
            if target_routing is not None and target_id is not None:
                toolset = mcp_tools.narrow_to_servers(toolset, target_routing, target_id)
            turn_labeled = True  # -p mode: stdout is just the answer, no label
            _think()
            await agent.run(
                base,
                [*seed(), {"role": "user", "content": agent.user_content(prompt, images, audios)}],
                toolset,
                max_tokens,
                on_event,
                on_token,
                on_reasoning=on_reasoning,
                temperature=rec.temperature,
                top_p=rec.top_p,
                top_k=rec.top_k,
                min_p=rec.min_p,
                repeat_penalty=rec.repeat_penalty,
                model=model_id,  # required by mlx-vlm and remote routers; ignored by llama-server/mlx-lm
                vision=model_vision,
                on_confirm=on_confirm,
                confirm_policy=confirm_policy,
            )
            _first_output()
            if render:
                _render_markdown("".join(answer_parts))
            else:
                print()  # noqa: T201 - newline after streamed answer

    # Attach to a running stabbur serve for the one-shot path: reuse its loaded model, no spawn/stop.
    if base_url is not None and prompt is not None:
        asyncio.run(_run_oneshot(base_url))
        return "".join(answer_parts)

    assert model is not None  # a model-id-only chat is always a remote one-shot, handled above
    rt = runtime.load(model)  # start the runtime (with a load spinner); caller/TUI owns stop()
    if prompt is not None:
        try:
            asyncio.run(_run_oneshot(rt.base))
        finally:
            runtime.stop(rt)
        return "".join(answer_parts)
    # Interactive: hand the runtime to the full-screen Textual chat (imported lazily so
    # `stabbur library ls` and friends don't pay textual's import cost). The TUI owns the runtime
    # from here — it can switch models — and stops it on exit.
    from stabbur import chat_tui  # noqa: PLC0415

    chat_tui.run_interactive(
        endpoint=rt,
        servers=servers,
        system_prompt=system_prompt,
        images=images or [],
        audios=audios or [],
        max_tokens=max_tokens,
    )
    return ""  # interactive session: the TUI owns its own transcript (/export)
