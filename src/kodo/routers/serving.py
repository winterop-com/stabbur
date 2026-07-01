"""Model lifecycle + OpenAI `/v1` proxy for the browser UI."""

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from kodo import library as library_ops
from kodo import runtime
from kodo.config import Settings
from kodo.server import ServerManager

router = APIRouter(tags=["serving"])

# Hop-by-hop headers that must not be forwarded through the proxy.
_DROP_HEADERS = {"content-length", "transfer-encoding", "connection", "host"}


class ServerStatus(BaseModel):
    """Current runtime status for the UI."""

    state: str
    model: str | None = None
    locked: bool = False


def get_manager(request: Request) -> ServerManager:
    """Dependency: the app's singleton runtime manager."""
    manager: ServerManager = request.app.state.manager
    return manager


def get_http(request: Request) -> httpx.AsyncClient:
    """Dependency: the app's shared HTTP client (created in lifespan)."""
    client: httpx.AsyncClient = request.app.state.http
    return client


def get_conf(request: Request) -> Settings:
    """Dependency: the app's configured settings (not the global cache)."""
    settings: Settings = request.app.state.settings
    return settings


ManagerDep = Annotated[ServerManager, Depends(get_manager)]
HttpDep = Annotated[httpx.AsyncClient, Depends(get_http)]
ConfDep = Annotated[Settings, Depends(get_conf)]


async def _status(manager: ServerManager, settings: Settings) -> ServerStatus:
    current = manager.current
    return ServerStatus(
        state=(await manager.state()).value,
        model=current.name if current else None,
        locked=settings.serve_model is not None,
    )


@router.get("/api/status")
async def status(manager: ManagerDep, settings: ConfDep) -> ServerStatus:
    """Report the loaded model and runtime state."""
    return await _status(manager, settings)


@router.post("/api/load/{name:path}")
async def load(name: str, manager: ManagerDep, settings: ConfDep) -> ServerStatus:
    """Load (or switch to) a model by name; rejected in locked mode."""
    if settings.serve_model is not None:
        raise HTTPException(status_code=409, detail="Server is locked to a single model")
    matches = library_ops.find(name)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No library model matches {name!r}")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"{name!r} is ambiguous across formats")
    reason = runtime.runnable_error(matches[0])
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    try:
        manager.load(matches[0])
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return await _status(manager, settings)


@router.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def proxy_v1(path: str, request: Request, manager: ManagerDep, client: HttpDep) -> StreamingResponse:
    """Stream-proxy OpenAI `/v1/*` calls to the loaded runtime.

    A manual ``StreamingResponse`` (rather than a yielding path op) is used
    deliberately: a transparent proxy must forward the upstream status code and
    headers (e.g. ``text/event-stream`` for streaming), which the yield form
    cannot set. Bytes are forwarded verbatim, so SSE deltas stream through live.
    """
    if manager.current is None:
        raise HTTPException(status_code=409, detail="No model loaded")

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
    req = client.build_request(
        request.method,
        f"{manager.base_url}/v1/{path}",
        content=body,
        headers=headers,
        params=request.query_params,
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"runtime unreachable: {exc}") from exc

    resp_headers: dict[str, Any] = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_HEADERS}
    # Close only the upstream response; the shared client lives for the app's lifetime.
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream.aclose),
    )
