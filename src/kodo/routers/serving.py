"""Model lifecycle, server-side chat (agent loop + MCP), and OpenAI `/v1` proxy."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from kodo import agent, runtime
from kodo import library as library_ops
from kodo.config import Settings
from kodo.server import ServerManager
from kodo.tools import MCPToolset

router = APIRouter(tags=["serving"])

# Hop-by-hop headers that must not be forwarded through the proxy.
_DROP_HEADERS = {"content-length", "transfer-encoding", "connection", "host"}


class ServerStatus(BaseModel):
    """Current runtime status for the UI."""

    state: str
    model: str | None = None
    locked: bool = False


class LibraryModelInfo(BaseModel):
    """A runnable library model, for the UI's model picker."""

    name: str
    model_format: str
    size_bytes: int
    size_human: str


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


@router.get("/api/library")
def library() -> list[LibraryModelInfo]:
    """List runnable (generative) library models for the UI's picker.

    Sync (``def``) so the filesystem scan runs in a worker thread, off the loop.
    """
    return [
        LibraryModelInfo(
            name=m.name, model_format=m.model_format.value, size_bytes=m.size_bytes, size_human=m.size_human
        )
        for m in library_ops.scan()
        if m.generative and not m.is_ollama
    ]


class ChatRequest(BaseModel):
    """A chat turn for the server-side agent loop."""

    messages: list[dict[str, Any]]
    max_tokens: int | None = None
    use_tools: bool = True  # off → don't attach MCP tools (for non-tool-trained models)


@router.post("/api/chat")
async def chat(req: ChatRequest, manager: ManagerDep, request: Request) -> StreamingResponse:
    """Run the agent loop (MCP tools + the loaded model) and stream typed SSE.

    Events: ``{"type":"token","text":...}``, ``{"type":"tool","kind":"call"|"result",
    "detail":...}``, ``{"type":"error","detail":...}``, ``{"type":"done"}``. Unlike
    the raw ``/v1`` proxy, this executes tool calls server-side so the web UI and
    extension get tools — and surfaces tool activity the proxy can't.
    """
    if manager.current is None:
        raise HTTPException(status_code=409, detail="No model loaded")
    # use_tools off → empty toolset (non-tool-trained models otherwise regurgitate
    # the injected tool schema as text instead of calling tools).
    toolset: MCPToolset = (
        (getattr(request.app.state, "toolset", None) or MCPToolset()) if req.use_tools else MCPToolset()
    )
    base = manager.base_url

    # Apply the project's system prompt (kodo.toml) unless the client sent its own.
    system_prompt: str = getattr(request.app.state, "system_prompt", "") or ""
    messages = list(req.messages)
    if system_prompt and not (messages and messages[0].get("role") == "system"):
        messages = [{"role": "system", "content": system_prompt}, *messages]

    async def events() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        done = {"type": "done"}

        def on_event(kind: str, detail: str) -> None:
            queue.put_nowait({"type": "tool", "kind": kind, "detail": detail[:2000]})

        def on_token(text: str) -> None:
            queue.put_nowait({"type": "token", "text": text})

        async def produce() -> None:
            try:
                await agent.run(base, messages, toolset, req.max_tokens, on_event, on_token)
            except Exception as exc:  # noqa: BLE001 - surface any runtime/tool failure to the client
                queue.put_nowait({"type": "error", "detail": str(exc)})
            finally:
                queue.put_nowait(done)

        task = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is done:
                    break
                yield f"data: {json.dumps(item)}\n\n"
            yield 'data: {"type": "done"}\n\n'
        finally:
            if not task.done():
                task.cancel()  # client disconnected → cancel the in-flight generation

    return StreamingResponse(events(), media_type="text/event-stream")


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
