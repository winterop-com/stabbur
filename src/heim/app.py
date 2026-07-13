"""FastAPI application factory."""

import asyncio
import secrets
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from heim import config, mcpservers, project, runtime
from heim import library as library_ops
from heim import tools as mcp_tools
from heim.config import Settings, get_settings
from heim.routers import catalog, health, serving
from heim.runtime import supervisor
from heim.server import ServerManager
from heim.targets import AssistantRegistry

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


_GUARDED_PREFIXES = ("/api", "/v1", "/models")


def _cross_site_blocked(request: Request, allowed_origins: list[str]) -> bool:
    """Whether to reject a request as a cross-site (drive-by) browser call.

    Guards mutating ``/api``, ``/v1``, and ``/models`` calls (the last exposes
    ``POST /models/{source}/pull``, which downloads/copies files to disk). A random
    webpage in the user's browser can POST to the localhost server — and a
    no-preflight "simple" request (e.g. ``text/plain`` body, or a no-body POST)
    still executes server-side, firing MCP tools / disk writes — so block requests
    the browser marks cross-site via ``Sec-Fetch-Site``. Allowed through:
    same-origin (the served SPA), an explicitly-configured origin (extension/dev),
    and non-browser clients (curl, the CLI, tests — they send no ``Sec-Fetch-Site``).
    """
    if request.method not in _MUTATING_METHODS:
        return False
    path = request.url.path
    if not path.startswith(_GUARDED_PREFIXES):
        return False
    origin = request.headers.get("origin")
    # Only a *specific* allow-listed origin bypasses the guard. A bare "*" enables
    # CORS (so responses are readable cross-origin) but must NOT exempt mutating
    # calls — otherwise a wildcard config silently re-opens the tool-execution hole.
    if origin and origin in allowed_origins:
        return False
    site = request.headers.get("sec-fetch-site")
    if site is None:
        # No Sec-Fetch metadata: either a genuine non-browser client (curl/CLI/tests — which
        # also send no Origin) or an old browser / embedded WebView that omits Sec-Fetch-Site
        # (Safari <16.4). Fall back to the Origin header so the latter aren't a blanket bypass:
        # a cross-site browser POST still carries Origin, so an Origin whose host differs from
        # this server's (and isn't allow-listed, checked above) is a cross-site call → block.
        # No Origin at all → a non-browser client → allow (V-13).
        if not origin:
            return False
        return urlsplit(origin).netloc != (request.headers.get("host") or "")
    return site not in ("same-origin", "none")


def _auth_failed(request: Request, token: str) -> bool:
    """Whether a request to a guarded route lacks the required bearer token.

    No-op when ``token`` is empty (auth disabled — the loopback default). Otherwise every
    ``/api``, ``/v1``, ``/models`` call (any method) must carry ``Authorization: Bearer <token>``;
    this is what keeps model control + MCP tool execution from being open to the LAN when the
    server binds a non-loopback address (V-14). The SPA shell, favicon, and ``/health`` stay
    unauthenticated so a browser can still load the page (then supply the token per request).
    """
    if not token:
        return False
    if request.method == "OPTIONS":
        return False  # CORS preflight carries no Authorization; let CORS handle it
    if not request.url.path.startswith(_GUARDED_PREFIXES):
        return False
    header = request.headers.get("authorization", "")
    scheme, _, provided = header.partition(" ")
    if scheme.lower() != "bearer":
        return True
    return not secrets.compare_digest(provided.strip(), token)  # constant-time compare


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start a shared HTTP client, runtime manager, and MCP tools; clean up after."""
    settings: Settings = app.state.settings
    manager: ServerManager = app.state.manager

    # Reclaim any runtime orphaned by a previously-crashed heim before we start ours (A4).
    try:
        supervisor.sweep_orphans()
    except Exception:  # noqa: BLE001 - a sweep failure must never block startup
        pass

    if settings.serve_model:
        matches = library_ops.find(settings.serve_model)
        if len(matches) != 1:
            raise RuntimeError(
                f"locked --model {settings.serve_model!r} did not resolve to exactly one library "
                f"model (found {len(matches)}); fix the name or library before locking the server"
            )
        reason = runtime.runnable_error(matches[0])
        if reason is not None:
            raise RuntimeError(f"locked --model cannot be run: {reason}")
        manager.load(matches[0])

    # Spawn the resolved MCP servers (global ~/.config/heim/mcp.json + project .mcp.json) once,
    # shared across chat requests, so the web UI / extension get tools via the server-side agent loop.
    async with AsyncExitStack() as mcp_stack:
        proj = project.load()
        app.state.system_prompt = proj.system_prompt if proj else ""
        # The project's assistant target(s). `registry` holds all of them ([[assistants]]); `assistant`
        # stays the primary (registry.primary) so every existing consumer (/api/assistant, confirm
        # policy) is untouched. Both empty/None outside a project or when no assistant block is present.
        registry = proj.registry if proj else AssistantRegistry()
        app.state.registry = registry
        app.state.assistant = registry.primary
        # The model the UI auto-loads on open: the project's bound model (a project is a
        # reproducible assistant: model + prompt + tools), or — outside a project — the machine
        # default (`heim config set model`), so free-play serve --ui still opens on a model.
        app.state.project_model = project.resolve_model(None, proj)
        # The project's spoken-reply voice + whether the Voice surface is enabled, surfaced in
        # /api/status so the UI defaults the Listen voice and hides Voice for a text-only assistant.
        app.state.chat_voice = proj.chat_voice if proj else None
        app.state.voice_enabled = proj.voice_enabled if proj else True
        resolved = mcpservers.resolve()
        # Map each target id -> the server names it owns, from the resolved server *names* (not live
        # clients), computed before connect. A target with mcp_servers=[] owns ALL resolved servers
        # (the compat rule: today's merged toolset). Per-request routing (a later chunk) reads this.
        server_names = {s.name for s in resolved}
        app.state.target_servers = {
            target_id: (set(target.mcp_servers) if target.mcp_servers else set(server_names))
            for target_id, target in zip(registry.ids, registry.targets, strict=True)
        }
        servers = [s.to_spec() for s in resolved]
        if servers:
            app.state.toolset = await mcp_stack.enter_async_context(mcp_tools.connect(servers))
        try:
            yield
        finally:
            manager.stop()
            await app.state.http.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional settings override; defaults to the cached settings.

    Returns:
        The configured FastAPI application.
    """
    if settings is None:
        settings = get_settings()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    # Honor the passed settings' runtime_port; the CLI --runtime-port override
    # (process-global) still wins when set. None → ServerManager auto-picks.
    app.state.manager = ServerManager(port=config.runtime_port_override() or settings.runtime_port)
    # Serializes model load/unload so two concurrent requests can't interleave and
    # corrupt the manager's process state (ServerManager has no internal lock, and
    # load/unload now run in worker threads). Async, so waiting doesn't block the loop.
    app.state.lifecycle_lock = asyncio.Lock()
    # Count of in-flight generations (server-side chat + /v1 proxy streams). A
    # load/unload refuses while this is > 0 so a running generation never has its
    # runtime swapped/killed out from under it. Mutated only on the event loop.
    app.state.active_generations = 0
    # Shared client for the /v1 proxy; closed in lifespan. Bounded: a hung/half-open
    # runtime must not hold the generation reservation forever (which would 409 every
    # later load/unload). 600s per read allows the long token gaps of prompt processing
    # on big contexts (and matches the agent loop's own client).
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    # MCP toolset + system prompt for the server-side agent loop; populated by
    # lifespan from heim.toml (None / "" otherwise).
    app.state.toolset = None
    app.state.system_prompt = ""
    app.state.project_model = None
    app.state.chat_voice = None
    app.state.voice_enabled = True
    # Target-instance metadata for /api/assistant (the project's [assistant] block), and the 60s
    # TTL cache of its last verify probe. Both populated by lifespan when a project loads.
    app.state.assistant = None
    app.state.assistant_verified = None  # compat single-slot cache for /api/assistant
    # Per-target verify state for /api/assistants/{id}: one (checked_at, AssistantVerified) slot and one
    # single-flight lock per registry id, so verifying/binding one target never touches another's cache.
    app.state.assistant_verified_by_id = {}
    app.state.assistant_verify_locks = {}
    # The full multi-target registry (all [[assistants]]) and, per target id, the set of .mcp.json
    # server names it owns (empty mcp_servers => owns every resolved server). Populated by lifespan.
    app.state.registry = AssistantRegistry()
    app.state.target_servers = {}
    # Pending per-action write-confirmations for /api/chat, keyed by an unguessable server-minted
    # uuid delivered only over the SSE stream. Each future is resolved by POST /api/chat/confirm
    # (user approve/decline) or auto-denied on timeout; mutated only on the event loop.
    pending_confirmations: dict[str, asyncio.Future[bool]] = {}
    app.state.pending_confirmations = pending_confirmations

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Reject cross-site (drive-by) browser calls to mutating endpoints, so a random
    # webpage can't load models / run MCP tools on the local server. Same-origin SPA,
    # allow-listed origins, and non-browser clients are unaffected.
    @app.middleware("http")
    async def _cross_site_guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if _auth_failed(request, settings.auth_token):
            return JSONResponse(
                {"detail": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if _cross_site_blocked(request, settings.cors_origins):
            return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)
        return await call_next(request)

    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(serving.router)

    # Serve the SPA via FastAPI's first-party frontend() when enabled and built.
    # API path operations are matched first; fallback="index.html" supports the
    # SPA's client-side routing.
    if settings.serve_ui and settings.frontend_dir.is_dir():
        # Browsers probe /favicon.ico regardless of the declared <link> icon; serve
        # the SVG for it so it doesn't 404 (the SPA fallback would hand back HTML).
        favicon = settings.frontend_dir / "favicon.svg"
        if favicon.is_file():

            @app.get("/favicon.ico", include_in_schema=False)
            async def _favicon() -> FileResponse:
                return FileResponse(favicon, media_type="image/svg+xml")

        app.frontend("/", directory=str(settings.frontend_dir), fallback="index.html")

    return app


app = create_app()
