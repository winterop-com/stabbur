"""The tool-calling agent loop: model ⇄ tools over the OpenAI /v1 API.

The model may emit ``tool_call``s; stabbur executes each via the MCP toolset, feeds
the results back, and repeats until the model answers with plain text.
"""

import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from inspect import isawaitable
from typing import Any, Literal

import httpx

from stabbur.tools import MCPToolset, ToolResult

# Prefaces the user message that carries images a tool returned (e.g. a screenshot), so a
# vision model reads them as its own multimodal input right after the tool results.
_TOOL_IMAGE_PREAMBLE = "Image(s) returned by the tool call(s) above:"

# Cap on the text a tool result feeds back to the model, matching the page-action cap
# (``pageactions._MAX_RESULT``) — this is the loop-wide backstop for every *other* tool result.
# An MCP tool's output is unbounded (a whole file, a long HTTP body, a directory listing), and
# unlike an SSE detail — which is only ever *displayed*, and is capped separately — this text is
# spent from the model's context window, where overflowing it fails the whole turn rather than
# one tool call. Generous enough that a normal result arrives whole, and the marker says plainly
# that something was cut so the model doesn't read a severed result as the complete one.
_MAX_TOOL_RESULT = 50_000
_TOOL_TRUNCATED = "\n[truncated: tool result exceeded the size a single tool result may return]"

# Callbacks: on_event(kind, detail) for tool activity; on_token(text) for streamed reply.
# Sinks may be sync (TUI/CLI append to a buffer) or async (the /api/chat SSE path uses an
# async sink so a full, bounded queue back-pressures generation instead of buffering the
# whole reply in memory). ``_emit`` awaits the result only when it's awaitable.
ToolEvent = Callable[[str, str], None | Awaitable[None]]
TokenSink = Callable[[str], None | Awaitable[None]]
# on_usage(usage) receives the server's token accounting for a turn (OpenAI `usage`:
# prompt_tokens / completion_tokens / total_tokens) so a REPL can show context used.
UsageSink = Callable[[dict[str, Any]], None]
# on_confirm(name, args) is consulted before a gated tool call runs; it returns whether the user
# approved the action. Async-only (the /api/chat + UI path awaits a user decision over a channel),
# so the loop can suspend on it — a missing sink is treated as a denial (fail-safe) at the gate.
ConfirmSink = Callable[[str, dict[str, Any]], Awaitable[bool]]
# on_finish(reason) receives the OpenAI ``finish_reason`` of each round as the round ends
# ("stop", "length", "tool_calls", ...). Sync; the last call is the one that describes how the
# turn actually ended. Without it a length-capped reply is indistinguishable from a short one.
FinishSink = Callable[[str], None]


def _needs_confirm(policy: Literal["all", "writes", "none"], toolset: MCPToolset, name: str) -> bool:
    """Whether a tool call must be confirmed under ``policy``.

    ``"all"`` gates every call; ``"writes"`` gates only tools not known read-only (fail-safe —
    an unknown/unannotated tool is treated as a write); anything else (``"none"``) gates nothing.
    """
    if policy == "all":
        return True
    if policy == "writes":
        return not toolset.is_readonly(name)
    return False


def _capped(text: str) -> str:
    """A tool result's text, cut to ``_MAX_TOOL_RESULT`` with an explicit truncation marker.

    Applied to every tool result the loop feeds back — MCP, page action, and the loop's own error
    strings alike — so one oversized result can't consume the whole context window. A page action
    caps its own text first (same size), so re-capping one is a no-op in substance: its marker is
    simply replaced by this one.
    """
    return text if len(text) <= _MAX_TOOL_RESULT else text[:_MAX_TOOL_RESULT] + _TOOL_TRUNCATED


def _exc_text(exc: BaseException) -> str:
    """Describe an exception for the model, never as an empty string.

    A whole class of failures a tool call can hit carries no message at all — a bare
    ``TimeoutError``, a closed anyio stream — so ``f"{exc}"`` alone produced a bare ``error:``. The
    model then retried blind against a failure it had been told nothing about. The type name is
    the only fact those exceptions have, so it stands in when there is no message.
    """
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


async def _emit(sink: Callable[..., None | Awaitable[None]] | None, *args: Any) -> None:
    """Call an optional sync-or-async sink, awaiting it when it returns an awaitable."""
    if sink is None:
        return
    result = sink(*args)
    if isawaitable(result):
        await result


def _audio_part(data_url: str) -> dict[str, Any]:
    """Convert an audio ``data:`` URL to an OpenAI ``input_audio`` content part.

    llama-server / mlx-vlm want ``{data: <base64 (no prefix)>, format: "wav"|"mp3"}``
    (not a data URL like images), so split the mime + payload out.
    """
    fmt = "wav"
    b64 = data_url
    if data_url.startswith("data:"):
        header, _, b64 = data_url.partition(",")
        mime = header[len("data:") :].split(";")[0]  # e.g. audio/wav
        subtype = mime.split("/")[-1] or "wav"
        fmt = {"mpeg": "mp3", "x-wav": "wav", "wave": "wav"}.get(subtype, subtype)
    return {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}


def user_content(
    text: str, images: list[str] | None = None, audios: list[str] | None = None
) -> str | list[dict[str, Any]]:
    """Build a user message's content: plain text, or OpenAI multimodal parts.

    ``images`` / ``audios`` are ``data:`` URL strings. With none, returns the plain
    string (backward compatible); otherwise a ``content`` array of an optional text
    part followed by ``image_url`` and ``input_audio`` parts — the format both
    llama-server (with ``--mmproj``) and mlx-vlm accept.
    """
    if not images and not audios:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts += [{"type": "image_url", "image_url": {"url": u}} for u in images or []]
    parts += [_audio_part(a) for a in audios or []]
    return parts


_SYNTHETIC_ID = "stabbur-call-"
"""Prefix for a tool-call id stabbur mints because the server streamed none."""


def _slot_for(index: object, call_id: str, name: str, calls: dict[int, dict[str, str]], by_id: dict[str, int]) -> int:
    """Which accumulating slot a ``tool_calls`` delta belongs to.

    ``index`` is how OpenAI's streaming format identifies which call a delta continues, and it is
    what the parser used to key on unconditionally — a ``KeyError`` that killed the whole stream
    for any compatible server that omits it. The fallbacks, in order:

    - a repeated ``id`` continues the call it already named;
    - a delta bringing a *new* id or a function name starts a new call (that is what "new call"
      looks like in every dialect);
    - anything else is an arguments-only fragment, which continues the call in flight — the shape
      a server that streams incrementally but omits ``index`` produces.
    """
    if isinstance(index, int) and not isinstance(index, bool):
        return index
    if call_id and call_id in by_id:
        return by_id[call_id]
    if calls and not call_id and not name:
        return max(calls)  # arguments-only: keep filling the most recent call
    return max(calls) + 1 if calls else 0


def _merge_tool_call(tc: object, calls: dict[int, dict[str, str]], by_id: dict[str, int]) -> None:
    """Fold one streamed ``tool_calls`` delta into the accumulating call slots.

    Every field is read defensively: a delta of the wrong shape must degrade to a tool call the
    loop then fails and reports back to the model (the existing malformed-call path, which the
    model can retry from), never to an exception that ends the generation.
    """
    if not isinstance(tc, dict):
        return
    raw_id = tc.get("id")
    call_id = raw_id if isinstance(raw_id, str) else ""
    fn = tc.get("function")
    fn = fn if isinstance(fn, dict) else {}
    raw_name, raw_args = fn.get("name"), fn.get("arguments")
    name = raw_name if isinstance(raw_name, str) else ""
    args = raw_args if isinstance(raw_args, str) else ""
    if not (call_id or name or args):
        return  # an empty delta must not conjure a phantom tool call out of a plain text reply
    slot_index = _slot_for(tc.get("index"), call_id, name, calls, by_id)
    slot = calls.setdefault(slot_index, {"id": "", "name": "", "args": ""})
    if call_id:
        slot["id"] = call_id
        by_id[call_id] = slot_index
    if name:
        slot["name"] = name
    slot["args"] += args


def _mint_missing_ids(calls: list[dict[str, str]]) -> None:
    """Give every parsed call a unique id, minting one where the server streamed none.

    The id is the only thing that ties a ``tool`` result message back to the call it answers. Left
    empty — as a server that omits ``id`` leaves them — two calls in one round both came back as
    ``tool_call_id: ""``: two answers the model cannot tell apart, which is worse than a failure
    because it looks like it worked. Minted ids are checked against the ones the server did send,
    so a synthetic id can never shadow a real one.
    """
    used = {call["id"] for call in calls if call["id"]}
    n = 0
    for call in calls:
        if call["id"]:
            continue
        while f"{_SYNTHETIC_ID}{n}" in used:
            n += 1
        call["id"] = f"{_SYNTHETIC_ID}{n}"
        used.add(call["id"])
        n += 1


async def _stream_turn(
    http: httpx.AsyncClient,
    base_url: str,
    body: dict[str, Any],
    on_token: TokenSink | None,
    on_reasoning: TokenSink | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None, str | None]:
    """Stream one completion; return (content, tool_calls, usage, finish_reason).

    Emits content + reasoning live. ``finish_reason`` is the last one the stream reported —
    ``"stop"`` for a complete answer, ``"tool_calls"`` for a round that ends in tool calls, and
    ``"length"`` when the runtime cut the reply at ``max_tokens``. It is the only signal that
    distinguishes a finished reply from a truncated one, so it is carried out rather than dropped:
    a reply whose whole budget went to ``reasoning_content`` emits no content deltas at all, and
    without this reads as a model that simply answered nothing.
    """
    content = ""
    calls: dict[int, dict[str, str]] = {}  # slot -> the call accumulating in it
    by_id: dict[str, int] = {}  # call id -> its slot, for deltas that identify by id alone
    usage: dict[str, Any] | None = None
    finish: str | None = None
    async with http.stream("POST", f"{base_url}/v1/chat/completions", json=body) as resp:
        if resp.status_code >= 400:
            # Read the body on error — llama-server puts the real cause (e.g. context overflow)
            # in the JSON detail, which raise_for_status alone would discard.
            detail = (await resp.aread()).decode("utf-8", errors="replace").strip()[:500]
            raise httpx.HTTPStatusError(
                f"runtime returned {resp.status_code}: {detail}" if detail else f"runtime returned {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                # A frame that is not JSON is one non-conforming server's noise, not a reason to
                # abandon a generation that is otherwise streaming fine. Skipping costs one delta.
                continue
            if not isinstance(chunk, dict):
                continue
            # With include_usage the final chunk carries `usage` and an empty
            # `choices` list; capture it and skip the (missing) delta.
            if chunk.get("usage"):
                captured = chunk["usage"]
                usage = dict(captured) if isinstance(captured, dict) else {}
                # llama.cpp adds its own `timings` (prompt_ms, predicted_ms,
                # predicted_per_second) to that chunk. Pass them through: the runtime's
                # measurement of its own decode rate beats anything a client can infer
                # from arrival times. Absent on other servers, hence the guard.
                timings = chunk.get("timings")
                if isinstance(timings, dict):
                    usage["timings"] = timings
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue
            choice = choices[0]
            # Only the final chunk of a choice carries it; every earlier one sends null, so
            # overwrite on truthy values rather than assigning unconditionally.
            if isinstance(choice.get("finish_reason"), str) and choice["finish_reason"]:
                finish = choice["finish_reason"]
            # ``.get``, since the chunk that carries finish_reason may carry nothing else.
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            text = delta.get("content")
            if isinstance(text, str) and text:
                content += text
                await _emit(on_token, text)
            # Reasoning models (gemma-4, Qwen3.5, …) stream their thinking here, not in
            # content; surface it separately instead of dropping it (→ blank replies).
            thinking = delta.get("reasoning_content")
            if isinstance(thinking, str) and thinking and on_reasoning:
                await _emit(on_reasoning, thinking)
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    _merge_tool_call(tc, calls, by_id)
    ordered = [calls[i] for i in sorted(calls)]
    _mint_missing_ids(ordered)
    return content, ordered, usage, finish


ReasoningLevel = Literal["off", "low", "medium", "high", "max"]
"""Reasoning-effort levels for thinking models, mirroring the llama.cpp webui's control."""

_REASONING_BUDGETS: dict[str, int] = {"low": 512, "medium": 2048, "high": 8192}


def reasoning_fields(level: "ReasoningLevel | None") -> dict[str, Any]:
    """The request fields for a reasoning level; empty for ``None`` (the model default).

    Speaks llama-server's dialect — exactly what its own webui sends: ``chat_template_kwargs.
    enable_thinking`` toggles thinking in the chat template (Qwen-style models), ``thinking_
    budget_tokens`` caps the thinking length (low 512 / medium 2048 / high 8192; ``max`` sends
    no cap), and ``reasoning_control`` marks the request as reasoning-managed. Servers without
    reasoning support ignore the unknown fields, so sending them is always safe.
    """
    if level is None:
        return {}
    fields: dict[str, Any] = {
        "chat_template_kwargs": {"enable_thinking": level != "off"},
        "reasoning_control": True,
    }
    if level in _REASONING_BUDGETS:
        fields["thinking_budget_tokens"] = _REASONING_BUDGETS[level]
    return fields


async def run(
    base_url: str,
    messages: list[dict[str, Any]],
    toolset: MCPToolset,
    max_tokens: int | None = None,
    on_event: ToolEvent | None = None,
    on_token: TokenSink | None = None,
    on_reasoning: TokenSink | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    min_p: float | None = None,
    repeat_penalty: float | None = None,
    model: str | None = None,
    max_rounds: int = 8,
    on_usage: UsageSink | None = None,
    tool_timeout: float | None = None,
    vision: bool = False,
    on_confirm: ConfirmSink | None = None,
    confirm_policy: Literal["all", "writes", "none"] = "none",
    reasoning: "ReasoningLevel | None" = None,
    response_format: dict[str, Any] | None = None,
    on_finish: FinishSink | None = None,
    http: httpx.AsyncClient | None = None,
) -> str:
    """Run the agent loop against ``base_url``, streaming the reply; return its text.

    ``messages`` is mutated in place (assistant/tool turns) so a REPL keeps the
    conversation. ``on_event`` reports tool activity; ``on_token`` receives the
    final reply's tokens; ``on_reasoning`` receives a reasoning model's thinking
    tokens (separate channel); ``on_usage`` receives the server's token accounting
    (prompt/completion/total) after each round. ``model`` is sent as the OpenAI
    ``model`` field — required by mlx-vlm (which 422s without it), ignored by
    llama-server/mlx-lm. Bounded by ``max_rounds``; each tool call is bounded by
    ``tool_timeout`` seconds so a hung MCP server can't stall the loop forever —
    ``None`` (default) reads ``STABBUR_TOOL_TIMEOUT`` (120s; set 0 to disable the bound).
    ``vision`` is set when the model can see images: an image a tool returns (e.g. a
    screenshot) is then fed back as a follow-up user image message so the model reads it
    as multimodal input; a text-only model instead gets a note that an image was returned.
    ``confirm_policy`` gates tool execution behind ``on_confirm``: ``"none"`` (default) runs
    every tool as before; ``"writes"`` confirms only tools not known read-only (via each tool's
    ``readOnlyHint`` annotation — an unannotated tool is treated as a write); ``"all"`` confirms
    every call. When a call is gated, ``on_confirm(name, args)`` is awaited for approval; if it
    returns falsy (or no ``on_confirm`` is supplied — fail-safe deny), the tool is NOT run and the
    model gets a ``tool`` turn whose text is exactly ``error: user declined this action``.
    ``on_finish`` receives each round's OpenAI ``finish_reason``, so a caller can tell a complete
    reply from one the runtime cut off at ``max_tokens``.

    ``http`` lets a long-lived caller pass its own client so the loop reuses one connection pool
    (the server passes ``app.state.http``, the same client the ``/v1`` proxy uses); ``None`` opens
    a private one for the call, which is what the CLI and TUI want — they have no app to borrow
    from. A passed client is never closed here: it belongs to whoever opened it.
    """
    if tool_timeout is None:
        from stabbur.config import get_settings  # noqa: PLC0415 - lazy to keep agent import light

        tool_timeout = get_settings().tool_timeout or None  # 0 → no bound
    async with AsyncExitStack() as stack:
        if http is None:
            http = await stack.enter_async_context(httpx.AsyncClient(timeout=600))
        for _ in range(max_rounds):
            body: dict[str, Any] = {"messages": messages, "stream": True}
            # Ask for a final usage chunk (prompt/completion tokens) so callers can
            # report real context consumption; runtimes that ignore it just omit it.
            if on_usage is not None:
                body["stream_options"] = {"include_usage": True}
            if model is not None:
                body["model"] = model
            # Omit tools entirely when there are none, so a no-tool chat is plain
            # completion (no --jinja tool parsing / buffering).
            if toolset.schemas:
                body["tools"] = toolset.schemas
                body["tool_choice"] = "auto"
            if max_tokens is not None:
                body["max_tokens"] = max_tokens
            if temperature is not None:
                body["temperature"] = temperature
            if top_p is not None:
                body["top_p"] = top_p
            # top_k / min_p / repeat_penalty are OpenAI extensions supported by
            # llama-server and the MLX servers; unknown ones are ignored upstream.
            if top_k is not None:
                body["top_k"] = top_k
            if min_p is not None:
                body["min_p"] = min_p
            if repeat_penalty is not None:
                body["repeat_penalty"] = repeat_penalty
            # Structured output. Constrains the reply to a JSON schema (OpenAI's
            # ``response_format``). NOT combinable with tools: llama-server builds one grammar
            # per request and rejects the pair with 400 "failed to parse grammar", so callers
            # are stopped before they get there — see the check in the /api/chat route.
            if response_format is not None:
                body["response_format"] = response_format
            # Reasoning effort (thinking on/off + budget) — llama-server dialect, see reasoning_fields.
            body.update(reasoning_fields(reasoning))
            content, calls, usage, finish = await _stream_turn(http, base_url, body, on_token, on_reasoning)
            if usage and on_usage:
                on_usage(usage)
            if finish and on_finish:
                on_finish(finish)
            if not calls:
                messages.append({"role": "assistant", "content": content})
                return content

            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"]}}
                        for c in calls
                    ],
                }
            )
            round_images: list[str] = []  # images tools returned this round (fed back below)
            for c in calls:
                await _emit(on_event, "call", f"{c['name']}({c['args']})")
                try:
                    args = json.loads(c["args"] or "{}")
                except json.JSONDecodeError as exc:
                    # Don't run the tool with empty args on unparseable JSON — for a tool with all
                    # optional params that silently returns a plausible-but-wrong result. Feed the
                    # parse error back so the model resends valid arguments.
                    result = ToolResult(
                        text=f"error: could not parse tool arguments as JSON ({exc}); resend valid JSON."
                    )
                else:
                    if _needs_confirm(confirm_policy, toolset, c["name"]):
                        # Gated action: no confirmation channel means deny (fail-safe); otherwise ask.
                        approved = await on_confirm(c["name"], args) if on_confirm is not None else False
                    else:
                        approved = True
                    if not approved:
                        # Declined: skip the side-effecting call but still give the model a tool turn
                        # (mirroring the error-branch shape) so the loop continues with a clear signal.
                        result = ToolResult(text="error: user declined this action")
                    else:
                        try:
                            result = await toolset.call(c["name"], args, timeout=tool_timeout)
                        except Exception as exc:  # noqa: BLE001 - report tool failures (incl. timeout) to the model
                            result = ToolResult(text=f"error: {_exc_text(exc)}")
                display = result.text + (f"  [+{len(result.images)} image(s)]" if result.images else "")
                await _emit(on_event, "result", display)
                content = _capped(result.text)
                if result.images and vision:
                    # Feed the pixels back below; leave the tool message a short marker so the
                    # tool_call_id still has content and the model knows where the image came from.
                    round_images.extend(result.images)
                    content = content or "[image returned by the tool; shown in the next message]"
                elif result.images:
                    # Text-only model: it can't see the image, so say so rather than drop it silently
                    # (a screenshot vanishing makes a vision-less model hallucinate what it "saw").
                    note = "[a tool returned an image, but this model cannot view images]"
                    content = f"{content}\n{note}" if content else note
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})

            # A vision model reads tool-returned images as its own input: deliver them in a user
            # message right after the tool results (the exercised multimodal path — an image_url
            # part in a tool message isn't understood by llama-server/mlx-vlm).
            if round_images:
                messages.append({"role": "user", "content": user_content(_TOOL_IMAGE_PREAMBLE, images=round_images)})

    # Ran out of tool rounds: surface a terminal message the same way a normal
    # reply is delivered — stream it (so streaming clients, incl. the web UI whose
    # /api/chat discards the return value, actually see it) and record it in history.
    stopped = "[agent stopped: too many tool rounds]"
    # _emit, not a bare call: the /api/chat sink is async (queue.put) — calling it
    # unawaited would silently drop the message for exactly the clients it's for.
    await _emit(on_token, stopped)
    messages.append({"role": "assistant", "content": stopped})
    return stopped
