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


async def _stream_turn(
    http: httpx.AsyncClient,
    base_url: str,
    body: dict[str, Any],
    on_token: TokenSink | None,
    on_reasoning: TokenSink | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Stream one completion; return (content, tool_calls). Emits content + reasoning live."""
    content = ""
    calls: dict[int, dict[str, str]] = {}
    async with http.stream("POST", f"{base_url}/v1/chat/completions", json=body) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            delta = json.loads(payload)["choices"][0]["delta"]
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
    return content, ordered


async def run(
    base_url: str,
    messages: list[dict[str, Any]],
    toolset: MCPToolset,
    max_tokens: int | None = None,
    on_event: ToolEvent | None = None,
    on_token: TokenSink | None = None,
    on_reasoning: TokenSink | None = None,
    max_rounds: int = 8,
) -> str:
    """Run the agent loop against ``base_url``, streaming the reply; return its text.

    ``messages`` is mutated in place (assistant/tool turns) so a REPL keeps the
    conversation. ``on_event`` reports tool activity; ``on_token`` receives the
    final reply's tokens; ``on_reasoning`` receives a reasoning model's thinking
    tokens (separate channel). Bounded by ``max_rounds``.
    """
    async with httpx.AsyncClient(timeout=600) as http:
        for _ in range(max_rounds):
            body: dict[str, Any] = {"messages": messages, "stream": True}
            # Omit tools entirely when there are none, so a no-tool chat is plain
            # completion (no --jinja tool parsing / buffering).
            if toolset.schemas:
                body["tools"] = toolset.schemas
                body["tool_choice"] = "auto"
            if max_tokens is not None:
                body["max_tokens"] = max_tokens
            content, calls = await _stream_turn(http, base_url, body, on_token, on_reasoning)
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
                    result = await toolset.call(c["name"], args)
                except Exception as exc:  # noqa: BLE001 - report tool failures back to the model
                    result = f"error: {exc}"
                if on_event:
                    on_event("result", result)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})

    return "[agent stopped: too many tool rounds]"
