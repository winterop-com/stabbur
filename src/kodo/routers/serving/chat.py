"""Server-side chat: the streaming agent loop (/api/chat) and model load (/api/load)."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from kodo import agent, capabilities, runtime
from kodo import library as library_ops
from kodo.routers.serving._base import (  # shared router + request deps
    ConfDep,
    LockDep,
    ManagerDep,
    _reject_if_generating,
    _reserve_runtime,
    router,
)
from kodo.routers.serving.core import ServerStatus, _status
from kodo.runtime import sampling
from kodo.tools import MCPToolset

_MAX_DETAIL = 2000  # cap on a tool SSE detail so one giant result can't flood the stream / the UI
_MAX_DETAIL_STR = 200  # per-string cap when re-dumping a large JSON detail so it stays parseable


def _cap_strings(value: Any) -> Any:
    """Recursively cap every string in a JSON-decoded value to ``_MAX_DETAIL_STR`` chars."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_DETAIL_STR else value[:_MAX_DETAIL_STR] + "..."
    if isinstance(value, list):
        return [_cap_strings(v) for v in value]
    if isinstance(value, dict):
        return {k: _cap_strings(v) for k, v in value.items()}
    return value


def _truncate_detail(detail: str) -> str:
    """Cap a tool SSE detail near ``_MAX_DETAIL`` chars, preserving JSON structure when it is JSON.

    Small details pass through untouched. A large *JSON* detail is re-dumped compactly with every
    string value capped, so the UI's collapsible chips can still parse it (a blind ``detail[:2000]``
    slice would leave them a truncated, unparseable fragment — the exact large payloads they exist
    for). If the capped re-dump is still too big, or the detail isn't JSON, fall back to a hard cut.
    """
    if len(detail) <= _MAX_DETAIL:
        return detail
    try:
        parsed = json.loads(detail)
    except (ValueError, TypeError):
        return detail[:_MAX_DETAIL]
    dumped = json.dumps(_cap_strings(parsed), separators=(",", ":"))
    return dumped if len(dumped) <= _MAX_DETAIL else detail[:_MAX_DETAIL]


class ChatRequest(BaseModel):
    """A chat turn for the server-side agent loop."""

    messages: list[dict[str, Any]]
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    use_tools: bool = True  # off → don't attach MCP tools (for non-tool-trained models)
    enabled_tools: list[str] | None = None  # None → all tools; else only these namespaced names
    # Authoritative system prompt: a string (incl. "" for *no* system prompt) overrides
    # the project default; None (field absent) falls back to it. Lets a roleplay model
    # run with no assistant framing instead of being forced into "I'm an AI" refusals.
    system_prompt: str | None = None


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
    # An explicit allow-list narrows the toolset to the tools the user left enabled.
    if req.enabled_tools is not None:
        toolset = toolset.subset(set(req.enabled_tools))

    # System prompt precedence: an explicit ``system_prompt`` from the client is
    # authoritative — including "" for *no* system prompt (a roleplay model then
    # runs with no assistant framing). Only when the field is absent (None) do we
    # fall back to the project (kodo.toml) prompt. A system message already in
    # ``messages`` still wins (kept for API clients that inline their own).
    if req.system_prompt is not None:
        system_prompt = req.system_prompt
    else:
        system_prompt = getattr(request.app.state, "system_prompt", "") or ""
    messages = list(req.messages)
    if system_prompt and not (messages and messages[0].get("role") == "system"):
        messages = [{"role": "system", "content": system_prompt}, *messages]

    async def events() -> AsyncGenerator[str, None]:
        # Bounded queue = backpressure: when a slow SSE consumer (browser) lets it fill, the
        # async sinks below block on `put`, which pauses agent.run's read from the runtime
        # (TCP backpressure to llama-server) instead of buffering the whole reply in RAM (V-12).
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        done = {"type": "done"}

        async def on_event(kind: str, detail: str) -> None:
            await queue.put({"type": "tool", "kind": kind, "detail": _truncate_detail(detail)})

        async def on_token(text: str) -> None:
            await queue.put({"type": "token", "text": text})

        async def on_reasoning(text: str) -> None:
            await queue.put({"type": "reasoning", "text": text})

        # Reserve the runtime for the whole stream so a load/unload can't swap or kill
        # it mid-generation; read the current model/URL *inside* the reservation.
        async with _reserve_runtime(request):
            current = manager.current
            if current is None:  # swapped out in the race window before we reserved
                yield f"data: {json.dumps({'type': 'error', 'detail': 'No model loaded'})}\n\n"
                yield 'data: {"type": "done"}\n\n'
                return
            base = manager.base_url
            model_target = current.load_target
            try:
                model_vision = capabilities.capabilities(current).vision  # feed tool images back only if seen
            except Exception:  # noqa: BLE001 - detection is best-effort; a failure just disables image feedback
                model_vision = False
            # Model-recommended sampling (LM Studio parity); an explicit request value wins.
            rec = sampling.recommended(current)
            eff_temperature = req.temperature if req.temperature is not None else rec.temperature
            eff_top_p = req.top_p if req.top_p is not None else rec.top_p
            # A client that omits max_tokens gets the configured default cap so a small model
            # can't run away on a hard tool question and never emit a final answer; <= 0 disables it.
            default_cap = request.app.state.settings.default_max_tokens
            eff_max_tokens = (
                req.max_tokens if req.max_tokens is not None else (default_cap if default_cap > 0 else None)
            )

            async def produce() -> None:
                try:
                    await agent.run(
                        base,
                        messages,
                        toolset,
                        eff_max_tokens,
                        on_event,
                        on_token,
                        on_reasoning=on_reasoning,
                        temperature=eff_temperature,
                        top_p=eff_top_p,
                        top_k=rec.top_k,
                        min_p=rec.min_p,
                        repeat_penalty=rec.repeat_penalty,
                        # mlx-vlm requires the OpenAI ``model`` field match what it loaded
                        # (the launch path); harmless for llama-server / mlx-lm.
                        model=str(model_target) if model_target else None,
                        vision=model_vision,
                    )
                except Exception as exc:  # noqa: BLE001 - surface any runtime/tool failure to the client
                    await queue.put({"type": "error", "detail": str(exc)})
                finally:
                    await queue.put(done)

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
                # Wait for the producer to actually finish before the reservation is released
                # (on `_reserve_runtime` exit) — otherwise it can still be touching a runtime
                # that a load/unload then swaps out from under it (V-11).
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/api/load/{name:path}")
async def load(
    name: str, manager: ManagerDep, settings: ConfDep, lock: LockDep, request: Request, n_ctx: int | None = None
) -> ServerStatus:
    """Load (or switch to) a model by name; rejected in locked mode.

    ``n_ctx`` sets the context window (GGUF/llama.cpp only); changing it reloads
    the model since context is fixed at load time.
    """
    if settings.serve_model is not None:
        raise HTTPException(status_code=409, detail="Server is locked to a single model")
    if n_ctx is not None and n_ctx < 1:
        raise HTTPException(status_code=422, detail="n_ctx must be a positive integer")
    matches = library_ops.find(name)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No library model matches {name!r}")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"{name!r} is ambiguous across formats")
    reason = runtime.runnable_error(matches[0])
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    try:
        # load() spawns the runtime but first stops any current one (a terminate
        # that can wait up to 10s) — run it off the event loop so status polling and
        # other requests don't stall during a slow model swap. The asyncio lock
        # serializes the normal path (and avoids flooding the threadpool with queued
        # loads); ServerManager's own thread lock is the actual guarantee if a
        # request is cancelled while its worker thread is still inside load().
        async with lock:
            _reject_if_generating(request)  # don't swap the runtime under a live generation
            await asyncio.to_thread(manager.load, matches[0], n_ctx)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return await _status(manager, settings)
