"""MCP client: connect to an MCP server and expose its tools to a model.

heim is the MCP *client* — it spawns an MCP server (over stdio), lists its tools
as OpenAI function schemas for the model, and executes ``tool_call``s against it.
"""

import dataclasses
import datetime
import enum
import json
import os
import re
import shutil
import sys
from collections.abc import AsyncGenerator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from heim.mcpservers import McpServer
    from heim.targets import AssistantRegistry

_NAME_STRIP = re.compile(r"^(heim-mcp-|mcp-server-|mcp-)")


def _bin_dir() -> str:
    """The running interpreter's directory — where heim's bundled ``heim-mcp-*`` scripts live.

    Not necessarily on PATH (a ``uv tool install``ed heim exposes only the ``heim`` symlink),
    so we add it explicitly when spawning bundled servers.
    """
    return str(Path(sys.executable).parent)


def _mcp_env() -> dict[str, str]:
    """Environment for a spawned MCP server, with heim's own bin/ prepended to PATH.

    So bundled ``heim-mcp-*`` scripts (and tools they call) resolve regardless of how heim ran.
    """
    env = dict(os.environ)
    env["PATH"] = _bin_dir() + os.pathsep + env.get("PATH", "")
    return env


def _resolve_command(cmd: str) -> str:
    """Resolve a bare command to an absolute path, searching heim's own bin/ too.

    subprocess resolves a bare executable name against the *parent's* PATH, so putting heim's
    bin/ only in the child env isn't enough — resolve it here. A command already found on PATH
    (or absolute) is returned as-is; an unfound one is passed through unchanged.
    """
    return shutil.which(cmd, path=os.environ.get("PATH", "") + os.pathsep + _bin_dir()) or cmd


def _slug(text: str) -> str:
    """Identifier slug: runs of non-alphanumerics → single underscores, trimmed."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")


def _default_name(command: list[str]) -> str:
    """Short server prefix from its launch command (e.g. heim-mcp-datetime → datetime)."""
    base = Path(command[0]).name if command else "mcp"
    return _slug(_NAME_STRIP.sub("", base)) or "mcp"


def _server_prefix(name: str | None, command: list[str]) -> str:
    """The tool namespace for a server: its manifest ``name`` if set, else derived."""
    if name:
        return _slug(name) or _default_name(command)
    return _default_name(command)


def _openai_schema(tool: Any) -> dict[str, Any]:
    """Convert one MCP tool to an OpenAI ``tools`` entry."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


def _structured_payload(result: Any) -> Any | None:
    """MCP structuredContent dict -> synthesized-dataclass asdict -> dict/list data; else None."""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    data = getattr(result, "data", None)
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        return dataclasses.asdict(data)
    if isinstance(data, (dict, list)):
        return data
    return None


def _json_default(value: Any) -> Any:
    """JSON fallback for values the encoder can't natively serialize.

    ``datetime``/``date`` -> ISO 8601 (stable + sortable), ``Enum`` -> its ``.value``, everything else
    -> ``str()``. A plain ``default=str`` yields a non-ISO, value-dependent datetime shape and a
    ``ClassName.MEMBER`` enum repr, so the shapes are pinned here instead.
    """
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return str(value)


def _result_text(result: Any) -> str:
    """Best-effort display text from a FastMCP call result.

    The decision ladder, in order:

    1. **Bare scalar** ``data`` (str/int/float/bool) -> ``str(data)`` verbatim. This must run
       first: fastmcp wraps a bare-string tool return as ``structured_content == {"result": ...}``
       while ``data`` stays the raw scalar, so a scalar guard *before* :func:`_structured_payload`
       is the only thing that keeps a plain ``"ok"`` / ``"Wednesday"`` return from being JSON-wrapped.
    2. **Structured payload** (``structuredContent`` / synthesized dataclass / dict-or-list data)
       -> a compact JSON dump, so the model and SSE get JSON rather than a ``Root(exit_code=0, ...)``
       repr wall.
    3. Fallbacks: ``str(data)`` when data is some other non-None object, then joined content text
       parts, then ``str(result)``.
    """
    data = getattr(result, "data", None)
    if isinstance(data, (str, int, float, bool)):
        return str(data)
    payload = _structured_payload(result)
    if payload is not None:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    if data is not None:
        return str(data)
    parts = [c.text for c in getattr(result, "content", []) if getattr(c, "text", None)]
    return "\n".join(parts) or str(result)


class ToolResult(BaseModel):
    """A tool call's result: its text plus any images it returned (as ``data:`` URLs).

    An MCP tool may return image content — e.g. ``browser_take_screenshot`` returns a PNG.
    Keeping the images (not just the text) lets the agent loop feed them back to a vision
    model as image parts, so the model actually *sees* what the tool produced.
    """

    text: str = ""
    images: list[str] = []  # data:<mime>;base64,<...> URLs, one per image content block


def _result_content(result: Any) -> ToolResult:
    """Extract text (unchanged from :func:`_result_text`) plus any image blocks from a result."""
    images = [
        f"data:{getattr(c, 'mimeType', None) or 'image/png'};base64,{c.data}"
        for c in getattr(result, "content", []) or []
        if getattr(c, "type", None) == "image" and getattr(c, "data", None)
    ]
    return ToolResult(text=_result_text(result), images=images)


class MCPToolset:
    """Aggregated tools across one or more MCP servers.

    Tool names are namespaced by server (``<server>__<tool>``) so tools from
    different servers never collide and stay unambiguous to the model and the UI;
    ``call`` strips the prefix and routes to the owning server.
    """

    def __init__(self) -> None:
        self.schemas: list[dict[str, Any]] = []
        self._owner: dict[str, tuple[Client, str]] = {}  # qualified name → (client, tool name)
        self._readonly: dict[str, bool] = {}  # qualified name → is-known-read-only (fail-safe False)
        self.errors: list[tuple[str, str]] = []  # (server label, error) for servers that failed to start

    async def add(self, client: Client, prefix: str) -> None:
        """Register a server's tools under ``<prefix>__<tool>`` (skip duplicates)."""
        for tool in await client.list_tools():
            qualified = f"{prefix}__{tool.name}"
            if qualified in self._owner:
                continue
            schema = _openai_schema(tool)
            schema["function"]["name"] = qualified
            self.schemas.append(schema)
            self._owner[qualified] = (client, tool.name)
            # readOnlyHint (MCP ToolAnnotations) drives the confirmation gate. None/False/missing all
            # map to False = "NOT known read-only" = treat as a write that needs confirmation (fail-safe);
            # only an explicit ``readOnlyHint == True`` yields True.
            self._readonly[qualified] = bool(getattr(getattr(tool, "annotations", None), "readOnlyHint", None))

    @property
    def names(self) -> list[str]:
        """Names of the available (namespaced) tools."""
        return [s["function"]["name"] for s in self.schemas]

    def prefixes(self) -> set[str]:
        """The server prefixes present in this toolset (the ``<prefix>`` of each ``<prefix>__<tool>``).

        Lets a caller reason about tools *per server* (e.g. registry-target routing) without re-parsing
        the ``__`` naming convention itself — the one place that knows the convention stays :meth:`add`.
        """
        return {name.split("__", 1)[0] for name in self._owner}

    def names_for_prefixes(self, prefixes: set[str]) -> set[str]:
        """Qualified tool names whose server prefix is in ``prefixes`` — a ready :meth:`subset` argument."""
        return {name for name in self._owner if name.split("__", 1)[0] in prefixes}

    def is_readonly(self, name: str) -> bool:
        """Whether a namespaced tool is known read-only (its ``readOnlyHint`` annotation was True).

        Fail-safe: an unknown tool, or one with no annotation, returns ``False`` = treat it as a
        write that needs confirmation. Only an explicit ``readOnlyHint == True`` (recorded in
        :meth:`add`) yields ``True``, so a missing/ambiguous hint never silently skips the gate.
        """
        return self._readonly.get(name, False)

    def subset(self, names: set[str]) -> "MCPToolset":
        """A view restricted to ``names`` — for both display *and* execution.

        Routing (``_owner``) is filtered too, not just ``schemas``: a tool the user disabled must
        not run even if the model calls it from memory/hallucination — ``call()`` returns an
        "unknown tool" error for anything outside the subset (a real consent control, not display-only).
        The read-only annotations (``_readonly``) are filtered alongside so a narrowed view keeps the
        confirmation-gate metadata for the tools it keeps.
        """
        view = MCPToolset()
        view._owner = {k: v for k, v in self._owner.items() if k in names}
        view.schemas = [s for s in self.schemas if s["function"]["name"] in names]
        view._readonly = {k: v for k, v in self._readonly.items() if k in names}
        return view

    async def call(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> ToolResult:
        """Execute a namespaced tool on its owning server and return its text + any images.

        ``timeout`` (seconds) bounds the call so a hung server (e.g. an MCP tool that shells
        out to a command that never returns) can't stall the agent loop indefinitely — fastmcp
        raises on timeout, which the loop turns into an error fed back to the model.
        """
        entry = self._owner.get(name)
        if entry is None:
            return ToolResult(text=f"error: unknown tool {name!r}")
        client, tool_name = entry
        return _result_content(await client.call_tool(tool_name, arguments, timeout=timeout))

    async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
        """Execute a namespaced tool and return its **structured** payload (not display text).

        Unlike :meth:`call` (which returns text + images for the chat loop), this returns the
        MCP-spec ``structuredContent`` dict when the server provides one, else FastMCP's
        reconstructed ``result.data``, else the best-effort text — for callers that consume the
        result as data (e.g. the ``/api/assistant`` verify probe), not for rendering. The raw
        dict is preferred because ``result.data`` is a fastmcp-synthesized object (a dataclass,
        not a Pydantic model), which JSON-serializes as its repr string rather than as fields.
        An unknown tool raises :class:`KeyError` (the caller turns any failure into a state), so it
        never masquerades as a successful result the way :meth:`call`'s error string would.
        """
        entry = self._owner.get(name)
        if entry is None:
            raise KeyError(name)
        client, tool_name = entry
        result = await client.call_tool(tool_name, arguments, timeout=timeout)
        # Shared with _result_text: structuredContent dict -> synthesized-dataclass asdict -> dict/list data.
        # (fastmcp synthesizes dataclasses, which JSON-serialize as their repr, so we unwrap them here.)
        payload = _structured_payload(result)
        if payload is not None:
            return payload
        data = getattr(result, "data", None)
        return data if data is not None else _result_text(result)


class TargetRouting(BaseModel):
    """Per-target tool-prefix ownership, computed once at startup and consumed by :func:`narrow_to_servers`.

    ``explicit`` maps each registry target id to the set of tool *prefixes* it explicitly owns — its
    ``mcp_servers`` names slugged to the **exact** prefix :func:`connect` namespaces them under (via
    :func:`_server_prefix`, mirroring connect's duplicate-prefix suffixing). ``owns_all`` is the set of
    target ids that declared no ``mcp_servers`` (empty) and therefore own *every* server — the compat
    default. Keeping the explicit sets and the owns-all marker separate (rather than pre-expanding
    owns-all to every prefix) is what lets a scoped sibling still see the shared/global servers: an
    owns-all target contributes nothing to the "claimed" union, so unclaimed servers stay shared.
    """

    model_config = ConfigDict(frozen=True)

    explicit: dict[str, set[str]] = {}
    owns_all: set[str] = set()

    @property
    def empty(self) -> bool:
        """Whether no target is declared (free-play): narrowing is then a no-op."""
        return not self.explicit and not self.owns_all


def _prefix_by_name(resolved: "Sequence[McpServer]") -> dict[str, str]:
    """Map each resolved server's ``.mcp.json`` name to the tool prefix :func:`connect` gives it.

    Replays connect's assignment in the same order: the slug of a duplicate prefix is suffixed
    ``prefix2`` / ``prefix3`` (two *different* names can slug to the same prefix — e.g. ``a-b`` and
    ``a_b`` — and connect disambiguates the second, so this must too). Precedent: ``cli/project.py``.
    """
    used: dict[str, int] = {}
    mapping: dict[str, str] = {}
    for server in resolved:
        base = _server_prefix(server.name, [server.command, *server.args])
        n = used.get(base, 0)
        used[base] = n + 1
        mapping[server.name] = base if n == 0 else f"{base}{n + 1}"
    return mapping


def build_target_routing(resolved: "Sequence[McpServer]", registry: "AssistantRegistry") -> TargetRouting:
    """The per-target routing table for a registry against the resolved MCP servers.

    The one production builder shared by the serve lifespan (``app.state.target_routing``) and the CLI
    ``heim chat --target`` path — so both narrow tools identically. Each target's raw ``mcp_servers``
    names are mapped to the exact tool prefixes connect uses (:func:`_prefix_by_name`); a name that
    resolves to no server is dropped. A target with no ``mcp_servers`` is recorded as owns-all.
    """
    name_to_prefix = _prefix_by_name(resolved)
    explicit: dict[str, set[str]] = {}
    owns_all: set[str] = set()
    for target_id, target in zip(registry.ids, registry.targets, strict=True):
        if target.mcp_servers:
            explicit[target_id] = {name_to_prefix[n] for n in target.mcp_servers if n in name_to_prefix}
        else:
            owns_all.add(target_id)
    return TargetRouting(explicit=explicit, owns_all=owns_all)


def narrow_to_servers(toolset: MCPToolset, routing: TargetRouting, target_id: str) -> MCPToolset:
    """Narrow ``toolset`` to ``target_id``'s servers plus any shared (unclaimed) servers.

    Semantics for the turn's resolved ``target_id``:

    - **owns-all** (declared no ``mcp_servers``) → the full toolset (it owns everything).
    - **scoped** (explicit ``mcp_servers``) → its own prefixes *plus* the shared ones, where shared =
      every prefix not in any target's *explicit* set. Owns-all targets claim nothing here, so a scoped
      sibling still sees the global servers (``datetime``/``playwright``) instead of them being swallowed.

    A single-target registry naturally resolves to the full toolset (owns-all → all; or, if it is scoped,
    its explicit set already equals the whole claimed union so *everything else* is shared → still all).
    Free-play (an empty routing) is a no-op. Routes through :meth:`MCPToolset.subset`, so the ``_readonly``
    (confirmation-gate) metadata survives for every kept tool.
    """
    if routing.empty:
        return toolset
    if target_id in routing.owns_all:
        return toolset
    prefixes = toolset.prefixes()
    explicit_union: set[str] = set().union(*routing.explicit.values()) if routing.explicit else set()
    shared = prefixes - explicit_union
    resolved = routing.explicit.get(target_id, set()) | shared
    return toolset.subset(toolset.names_for_prefixes(resolved))


@asynccontextmanager
async def connect(
    servers: Sequence[tuple[str | None, list[str]] | tuple[str | None, list[str], dict[str, str]]],
) -> AsyncGenerator[MCPToolset, None]:
    """Spawn one or more MCP servers over stdio and yield a merged, namespaced toolset.

    Each server is ``(name, command)`` or ``(name, command, env)``: tools are namespaced under
    the given ``name`` when set (the ``.mcp.json`` server key), else a prefix derived from the
    executable. ``name`` may be ``None`` for a bare command (e.g. CLI ``--mcp``). A server's
    ``env`` (from its ``.mcp.json`` entry) is merged over heim's base environment for it only.
    """
    toolset = MCPToolset()
    used: dict[str, int] = {}  # disambiguate servers that derive the same prefix
    base_env = _mcp_env()  # heim's bin/ on PATH so bundled heim-mcp-* servers resolve
    async with AsyncExitStack() as stack:
        for spec in servers:
            name, command = spec[0], spec[1]
            server_env = spec[2] if len(spec) > 2 else {}
            env = {**base_env, **server_env} if server_env else base_env
            prefix = _server_prefix(name, command)
            # One server failing to start (e.g. an uninstalled optional server, a bad command)
            # must not take down the others — skip it, record why, and keep going.
            try:
                # Discard the spawned server's stderr (banners/logs) to keep our output clean.
                transport = StdioTransport(
                    command=_resolve_command(command[0]), args=command[1:], env=env, log_file=Path(os.devnull)
                )
                # Bound the MCP initialize handshake: fastmcp's default is None (wait forever), so a
                # server that starts but never completes init would hang `serve` startup / the TUI
                # mount indefinitely. 60s tolerates a cold `uvx`/`npx` first run; a hang is caught below.
                client = await stack.enter_async_context(Client(transport, init_timeout=60))
                n = used.get(prefix, 0)
                used[prefix] = n + 1
                await toolset.add(client, prefix if n == 0 else f"{prefix}{n + 1}")
            except Exception as exc:  # noqa: BLE001 - surface any spawn/connect failure without aborting the rest
                toolset.errors.append((name or command[0], str(exc)))
        yield toolset
