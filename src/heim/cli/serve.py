"""`heim serve` - run the web API + browser UI, optionally locked to one model."""

import os
import secrets
from typing import Annotated

import typer

from heim import (
    config,
    project,
    runtime,
)
from heim import library as library_ops
from heim.cli._app import app
from heim.cli._common import (
    _normalize_server_url,
    _resolve_library_model,
    console,
)
from heim.config import get_settings


def _export_serve_env(
    *, ui: bool, model: str | None, runtime_port: int | None, debug: bool, upstream: str | None = None
) -> None:
    """The one place ``serve`` hands its config to the app — the deliberate env-as-API (A8).

    With ``--reload``, uvicorn imports the app in a *fresh subprocess*, where the CLI callback's
    in-process overrides (``--runtime-port``, ``--debug``) and the resolved locked model don't
    exist — so these ``HEIM_*`` env vars are that subprocess's config channel. Centralized here so
    it's a single documented surface instead of scattered ``os.environ`` writes; without
    ``--reload`` it's harmless (the same process already has the overrides). The auth token is
    exported alongside these in ``serve`` once it's known.
    """
    if ui:
        os.environ["HEIM_SERVE_UI"] = "true"
    if model is not None:
        os.environ["HEIM_SERVE_MODEL"] = model
    if runtime_port is not None:
        os.environ["HEIM_RUNTIME_PORT"] = str(runtime_port)
    if debug:
        os.environ["HEIM_DEBUG"] = "true"
    if upstream is not None:
        os.environ["HEIM_UPSTREAM"] = upstream


@app.command()
def serve(
    ui: Annotated[bool, typer.Option("--ui", help="Also serve the browser UI (single-page app).")] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Lock the server to one model (extension backend); no switching."),
    ] = None,
    upstream: Annotated[
        str | None,
        typer.Option(
            "--upstream",
            help="Front a remote OpenAI-compatible /v1 (e.g. a llama-server router: http://msai:1234) "
            "instead of spawning local runtimes — the agent loop, tools, and UI run here; the models "
            "run there.",
        ),
    ] = None,
    host: Annotated[str | None, typer.Option("--host", help="Bind address (default 127.0.0.1).")] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Web server port (default: auto-pick a free port; pin for a stable URL)."),
    ] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes.")] = False,
) -> None:
    """Run the web server (browse API, plus the browser UI with --ui).

    With --model the server is locked to a single model (the Chrome-extension
    backend); otherwise the UI can switch models freely. The port defaults to a
    free auto-picked one (printed below); pass --port to pin a stable URL.
    """
    import os  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    upstream_url = _normalize_server_url(upstream)
    if upstream_url is None:
        library_ops.roots()  # fail fast + clean if no library is configured (rather than 500ing per request)

    # A project is a locked, purpose-built assistant: it binds the server to its model
    # (no picker, like --model), so `heim serve` in a project == serve --model <project.model>.
    # An explicit --model overrides. No project (or a project without a model) => free-play.
    proj = project.load()
    locked_model = model or (proj.model if proj else None)
    locked_by_project = model is None and locked_model is not None
    if locked_model is not None:
        # Validate the locked model up front (like `heim chat`) so a bad name gives a clean message
        # here, not a uvicorn "Application startup failed" traceback from the app lifespan. In
        # upstream mode the name must match one of the REMOTE's ids, not a library model.
        if upstream_url is not None:
            from heim.server import UpstreamManager  # noqa: PLC0415

            try:
                UpstreamManager(upstream_url).load_by_name(locked_model)
            except RuntimeError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(1) from exc
        else:
            _resolve_library_model(locked_model, None)
    # Hand the serve config to the (possibly reloaded) worker process — one documented env API.
    _export_serve_env(
        ui=ui,
        model=locked_model,
        runtime_port=config.runtime_port_override(),
        debug=config.debug_enabled(),
        upstream=upstream_url,
    )

    get_settings.cache_clear()
    settings = get_settings()
    # Precedence: --host/--port > HEIM_HOST/HEIM_PORT/heim.toml > auto-pick a free port.
    bind_host = host or settings.host
    bind_port = port or settings.port or runtime.find_free_port()
    base = f"http://{bind_host}:{bind_port}"

    # Binding a non-loopback address exposes model control + MCP tool execution (arbitrary code
    # via heim-mcp-exec) to the LAN. Never leave that unauthenticated: auto-generate a bearer
    # token if one isn't configured, so a client must present it (V-14). An explicitly-set
    # HEIM_AUTH_TOKEN is honored as-is (and enforced even on loopback, for deliberate opt-in).
    loopback = bind_host in ("127.0.0.1", "localhost", "::1", "")
    auth_token = settings.auth_token
    if not loopback and not auth_token:
        auth_token = secrets.token_urlsafe(24)
        os.environ["HEIM_AUTH_TOKEN"] = auth_token  # part of the serve→worker env API (_export_serve_env)
        get_settings.cache_clear()

    console.print("\n[bold]heim[/]")
    if not loopback:
        console.print(
            f"  [yellow]Exposed on {bind_host}[/] — anyone who can reach this host can control models and run tools."
        )
    if auth_token:
        console.print("  Auth:     [bold]bearer token required[/] [dim](Authorization: Bearer <token>)[/]")
    if upstream_url is not None:
        console.print(f"  Upstream: [bold]{upstream_url}[/] [dim]· remote models, no local runtimes[/]")
    if locked_model is not None:
        console.print(f"  Locked:   [bold]{locked_model}[/] [dim]· {'project' if locked_by_project else '--model'}[/]")
    # A tokenized URL lets the user just open the SPA: it captures ?token= into the browser and
    # sends it as a bearer header thereafter (like Jupyter). Non-browser clients send the header.
    ui_url = f"{base}/?token={auth_token}" if auth_token else base
    if ui:
        if settings.frontend_dir.is_dir():
            console.print(f"  UI:       [link={ui_url}]{ui_url}[/]")
        else:
            console.print(f"  [yellow]UI not built[/] — expected at [dim]{settings.frontend_dir}[/]; serving API only")
    console.print(f"  API:      [link={base}]{base}[/]")
    console.print(f"  Docs:     [link={base}/docs]{base}/docs[/]")
    if auth_token:
        console.print(f"  [dim]Token:[/]    [dim]{auth_token}[/]")
    console.print("  [dim]Ctrl-C to stop[/]\n")

    # Advertise this serve so `heim chat` (no --server) can attach to its loaded model instead of
    # reloading. Only unauthenticated (loopback, no-token) serves — chat can't send a token yet.
    from heim.runtime import serve_registry  # noqa: PLC0415

    if not auth_token and locked_model is not None:
        serve_registry.register(base, locked_model)
    try:
        uvicorn.run("heim.app:app", host=bind_host, port=bind_port, reload=reload)
    finally:
        serve_registry.unregister()


# --- voice -----------------------------------------------------------------
