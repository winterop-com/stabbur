"""Server-side chat: the streaming agent loop (/api/chat) and model load (/api/load)."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from stabbur import agent, capabilities, runtime
from stabbur import library as library_ops
from stabbur.routers.serving._base import (  # shared router + request deps
    ConfDep,
    LockDep,
    ManagerDep,
    _reject_if_generating,
    _reserve_runtime,
    router,
)
from stabbur.routers.serving.core import ServerStatus, _status
from stabbur.runtime import sampling
from stabbur.server import UpstreamManager, UpstreamModel
from stabbur.tools import MCPToolset, TargetRouting, narrow_to_servers

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
    # Sampling. ``None`` means "whatever the model recommends" (stabbur.runtime.sampling), never a
    # hardcoded value here — the resolution happens once, below, so every field falls back the
    # same way. top_k / min_p / repeat_penalty are OpenAI *extensions* llama.cpp and the MLX
    # servers accept; stabbur already sent the recommended values, this just lets a client override
    # them like the other three.
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    repeat_penalty: float | None = None
    use_tools: bool = True  # off → don't attach MCP tools (for non-tool-trained models)
    enabled_tools: list[str] | None = None  # None → all tools; else only these namespaced names
    # Registry target id this turn routes to (narrows tools to that target's servers + shared, and
    # drives the confirm-policy default). ``None`` with a non-empty registry → the primary target;
    # free-play (no registry) ignores it and keeps the full toolset. Unknown id → 400.
    target: str | None = None
    # Authoritative system prompt: a string (incl. "" for *no* system prompt) overrides
    # the project default; None (field absent) falls back to it. Lets a roleplay model
    # run with no assistant framing instead of being forced into "I'm an AI" refusals.
    system_prompt: str | None = None
    # Per-action write-confirmation policy for this turn. ``None`` (absent) defers to the
    # server default: "writes" for a non-readonly assistant, "none" for free-play / readonly
    # (today's ungated behavior). "all" confirms every tool call, "none" gates nothing.
    confirm_tools: Literal["all", "writes", "none"] | None = None
    # Reasoning effort for thinking models: "off" disables thinking, low/medium/high cap the
    # thinking budget (512/2048/8192 tokens), "max" thinks unbounded. ``None`` (absent) leaves
    # the model's default behavior untouched. llama-server dialect; others ignore it.
    reasoning: agent.ReasoningLevel | None = None


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
    # Route this turn to a registry target. With a non-empty registry the toolset is narrowed to the
    # resolved target's servers plus any shared (unowned) servers: an explicit ``target`` picks that
    # target (400 on an unknown id), ``target=None`` picks the primary. Free-play (no registry) keeps
    # the full toolset — today's behavior. The resolved target also drives the confirm-policy default
    # below (``resolved_assistant``); it defaults to ``app.state.assistant`` (the primary) so free-play
    # and existing single-assistant behavior are untouched.
    registry = getattr(request.app.state, "registry", None)
    resolved_assistant = getattr(request.app.state, "assistant", None)
    if registry is not None and registry.targets:
        if req.target is not None:
            resolved_assistant = registry.by_id(req.target)
            if resolved_assistant is None:
                raise HTTPException(status_code=400, detail=f"Unknown target {req.target!r}")
            resolved_id = req.target
        else:
            resolved_assistant = registry.primary
            resolved_id = registry.ids[0]
        routing = getattr(request.app.state, "target_routing", None) or TargetRouting()
        # First use of this target: spawn its lazily-deferred servers (a non-primary scoped target's own
        # bridges) before narrowing, so this turn sees its tools. Awaited — the first turn pays the init;
        # single-flight per server, and a spawn failure just leaves the tool absent (same as startup).
        # Only when tools are on: use_tools off means an empty toolset, so there is nothing to spawn for.
        if req.use_tools:
            bridge = getattr(request.app.state, "mcp_bridge", None)
            if bridge is not None:
                await bridge.ensure_target(routing, resolved_id)
        toolset = narrow_to_servers(toolset, routing, resolved_id)
    # An explicit allow-list narrows the toolset to the tools the user left enabled (intersecting on
    # top of any target narrowing above).
    if req.enabled_tools is not None:
        toolset = toolset.subset(set(req.enabled_tools))

    # System prompt precedence: an explicit ``system_prompt`` from the client is
    # authoritative — including "" for *no* system prompt (a roleplay model then
    # runs with no assistant framing). Only when the field is absent (None) do we
    # fall back to the project (stabbur.toml) prompt. A system message already in
    # ``messages`` still wins (kept for API clients that inline their own).
    if req.system_prompt is not None:
        system_prompt = req.system_prompt
    else:
        system_prompt = getattr(request.app.state, "system_prompt", "") or ""
    messages = list(req.messages)
    if system_prompt and not (messages and messages[0].get("role") == "system"):
        messages = [{"role": "system", "content": system_prompt}, *messages]

    # Confirmation policy: an explicit request value wins; otherwise the resolved target's readonly
    # flag decides — a non-readonly target gates writes, while free-play / a readonly target stays
    # ungated ("none" = today's behavior: no confirm channel is wired at all).
    default_policy: Literal["all", "writes", "none"] = (
        "none" if (resolved_assistant is None or getattr(resolved_assistant, "readonly", True)) else "writes"
    )
    policy: Literal["all", "writes", "none"] = req.confirm_tools or default_policy

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

        def on_usage(usage: dict[str, Any]) -> None:
            # Sync sink (the agent calls it inline) — put_nowait, and drop it if the client is
            # too slow to drain: token accounting is a nicety, never worth stalling the stream.
            # One event per round, so a tool-using turn reports each round's usage.
            with suppress(asyncio.QueueFull):
                queue.put_nowait({"type": "usage", "usage": usage})

        async def on_confirm(name: str, args: dict[str, Any]) -> bool:
            # Mint an unguessable id, register a future, and stream a "confirm" event; the client
            # resolves it via POST /api/chat/confirm. Fail-safe: a timeout (or a cancelled stream)
            # denies. The id is always popped and a "confirm_resolved" event emitted in `finally`.
            cid = uuid4().hex
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bool] = loop.create_future()
            request.app.state.pending_confirmations[cid] = fut
            await queue.put({"type": "confirm", "id": cid, "tool": name, "args": _cap_strings(args)})
            reason = "user"
            try:
                return await asyncio.wait_for(fut, timeout=request.app.state.settings.confirm_timeout)
            except asyncio.TimeoutError:
                reason = "timeout"
                return False
            finally:
                request.app.state.pending_confirmations.pop(cid, None)
                approved = fut.done() and not fut.cancelled() and fut.result() is True
                # put_nowait, not await: this runs in `finally`, including during a cancellation unwind
                # (client disconnect) when the queue consumer has already stopped draining — an awaited
                # put could then block the cancellation. The 512-slot queue has ample headroom, so this
                # best-effort resolved-signal virtually always lands; a full queue just drops it.
                with suppress(asyncio.QueueFull):
                    queue.put_nowait({"type": "confirm_resolved", "id": cid, "approved": approved, "reason": reason})

        # Reserve the runtime for the whole stream so a load/unload can't swap or kill
        # it mid-generation; read the current model/URL *inside* the reservation.
        async with _reserve_runtime(request):
            current = manager.current
            if current is None:  # swapped out in the race window before we reserved
                yield f"data: {json.dumps({'type': 'error', 'detail': 'No model loaded'})}\n\n"
                yield 'data: {"type": "done"}\n\n'
                return
            base = manager.base_url
            if isinstance(current, UpstreamModel):
                # Remote model: the OpenAI ``model`` field is the remote's id (a router-mode
                # server selects — and hot-swaps — by it), vision comes from the listing's
                # modalities, and sampling falls back to the anti-loop default (nothing else
                # is knowable without a local copy).
                model_field: str | None = current.name
                model_vision = current.vision
                rec = sampling.defaults()
            else:
                model_target = current.load_target
                try:
                    model_vision = capabilities.capabilities(current).vision  # feed tool images back only if seen
                except Exception:  # noqa: BLE001 - detection is best-effort; a failure just disables image feedback
                    model_vision = False
                # Model-recommended sampling (LM Studio parity); an explicit request value wins.
                rec = sampling.recommended(current)
                model_field = str(model_target) if model_target else None
            eff_temperature = req.temperature if req.temperature is not None else rec.temperature
            eff_top_p = req.top_p if req.top_p is not None else rec.top_p
            eff_top_k = req.top_k if req.top_k is not None else rec.top_k
            eff_min_p = req.min_p if req.min_p is not None else rec.min_p
            eff_repeat_penalty = req.repeat_penalty if req.repeat_penalty is not None else rec.repeat_penalty
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
                        on_usage=on_usage,
                        temperature=eff_temperature,
                        top_p=eff_top_p,
                        top_k=eff_top_k,
                        min_p=eff_min_p,
                        repeat_penalty=eff_repeat_penalty,
                        # mlx-vlm requires the OpenAI ``model`` field match what it loaded (the
                        # launch path); a remote router selects by it; harmless for llama-server
                        # and mlx-lm, which ignore it.
                        model=model_field,
                        vision=model_vision,
                        on_confirm=(on_confirm if policy != "none" else None),
                        confirm_policy=policy,
                        reasoning=req.reasoning,
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


class ConfirmRequest(BaseModel):
    """Resolution for a pending /api/chat write-confirmation."""

    id: str
    approve: bool


@router.post("/api/chat/confirm")
async def chat_confirm(req: ConfirmRequest, request: Request) -> dict[str, bool]:
    """Resolve a pending write-confirmation for the streaming agent loop.

    The ``id`` is an unguessable server-minted uuid delivered only over the SSE stream (never
    listed), so possessing it is the authorization to answer that specific gate; this route also
    inherits the app-level cross-site + bearer guard on ``/api``. Unknown or already-resolved ids
    (a double answer, or a stream that timed out / disconnected) 404 — the confirmation is gone.
    """
    fut = request.app.state.pending_confirmations.get(req.id)
    if fut is None or fut.done():
        raise HTTPException(status_code=404, detail="no pending confirmation")
    fut.set_result(req.approve)
    return {"ok": True}


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
    if isinstance(manager, UpstreamManager):
        # Upstream mode: "loading" selects one of the remote's ids (matched exactly, case-
        # insensitively, or by basename); the remote itself loads it on the next request.
        # ``n_ctx`` is decided by the remote's own presets, so it is ignored here.
        try:
            async with lock:
                _reject_if_generating(request)  # don't repoint the backend under a live generation
                await asyncio.to_thread(manager.load_by_name, name)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await _status(manager, settings)
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
