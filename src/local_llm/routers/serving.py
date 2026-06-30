"""Model lifecycle + OpenAI `/v1` proxy for the browser UI."""

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from local_llm import library as library_ops
from local_llm.config import get_settings
from local_llm.server import ServerManager

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


ManagerDep = Annotated[ServerManager, Depends(get_manager)]


async def _status(manager: ServerManager) -> ServerStatus:
    current = manager.current
    return ServerStatus(
        state=(await manager.state()).value,
        model=current.name if current else None,
        locked=get_settings().serve_model is not None,
    )


@router.get("/api/status")
async def status(manager: ManagerDep) -> ServerStatus:
    """Report the loaded model and runtime state."""
    return await _status(manager)


@router.post("/api/load/{name:path}")
async def load(name: str, manager: ManagerDep) -> ServerStatus:
    """Load (or switch to) a model by name; rejected in locked mode."""
    if get_settings().serve_model is not None:
        raise HTTPException(status_code=409, detail="Server is locked to a single model")
    matches = library_ops.find(name)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No library model matches {name!r}")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"{name!r} is ambiguous across formats")
    try:
        manager.load(matches[0])
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return await _status(manager)


@router.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def proxy_v1(path: str, request: Request, manager: ManagerDep) -> StreamingResponse:
    """Stream-proxy OpenAI `/v1/*` calls to the loaded runtime."""
    if manager.current is None:
        raise HTTPException(status_code=409, detail="No model loaded")

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
    client = httpx.AsyncClient(timeout=None)
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
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"runtime unreachable: {exc}") from exc

    resp_headers: dict[str, Any] = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_HEADERS}

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=BackgroundTask(close),
    )
