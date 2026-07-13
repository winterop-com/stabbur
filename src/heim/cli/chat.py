"""`heim chat` - the terminal chat: interactive TUI, one-shot -p, tools, and serve-attach."""

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import httpx
import typer
from rich.console import Console

from heim import (
    capabilities,
    mcpservers,
    project,
    runtime,
)
from heim import library as library_ops
from heim.cli._app import app
from heim.cli._common import (
    FormatOption,
    _cli_mcp_spec,
    _load_media,
    _maybe_library_model,
    _normalize_server_url,
    _resolve_library_model,
    console,
)
from heim.config import get_settings

if TYPE_CHECKING:
    from heim import agent
    from heim.chat_tui.app import RemoteEndpoint
    from heim.project import AssistantInfo


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


def _target_servers(proj: "project.Project | None", resolved_servers: "list[Any]") -> dict[str, set[str]]:
    """Map each registry target id to the server names it owns (``mcp_servers=[]`` → all resolved servers).

    The no-browser twin of ``app.state.target_servers`` (built in the serve lifespan): the routing table
    :func:`heim.tools.narrow_to_servers` consumes to keep a target's turn to its own servers plus shared.
    """
    if proj is None or not proj.registry.targets:
        return {}
    all_names = {s.name for s in resolved_servers}
    return {
        tid: (set(t.mcp_servers) if t.mcp_servers else set(all_names))
        for tid, t in zip(proj.registry.ids, proj.registry.targets, strict=True)
    }


async def _approve_all(name: str, args: dict[str, Any]) -> bool:
    """A confirmation sink that approves every gated call (the ``--allow-writes`` opt-out)."""
    return True


@app.command()
def chat(
    name: Annotated[
        str | None,
        typer.Argument(help="Library model (defaults to the project's model in heim.toml)."),
    ] = None,
    prompt: Annotated[
        str | None,
        typer.Option("-p", "--prompt", help="One-shot prompt, prints just the answer (Claude-style -p)."),
    ] = None,
    model_format: FormatOption = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", "-n", help="Cap generated tokens.")] = None,
    mcp: Annotated[
        list[str],
        typer.Option("--mcp", help="MCP server command(s) for tools; repeatable, e.g. --mcp heim-mcp-datetime."),
    ] = [],
    tools: Annotated[
        bool,
        typer.Option("--tools/--no-tools", help="Attach MCP tools. Use --no-tools for non-tool-trained models."),
    ] = True,
    system: Annotated[
        str | None,
        typer.Option("--system", help="System prompt for this session (overrides heim.toml)."),
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
            help="Attach to a running `heim serve` (e.g. http://127.0.0.1:8000) instead of loading a "
            "model locally — the interactive TUI and one-shot -p both. Default from HEIM_CHAT_SERVER / "
            "`heim config set server`.",
        ),
    ] = None,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="With -p, never render markdown; print raw text even to a terminal."),
    ] = False,
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
    ``heim.toml`` supplies the default model, its MCP tool servers, and a system
    prompt; ``--mcp`` flags add to (not replace) those, ``--system`` overrides the
    prompt, and ``--no-tools`` drops tools entirely (some models regurgitate the
    tool schema instead of calling it).
    """
    proj = project.load()
    model_name = project.resolve_model(name, proj)
    # Resolve the registry target this session routes to (--target, else the primary): its readonly flag
    # drives the confirm policy and its server set narrows the tools (below). A write-enabled target
    # gates non-read-only tool calls; in this non-interactive one-shot there is no human to confirm, so a
    # gated call is DENIED unless --allow-writes supplies a blanket auto-approve sink (with no project /
    # no assistant / a read-only target the policy is "none" — nothing is gated, identical to today).
    resolved_target, resolved_target_id = _resolve_target(proj, target)
    confirm_policy = _confirm_policy(resolved_target)
    # Attach to a running server (--server > HEIM_CHAT_SERVER / machine config) instead of
    # spawning a runtime — the interactive TUI and one-shot -p both. For the interactive
    # attach the model may exist only on the server, so local resolution is best-effort
    # there; every other path still requires (and strictly resolves) a library model.
    base_url = _normalize_server_url(server or get_settings().chat_server)
    interactive_remote = base_url is not None and prompt is None
    model: library_ops.LibraryModel | None
    if interactive_remote:
        # Only an explicitly typed name supplies local metadata: the server decides what runs,
        # so letting the machine/project *default* model label the session would mislead.
        model = _maybe_library_model(name, model_format) if name is not None else None
    else:
        if model_name is None:
            console.print(
                "[red]No model given.[/] Pass a model name (see [cyan]heim library ls[/]), "
                "set a machine default ([cyan]heim config set model <name>[/]), "
                "or define one in a project ([cyan]heim project init[/])."
            )
            raise typer.Exit(1)
        model = _resolve_library_model(model_name, model_format)

    # (name, argv, env) per server. A bare --mcp value resolves against advertised servers
    # (so `--mcp datetime` finds heim-mcp-datetime), else it's used verbatim as a command;
    # the rest come from the resolved mcp.json layers (global + project, see heim.mcpservers).
    resolved_servers = mcpservers.resolve() if tools else []
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]] = (
        [_cli_mcp_spec(c) for c in mcp] + [s.to_spec() for s in resolved_servers] if tools else []
    )
    # Per-target routing table (id -> owned server names); --target narrows the connected toolset to its
    # servers + shared ones. Empty outside a multi-target project → no narrowing (full toolset).
    target_servers = _target_servers(proj, resolved_servers)
    system_prompt = system if system is not None else (proj.system_prompt if proj else "")
    # Capabilities are unknowable without a local copy (remote attach): load media unwarned.
    caps = capabilities.capabilities(model) if model is not None else None
    images = _load_media(image, model, kind="vision", default_mime="image/png", capable=caps.vision if caps else True)
    audios = _load_media(audio, model, kind="audio", default_mime="audio/wav", capable=caps.audio if caps else True)

    # With nothing configured, auto-attach to a running `heim serve` locked to this model (one-shot
    # only): reuse its resident weights instead of reloading. A stderr note keeps it non-surprising.
    # The interactive TUI does NOT auto-attach — silently disabling /model would surprise.
    if base_url is None and prompt is not None:
        from heim.runtime import serve_registry  # noqa: PLC0415

        assert model is not None  # every non-interactive-remote path resolved strictly above
        found = serve_registry.discover(model.name)
        if found is not None:
            base_url = found.base_url
            typer.secho(f"↳ attaching to running heim serve at {base_url}", fg=typer.colors.BRIGHT_BLACK, err=True)

    # Render the reply as markdown only when -p writes to a real terminal (the git/bat/ls
    # convention): an interactive `heim chat -p "…"` gets tables/headings/code like the TUI,
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
            assert model is not None
            reply = runtime.generate(model, prompt, max_tokens, system_prompt, images, audios, base_url)
            if render:
                _render_markdown(reply)
            else:
                print(reply)  # noqa: T201
        else:
            assert model is not None
            _chat_with_tools(
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
                target_servers=target_servers,
                target_id=resolved_target_id,
            )
    except (RuntimeError, httpx.HTTPError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


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
    from heim import chat_tui  # noqa: PLC0415

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

    ``heim serve`` answers ``GET /api/status`` (loaded model name + context window); anything
    else OpenAI-compatible answers ``GET /v1/models``. An idle unlocked serve gets a model
    auto-loaded the way the web UI does on open (see :func:`_autoload_remote`). A locally-
    resolved ``model`` supplies sampling/capability metadata — unless a heim serve reports a
    *different* model, in which case the local metadata is dropped (it would describe the
    wrong model).
    """
    from heim.chat_tui.app import RemoteEndpoint  # noqa: PLC0415 - keeps textual off the non-TUI paths

    remote_name: str | None = None
    model_id: str | None = None
    n_ctx: int | None = None
    status = _probe_json(f"{base_url}/api/status")
    if status is not None:
        # heim serve: the status names the loaded model. With none loaded (an unlocked serve
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
        # Not heim serve — plain OpenAI discovery (llama-server, mlx-lm, LM Studio, ...).
        listed = _probe_json(f"{base_url}/v1/models")
        if listed is None:
            console.print(f"[red]Nothing answering at {base_url}[/] — is the server running?")
            raise typer.Exit(1)
        rows = listed.get("data")
        first = rows[0] if isinstance(rows, list) and rows else None
        got = first.get("id") if isinstance(first, dict) else None
        model_id = got if isinstance(got, str) and got else None
        remote_name = model_id

    display = model.name if model is not None else (remote_name or base_url)
    return RemoteEndpoint(base=base_url, model=model, model_name=display, model_id=model_id, n_ctx=n_ctx)


def _autoload_remote(
    base_url: str, status: dict[str, object], model: library_ops.LibraryModel | None, requested: str | None
) -> dict[str, object]:
    """Load a model into an idle ``heim serve``, mirroring what the web UI does on open.

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
        console.print(f"[red]{base_url} is locked but has no model loaded[/]{detail} — check the serve logs.")
        raise typer.Exit(1)
    default = status.get("project_model")
    target = (model.name if model is not None else requested) or (default if isinstance(default, str) else None)
    if not target:
        console.print(
            f"[red]{base_url} has no model loaded[/] and no default to load — pass a model name "
            f"([cyan]heim chat <model> --server …[/]), set one ([cyan]heim config set model <name>[/]), "
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
        console.print(f"[red]{base_url} could not load {target!r}[/] — {detail}")
        raise typer.Exit(1) from exc
    except httpx.HTTPError as exc:
        console.print(f"[red]{base_url} could not load {target!r}[/] — {exc}")
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
                    console.print(f"[red]loading {target!r} on {base_url} failed[/]{detail}")
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


def _chat_with_tools(
    model: library_ops.LibraryModel,
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
    target_servers: Mapping[str, set[str]] | None = None,
    target_id: str | None = None,
) -> None:
    """Serve the model, then chat: the Textual TUI interactively, or ``-p`` scripted.

    With ``prompt`` set (``-p``) this streams a single answer to stdout (tools still
    run); otherwise it hands off to the full-screen Textual chat. With ``base_url`` set
    (a running ``heim serve``), the one-shot path attaches to that server — reusing its
    loaded model instead of spawning a runtime. With ``render`` set (interactive ``-p``),
    the answer is buffered and printed as rendered markdown instead of streamed raw.

    ``target_servers`` + ``target_id`` (a multi-target ``--target``) narrow the connected toolset to that
    target's servers plus shared ones, but only on the scripted ``-p`` path — the interactive Textual TUI
    connects its own toolset and stays on the full (primary) set for now.
    """
    import asyncio  # noqa: PLC0415

    from rich.progress import Progress  # noqa: PLC0415

    from heim import (
        agent,  # noqa: PLC0415
        capabilities,  # noqa: PLC0415
        chatui,  # noqa: PLC0415
    )
    from heim import tools as mcp_tools  # noqa: PLC0415
    from heim.runtime import sampling  # noqa: PLC0415

    servers = mcp_servers  # already (name, argv, env) specs
    # Model-recommended sampling (incl. the anti-loop repeat_penalty default), applied
    # to every CLI chat turn just like the web path does.
    rec = sampling.recommended(model)
    model_vision = capabilities.capabilities(model).vision  # feed tool-returned images back only if seen
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
        if render:
            answer_parts.append(text)  # buffered; rendered as markdown once the reply completes
        else:
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
            if target_servers and target_id is not None:
                toolset = mcp_tools.narrow_to_servers(toolset, target_servers, target_id)
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
                model=str(model.load_target),  # required by mlx-vlm; ignored by llama-server/mlx-lm
                vision=model_vision,
                on_confirm=on_confirm,
                confirm_policy=confirm_policy,
            )
            _first_output()
            if render:
                _render_markdown("".join(answer_parts))
            else:
                print()  # noqa: T201 - newline after streamed answer

    # Attach to a running heim serve for the one-shot path: reuse its loaded model, no spawn/stop.
    if base_url is not None and prompt is not None:
        asyncio.run(_run_oneshot(base_url))
        return

    rt = runtime.load(model)  # start the runtime (with a load spinner); caller/TUI owns stop()
    if prompt is not None:
        try:
            asyncio.run(_run_oneshot(rt.base))
        finally:
            runtime.stop(rt)
        return
    # Interactive: hand the runtime to the full-screen Textual chat (imported lazily so
    # `heim library ls` and friends don't pay textual's import cost). The TUI owns the runtime
    # from here — it can switch models — and stops it on exit.
    from heim import chat_tui  # noqa: PLC0415

    chat_tui.run_interactive(
        endpoint=rt,
        servers=servers,
        system_prompt=system_prompt,
        images=images or [],
        audios=audios or [],
        max_tokens=max_tokens,
    )
