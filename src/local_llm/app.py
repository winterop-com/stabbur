"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from local_llm import library as library_ops
from local_llm.config import Settings, get_settings
from local_llm.routers import catalog, health, serving
from local_llm.server import ServerManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the runtime manager; load a locked model; clean up on shutdown."""
    settings: Settings = app.state.settings
    manager: ServerManager = app.state.manager

    if settings.serve_model:
        matches = library_ops.find(settings.serve_model)
        if matches:
            manager.load(matches[0])
    try:
        yield
    finally:
        manager.stop()


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
    app.state.manager = ServerManager(port=settings.runtime_port)

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

    # Serve the SPA at the root when enabled and built (API routes registered
    # above take precedence; this is the catch-all for the browser UI).
    if settings.serve_ui and settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="ui")

    return app


app = create_app()
