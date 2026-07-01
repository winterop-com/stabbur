"""The tool-calling agent loop: model ⇄ tools over the OpenAI /v1 API.

The model may emit ``tool_call``s; kodo executes each via the MCP toolset, feeds
the results back, and repeats until the model answers with plain text.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx

from kodo.tools import MCPToolset

# Callback for surfacing tool activity to a UI: (kind, detail).
ToolEvent = Callable[[str, str], None]


async def run(
    base_url: str,
    messages: list[dict[str, Any]],
    toolset: MCPToolset,
    max_tokens: int | None = None,
    on_event: ToolEvent | None = None,
    max_rounds: int = 8,
) -> str:
    """Run the agent loop against ``base_url`` and return the final reply text.

    ``messages`` is mutated in place with the assistant/tool turns (so a REPL can
    keep the conversation). ``on_event("call"|"result", detail)`` reports tool
    activity. Bounded by ``max_rounds`` to avoid runaway tool loops.
    """
    async with httpx.AsyncClient(timeout=600) as http:
        for _ in range(max_rounds):
            body: dict[str, Any] = {"messages": messages, "tools": toolset.schemas, "tool_choice": "auto"}
            if max_tokens is not None:
                body["max_tokens"] = max_tokens
            resp = await http.post(f"{base_url}/v1/chat/completions", json=body)
            resp.raise_for_status()
            message = resp.json()["choices"][0]["message"]
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                return message.get("content") or ""

            messages.append(message)  # assistant turn carrying the tool_calls
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if on_event:
                    on_event("call", f"{name}({raw_args})")
                try:
                    result = await toolset.call(name, args)
                except Exception as exc:  # noqa: BLE001 - report tool failures back to the model
                    result = f"error: {exc}"
                if on_event:
                    on_event("result", result)
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})

    return "[agent stopped: too many tool rounds]"
