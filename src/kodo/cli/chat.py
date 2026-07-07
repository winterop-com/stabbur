"""`kodo chat` - the terminal chat: interactive TUI, one-shot -p, tools, and serve-attach."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import httpx
import typer
from rich.console import Console

from kodo import (
    capabilities,
    mcpservers,
    project,
    runtime,
)
from kodo import library as library_ops
from kodo.config import get_settings

if TYPE_CHECKING:
    pass

from kodo.cli._app import app
from kodo.cli._common import (
    FormatOption,
    _cli_mcp_spec,
    _load_media,
    _normalize_server_url,
    _resolve_library_model,
    console,
)


@app.command()
def chat(
    name: Annotated[
        str | None,
        typer.Argument(help="Library model (defaults to the project's model in kodo.toml)."),
    ] = None,
    prompt: Annotated[
        str | None,
        typer.Option("-p", "--prompt", help="One-shot prompt, prints just the answer (Claude-style -p)."),
    ] = None,
    model_format: FormatOption = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", "-n", help="Cap generated tokens.")] = None,
    mcp: Annotated[
        list[str],
        typer.Option("--mcp", help="MCP server command(s) for tools; repeatable, e.g. --mcp kodo-mcp-datetime."),
    ] = [],
    tools: Annotated[
        bool,
        typer.Option("--tools/--no-tools", help="Attach MCP tools. Use --no-tools for non-tool-trained models."),
    ] = True,
    system: Annotated[
        str | None,
        typer.Option("--system", help="System prompt for this session (overrides kodo.toml)."),
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
            help="Attach to a running `kodo serve` (e.g. http://127.0.0.1:8000) instead of loading the "
            "model per call; the model stays loaded. Default from KODO_CHAT_SERVER / `kodo config set server`.",
        ),
    ] = None,
) -> None:
    """Chat with a library model: full-screen TUI, one-shot with ``-p``, tools with ``--mcp``.

    Interactive chat opens a Textual app (markdown replies, live tool activity, a
    context footer); ``-p`` prints just the answer for scripting. In a project dir,
    ``kodo.toml`` supplies the default model, its MCP tool servers, and a system
    prompt; ``--mcp`` flags add to (not replace) those, ``--system`` overrides the
    prompt, and ``--no-tools`` drops tools entirely (some models regurgitate the
    tool schema instead of calling it).
    """
    proj = project.load()
    model_name = project.resolve_model(name, proj)
    if model_name is None:
        console.print(
            "[red]No model given.[/] Pass a model name (see [cyan]kodo library ls[/]), "
            "set a machine default ([cyan]kodo config set model <name>[/]), "
            "or define one in a project ([cyan]kodo project init[/])."
        )
        raise typer.Exit(1)
    model = _resolve_library_model(model_name, model_format)

    # (name, argv, env) per server. A bare --mcp value resolves against advertised servers
    # (so `--mcp datetime` finds kodo-mcp-datetime), else it's used verbatim as a command;
    # the rest come from the resolved mcp.json layers (global + project, see kodo.mcpservers).
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]] = (
        [_cli_mcp_spec(c) for c in mcp] + [s.to_spec() for s in mcpservers.resolve()] if tools else []
    )
    system_prompt = system if system is not None else (proj.system_prompt if proj else "")
    caps = capabilities.capabilities(model)
    images = _load_media(image, model, kind="vision", default_mime="image/png", capable=caps.vision)
    audios = _load_media(audio, model, kind="audio", default_mime="audio/wav", capable=caps.audio)

    # Attach to a running kodo serve (--server > KODO_CHAT_SERVER / machine config) instead of
    # spawning a per-call runtime. Supported for the one-shot (-p) path; the interactive TUI owns
    # its own runtime (and can switch models), so --server doesn't apply there yet.
    base_url = _normalize_server_url(server or get_settings().chat_server)
    if base_url is not None and prompt is None:
        console.print(
            f"[yellow]--server[/] applies to one-shot (-p) only; the TUI loads its own model. Ignoring {base_url}."
        )
        base_url = None
    # With nothing configured, auto-attach to a running `kodo serve` locked to this model (one-shot
    # only): reuse its resident weights instead of reloading. A stderr note keeps it non-surprising.
    if base_url is None and prompt is not None:
        from kodo.runtime import serve_registry  # noqa: PLC0415

        found = serve_registry.discover(model.name)
        if found is not None:
            base_url = found.base_url
            typer.secho(f"↳ attaching to running kodo serve at {base_url}", fg=typer.colors.BRIGHT_BLACK, err=True)

    try:
        if prompt is not None and not mcp_servers:
            # Scripted one-shot, no tools: print only the reply to stdout (clean for
            # piping); errors go to stderr. Everything else goes through the one
            # interactive/agent path below (tools optional, empty list = plain chat).
            print(runtime.generate(model, prompt, max_tokens, system_prompt, images, audios, base_url))  # noqa: T201
        else:
            _chat_with_tools(model, mcp_servers, prompt, max_tokens, system_prompt, images, audios, base_url)
    except (RuntimeError, httpx.HTTPError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _chat_with_tools(
    model: library_ops.LibraryModel,
    mcp_servers: list[tuple[str | None, list[str], dict[str, str]]],
    prompt: str | None,
    max_tokens: int | None,
    system_prompt: str = "",
    images: list[str] | None = None,
    audios: list[str] | None = None,
    base_url: str | None = None,
) -> None:
    """Serve the model, then chat: the Textual TUI interactively, or ``-p`` scripted.

    With ``prompt`` set (``-p``) this streams a single answer to stdout (tools still
    run); otherwise it hands off to the full-screen Textual chat. With ``base_url`` set
    (a running ``kodo serve``), the one-shot path attaches to that server — reusing its
    loaded model instead of spawning a runtime.
    """
    import asyncio  # noqa: PLC0415

    from rich.progress import Progress  # noqa: PLC0415

    from kodo import (
        agent,  # noqa: PLC0415
        chatui,  # noqa: PLC0415
    )
    from kodo import tools as mcp_tools  # noqa: PLC0415
    from kodo.runtime import sampling  # noqa: PLC0415

    servers = mcp_servers  # already (name, argv, env) specs
    # Model-recommended sampling (incl. the anti-loop repeat_penalty default), applied
    # to every CLI chat turn just like the web path does.
    rec = sampling.recommended(model)
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
            )
            _first_output()
            print()  # noqa: T201 - newline after streamed answer

    # Attach to a running kodo serve for the one-shot path: reuse its loaded model, no spawn/stop.
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
    # `kodo library ls` and friends don't pay textual's import cost). The TUI owns the runtime
    # from here — it can switch models — and stops it on exit.
    from kodo import chat_tui  # noqa: PLC0415

    chat_tui.run_interactive(
        runtime_proc=rt,
        servers=servers,
        system_prompt=system_prompt,
        images=images or [],
        audios=audios or [],
        max_tokens=max_tokens,
    )
