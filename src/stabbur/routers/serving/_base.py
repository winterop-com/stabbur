"""Shared serving infrastructure: the APIRouter, request-scoped deps, and runtime reservation."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from stabbur.config import Settings
from stabbur.server import ServerManager, UpstreamManager

router = APIRouter(tags=["serving"])

# Hop-by-hop headers that must not be forwarded through the proxy.
_DROP_HEADERS = {"content-length", "transfer-encoding", "connection", "host"}


def get_manager(request: Request) -> "ServerManager | UpstreamManager":
    """Dependency: the app's singleton backend manager (local runtime, or remote upstream)."""
    manager: ServerManager | UpstreamManager = request.app.state.manager
    return manager


def get_http(request: Request) -> httpx.AsyncClient:
    """Dependency: the app's shared HTTP client (created in lifespan)."""
    client: httpx.AsyncClient = request.app.state.http
    return client


def get_conf(request: Request) -> Settings:
    """Dependency: the app's configured settings (not the global cache)."""
    settings: Settings = request.app.state.settings
    return settings


def get_lifecycle_lock(request: Request) -> asyncio.Lock:
    """Dependency: the lock serializing model load/unload (created in create_app)."""
    lock: asyncio.Lock = request.app.state.lifecycle_lock
    return lock


ManagerDep = Annotated[ServerManager | UpstreamManager, Depends(get_manager)]
HttpDep = Annotated[httpx.AsyncClient, Depends(get_http)]
LockDep = Annotated[asyncio.Lock, Depends(get_lifecycle_lock)]
ConfDep = Annotated[Settings, Depends(get_conf)]


async def _acquire_runtime(request: Request) -> None:
    """Reserve the runtime for a generation.

    Takes the lifecycle lock briefly — so it can't slip in mid load/unload — bumps
    the active-generation count, then releases the lock. While the count is > 0 a
    load/unload is refused (see ``_reject_if_generating``), so the runtime a running
    generation is streaming from is never swapped or killed underneath it.
    """
    lock: asyncio.Lock = request.app.state.lifecycle_lock
    async with lock:
        request.app.state.active_generations += 1


def _release_runtime(request: Request) -> None:
    """Release a runtime reservation taken by :func:`_acquire_runtime`."""
    request.app.state.active_generations -= 1


@asynccontextmanager
async def _reserve_runtime(request: Request) -> AsyncGenerator[None, None]:
    """Hold a runtime reservation for the duration of a ``with`` block."""
    await _acquire_runtime(request)
    try:
        yield
    finally:
        _release_runtime(request)


def _reject_if_generating(request: Request) -> None:
    """409 if a generation is in flight — its runtime must not be swapped/stopped.

    Called by load/unload while holding the lifecycle lock, so the count can't
    change between this check and the mutation.
    """
    if request.app.state.active_generations > 0:
        raise HTTPException(
            status_code=409,
            detail="A response is in progress; stop it before switching or unloading the model.",
        )
