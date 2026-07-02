"""FastAPI application factory."""

import asyncio
import shlex
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from kodo import config, project, runtime
from kodo import library as library_ops
from kodo import tools as mcp_tools
from kodo.config import Settings, get_settings
from kodo.routers import catalog, health, serving
from kodo.server import ServerManager

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _cross_site_blocked(request: Request, allowed_origins: list[str]) -> bool:
    """Whether to reject a request as a cross-site (drive-by) browser call.

    Guards mutating ``/api`` and ``/v1`` calls. A random webpage in the user's
    browser can POST to the localhost server — and a no-preflight "simple" request
    (e.g. ``text/plain`` body, or a no-body POST) still executes server-side,
    firing MCP tools — so block requests the browser marks cross-site via
    ``Sec-Fetch-Site``. Allowed through: same-origin (the served SPA), an
    explicitly-configured origin (extension/dev), and non-browser clients (curl,
    the CLI, tests — they send no ``Sec-Fetch-Site``).
    """
    if request.method not in _MUTATING_METHODS:
        return False
    path = request.url.path
    if not (path.startswith("/api") or path.startswith("/v1")):
        return False
    origin = request.headers.get("origin")
    # Only a *specific* allow-listed origin bypasses the guard. A bare "*" enables
    # CORS (so responses are readable cross-origin) but must NOT exempt mutating
    # calls — otherwise a wildcard config silently re-opens the tool-execution hole.
    if origin and origin in allowed_origins:
        return False
    site = request.headers.get("sec-fetch-site")
    if site is None:
        return False  # non-browser client (no Sec-Fetch metadata)
    return site not in ("same-origin", "none")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start a shared HTTP client, runtime manager, and MCP tools; clean up after."""
    settings: Settings = app.state.settings
    manager: ServerManager = app.state.manager

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

    # Spawn the project's MCP servers (kodo.toml [[mcp]]) once, shared across chat
    # requests, so the web UI / extension get tools via the server-side agent loop.
    async with AsyncExitStack() as mcp_stack:
        proj = project.load()
        app.state.system_prompt = proj.system_prompt if proj else ""
        # The project's bound model, surfaced in /api/status so the UI auto-loads
        # it on open (a project is a reproducible assistant: model + prompt + tools).
        app.state.project_model = proj.model if proj else None
        servers = [(m.name, shlex.split(m.command)) for m in proj.mcp] if proj else []
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
    # Shared client for the /v1 proxy (no timeout — streaming); closed in lifespan.
    app.state.http = httpx.AsyncClient(timeout=None)
    # MCP toolset + system prompt for the server-side agent loop; populated by
    # lifespan from kodo.toml (None / "" otherwise).
    app.state.toolset = None
    app.state.system_prompt = ""
    app.state.project_model = None

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
        app.frontend("/", directory=str(settings.frontend_dir), fallback="index.html")

    return app


app = create_app()
