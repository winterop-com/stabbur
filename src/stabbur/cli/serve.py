"""`stabbur serve` - run the web API + browser UI, optionally locked to one model."""

import json
import os
import secrets
from typing import TYPE_CHECKING, Annotated

import typer
from rich.markup import escape

from stabbur import (
    config,
    project,
)
from stabbur import library as library_ops
from stabbur.cli._app import app
from stabbur.cli._common import (
    _normalize_server_url,
    _resolve_library_model,
    console,
)
from stabbur.config import get_settings

if TYPE_CHECKING:  # stabbur.backends pulls in the server + library; keep it off the CLI's import path
    from stabbur.backends import BackendSpec


def _export_serve_env(
    *,
    ui: bool,
    model: str | None,
    runtime_port: int | None,
    debug: bool,
    upstream: str | None = None,
    backends: "list[BackendSpec] | None" = None,
) -> None:
    """The one place ``serve`` hands its config to the app — the deliberate env-as-API (A8).

    With ``--reload``, uvicorn imports the app in a *fresh subprocess*, where the CLI callback's
    in-process overrides (``--runtime-port``, ``--debug``) and the resolved locked model don't
    exist — so these ``STABBUR_*`` env vars are that subprocess's config channel. Centralized here so
    it's a single documented surface instead of scattered ``os.environ`` writes; without
    ``--reload`` it's harmless (the same process already has the overrides). The auth token is
    exported alongside these in ``serve`` once it's known.
    """
    if ui:
        os.environ["STABBUR_SERVE_UI"] = "true"
    if model is not None:
        os.environ["STABBUR_SERVE_MODEL"] = model
    if runtime_port is not None:
        os.environ["STABBUR_RUNTIME_PORT"] = str(runtime_port)
    if debug:
        os.environ["STABBUR_DEBUG"] = "true"
    if upstream is not None:
        os.environ["STABBUR_UPSTREAM"] = upstream
    if backends is not None:
        # The already-resolved list, not just the flags: STABBUR_BACKENDS outranks the project's
        # own [[backends]] (env beats file), so exporting only what --upstream added would *hide*
        # the declared ones from the reloaded worker.
        os.environ["STABBUR_BACKENDS"] = json.dumps([spec.model_dump() for spec in backends])


def _port_free(host: str, port: int) -> bool:
    """Whether ``port`` can be bound on ``host`` right now (a pre-flight, so the failure is ours).

    Probes with SO_REUSEADDR because that is what uvicorn binds with: the question this answers
    has to be "can uvicorn bind here?", not a stricter one it would refuse on. Without it, the
    TIME_WAIT sockets a just-stopped server leaves behind (uvicorn closes its keep-alives on
    shutdown, so the *server* side holds TIME_WAIT for ~15s) read as "in use" — stabbur then refuses
    to restart on a port uvicorn would have taken happily. SO_REUSEADDR does not let anything
    steal a live server's port: a running listener still fails to bind either way. A race with
    another process between this check and uvicorn's bind is possible but harmless — uvicorn then
    reports the collision itself.
    """
    import socket  # noqa: PLC0415

    target = host or "127.0.0.1"
    try:
        # Resolve first so the probe uses the host's real family: binding AF_INET at an IPv6
        # address (::1, or an IPv6-only name) fails, which would be misreported as "in use".
        infos = socket.getaddrinfo(target, port, type=socket.SOCK_STREAM)
    except OSError:
        return True  # unresolvable here: let uvicorn produce the real error, don't guess
    for family, socktype, proto, _canon, sockaddr in infos:
        try:
            with socket.socket(family, socktype, proto) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(sockaddr)
        except OSError:
            return False
    return True


@app.command()
def serve(
    ui: Annotated[bool, typer.Option("--ui", help="Also serve the browser UI (single-page app).")] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Lock the server to one model (extension backend); no switching."),
    ] = None,
    upstream: Annotated[
        list[str] | None,
        typer.Option(
            "--upstream",
            help="Front a remote OpenAI-compatible /v1 (e.g. a llama-server router: http://gpu-box:8080) "
            "instead of spawning local runtimes — the agent loop, tools, and UI run here; the models "
            "run there. Repeatable: each one is declared as a backend named after its host.",
        ),
    ] = None,
    host: Annotated[str | None, typer.Option("--host", help="Bind address (default 127.0.0.1).")] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help=f"Web server port (default {config.DEFAULT_SERVE_PORT})."),
    ] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes.")] = False,
) -> None:
    """Run the web server (browse API, plus the browser UI with --ui).

    With --model the server is locked to a single model (the Chrome-extension
    backend); otherwise the UI can switch models freely. The port is fixed (2222 by
    default) so the URL is stable across restarts; if it's taken, serve says so
    rather than moving — pass --port, or `stabbur config set port` for a new default.
    """
    import os  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    upstream_urls = [url for url in (_normalize_server_url(raw) for raw in (upstream or [])) if url]
    # The first --upstream is *the* upstream, exactly as a single one always was: it is what the
    # /v1 proxy forwards to and what a locked --model is checked against. The rest are declared
    # (below) but not yet served — routing across several backends is the next ROADMAP step.
    upstream_url = upstream_urls[0] if upstream_urls else None
    if upstream_url is None:
        library_ops.roots()  # fail fast + clean if no library is configured (rather than 500ing per request)

    # A project is a locked, purpose-built assistant: it binds the server to its model
    # (no picker, like --model), so `stabbur serve` in a project == serve --model <project.model>.
    # An explicit --model overrides. No project (or a project without a model) => free-play.
    proj = project.load()
    locked_model = model or (proj.model if proj else None)
    locked_by_project = model is None and locked_model is not None
    if locked_model is not None:
        # Validate the locked model up front (like `stabbur chat`) so a bad name gives a clean message
        # here, not a uvicorn "Application startup failed" traceback from the app lifespan. In
        # upstream mode the name must match one of the REMOTE's ids, not a library model.
        if upstream_url is not None:
            from stabbur import backends  # noqa: PLC0415

            try:
                # Name check only: this pre-flight runs before the server exists, so loading
                # here would evict the remote's resident model for a process about to exit.
                # Built through the shared factory rather than a bare manager, so this and the
                # app agree on what "the backend for this configuration" means.
                backends.build(upstream_url).load_by_name(locked_model, warmup=False)
            except RuntimeError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(1) from exc
        else:
            _resolve_library_model(locked_model, None)

    # Resolve the declared backends before anything is served, so a bad [[backends]] entry or a
    # pair of --upstream flags that cannot both be named is a clean line here rather than a
    # surprise from the picker later. Read before the export below: the declaration comes from the
    # config files and the flags, never from STABBUR_BACKENDS (which this then writes).
    get_settings.cache_clear()
    # Snapshot before the export below writes STABBUR_BACKENDS, which would otherwise read back
    # as "the files declared backends" and turn a plain single --upstream into a new-style run.
    declared_in_files = bool(get_settings().backends)
    try:
        declared = config.declared_backends(upstream_urls)
    except config.BackendDeclarationError as exc:
        # Escaped: these messages name `[[backends]]`, which Rich would otherwise read as markup
        # and swallow — leaving the user a fix that omits the very thing they must type.
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc

    # Hand the serve config to the (possibly reloaded) worker process — one documented env API.
    _export_serve_env(
        ui=ui,
        model=locked_model,
        runtime_port=config.runtime_port_override(),
        debug=config.debug_enabled(),
        upstream=upstream_url,
        # Only when the command line added something: without a flag the worker reads the same
        # files this process did and resolves the identical list on its own.
        backends=declared if upstream_urls else None,
    )

    get_settings.cache_clear()
    settings = get_settings()
    # Precedence: --host/--port > STABBUR_HOST/STABBUR_PORT/stabbur.toml > the fixed default.
    bind_host = host or settings.host
    bind_port = port or settings.port
    if not _port_free(bind_host, bind_port):
        # Never silently move: a wandering URL breaks bookmarks, the extension origin, and
        # `stabbur chat --server`. Say what is wrong and let the user choose.
        console.print(
            f"[red]Port {bind_port} is already in use[/] — another stabbur serve may be running.\n"
            f"Pass [cyan]--port <number>[/] for this run, or set a new default with "
            f"[cyan]stabbur config set port <number>[/]."
        )
        raise typer.Exit(1)
    base = f"http://{bind_host}:{bind_port}"

    # Binding a non-loopback address exposes model control + MCP tool execution (arbitrary code
    # via stabbur-mcp-exec) to the LAN. Never leave that unauthenticated: auto-generate a bearer
    # token if one isn't configured, so a client must present it (V-14). An explicitly-set
    # STABBUR_AUTH_TOKEN is honored as-is (and enforced even on loopback, for deliberate opt-in).
    loopback = bind_host in ("127.0.0.1", "localhost", "::1", "")
    auth_token = settings.auth_token
    if not loopback and not auth_token:
        auth_token = secrets.token_urlsafe(24)
        os.environ["STABBUR_AUTH_TOKEN"] = auth_token  # part of the serve→worker env API (_export_serve_env)
        get_settings.cache_clear()

    console.print("\n[bold]stabbur[/]")
    if not loopback:
        console.print(
            f"  [yellow]Exposed on {bind_host}[/] — anyone who can reach this host can control models and run tools."
        )
    if auth_token:
        console.print("  Auth:     [bold]bearer token required[/] [dim](Authorization: Bearer <token>)[/]")
    if upstream_url is not None:
        console.print(f"  Upstream: [bold]{upstream_url}[/] [dim]· remote models, no local runtimes[/]")
    # One --upstream and nothing else declared is the old single-backend invocation; the Upstream
    # line above already says everything, so it keeps its exact banner. Anything more is a real
    # declaration, and then the names are worth showing — they are what a `model@backend` id is
    # written with.
    legacy_single = upstream_url is not None and len(upstream_urls) == 1 and not declared_in_files
    if len(declared) > 1 and not legacy_single:
        listing = ", ".join(f"{spec.name} [dim]({spec.url or 'local library'})[/]" for spec in declared)
        console.print(f"  Backends: {listing}")
    if len(upstream_urls) > 1:
        console.print(
            "  [yellow]Serving the first upstream only[/] [dim]· the rest are declared; "
            "routing across backends is not wired up yet[/]"
        )
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

    # Advertise this serve so `stabbur chat` (no --server) can attach to its loaded model instead of
    # reloading. Only unauthenticated (loopback, no-token) serves — chat can't send a token yet.
    from stabbur.runtime import serve_registry  # noqa: PLC0415

    if not auth_token and locked_model is not None:
        serve_registry.register(base, locked_model)
    try:
        uvicorn.run("stabbur.app:app", host=bind_host, port=bind_port, reload=reload)
    finally:
        serve_registry.unregister()


# --- voice -----------------------------------------------------------------
