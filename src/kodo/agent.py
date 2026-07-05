"""The tool-calling agent loop: model ⇄ tools over the OpenAI /v1 API.

The model may emit ``tool_call``s; kodo executes each via the MCP toolset, feeds
the results back, and repeats until the model answers with plain text.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx

from kodo.tools import MCPToolset

# Callbacks: on_event(kind, detail) for tool activity; on_token(text) for streamed reply.
ToolEvent = Callable[[str, str], None]
TokenSink = Callable[[str], None]
# on_usage(usage) receives the server's token accounting for a turn (OpenAI `usage`:
# prompt_tokens / completion_tokens / total_tokens) so a REPL can show context used.
UsageSink = Callable[[dict[str, Any]], None]


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


async def _stream_turn(
    http: httpx.AsyncClient,
    base_url: str,
    body: dict[str, Any],
    on_token: TokenSink | None,
    on_reasoning: TokenSink | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Stream one completion; return (content, tool_calls, usage). Emits content + reasoning live."""
    content = ""
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, Any] | None = None
    async with http.stream("POST", f"{base_url}/v1/chat/completions", json=body) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            # With include_usage the final chunk carries `usage` and an empty
            # `choices` list; capture it and skip the (missing) delta.
            if chunk.get("usage"):
                usage = chunk["usage"]
            if not chunk.get("choices"):
                continue
            delta = chunk["choices"][0]["delta"]
            if delta.get("content"):
                content += delta["content"]
                if on_token:
                    on_token(delta["content"])
            # Reasoning models (gemma-4, Qwen3.5, …) stream their thinking here, not in
            # content; surface it separately instead of dropping it (→ blank replies).
            if delta.get("reasoning_content") and on_reasoning:
                on_reasoning(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc["index"], {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
    ordered = [calls[i] for i in sorted(calls)]
    return content, ordered, usage


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
    ``None`` (default) reads ``KODO_TOOL_TIMEOUT`` (120s; set 0 to disable the bound).
    """
    if tool_timeout is None:
        from kodo.config import get_settings  # noqa: PLC0415 - lazy to keep agent import light

        tool_timeout = get_settings().tool_timeout or None  # 0 → no bound
    async with httpx.AsyncClient(timeout=600) as http:
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
            content, calls, usage = await _stream_turn(http, base_url, body, on_token, on_reasoning)
            if usage and on_usage:
                on_usage(usage)
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
            for c in calls:
                try:
                    args = json.loads(c["args"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if on_event:
                    on_event("call", f"{c['name']}({c['args']})")
                try:
                    result = await toolset.call(c["name"], args, timeout=tool_timeout)
                except Exception as exc:  # noqa: BLE001 - report tool failures (incl. timeout) back to the model
                    result = f"error: {exc}"
                if on_event:
                    on_event("result", result)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})

    # Ran out of tool rounds: surface a terminal message the same way a normal
    # reply is delivered — stream it (so streaming clients, incl. the web UI whose
    # /api/chat discards the return value, actually see it) and record it in history.
    stopped = "[agent stopped: too many tool rounds]"
    if on_token:
        on_token(stopped)
    messages.append({"role": "assistant", "content": stopped})
    return stopped
