"""FastAPI application factory."""

import shlex
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kodo import config, project, runtime
from kodo import library as library_ops
from kodo import tools as mcp_tools
from kodo.config import Settings, get_settings
from kodo.routers import catalog, health, serving
from kodo.server import ServerManager


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
        commands = [shlex.split(m.command) for m in proj.mcp] if proj else []
        if commands:
            app.state.toolset = await mcp_stack.enter_async_context(mcp_tools.connect(commands))
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
