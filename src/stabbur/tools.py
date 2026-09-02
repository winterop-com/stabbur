"""MCP client: connect to an MCP server and expose its tools to a model.

stabbur is the MCP *client* — it spawns an MCP server (over stdio), lists its tools
as OpenAI function schemas for the model, and executes ``tool_call``s against it.
"""

import asyncio
import dataclasses
import datetime
import enum
import json
import os
import re
import shutil
import sys
from collections.abc import AsyncGenerator, Callable, Iterable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from stabbur.mcpservers import McpServer
    from stabbur.targets import AssistantRegistry

_NAME_STRIP = re.compile(r"^(stabbur-mcp-|mcp-server-|mcp-)")

McpSpec = tuple[str | None, list[str]] | tuple[str | None, list[str], dict[str, str]]
"""One server to spawn: ``(name, argv)`` or ``(name, argv, env)`` — what :func:`connect` takes."""


def _bin_dir() -> str:
    """The running interpreter's directory — where stabbur's bundled ``stabbur-mcp-*`` scripts live.

    Not necessarily on PATH (a ``uv tool install``ed stabbur exposes only the ``stabbur`` symlink),
    so we add it explicitly when spawning bundled servers.
    """
    return str(Path(sys.executable).parent)


def _mcp_env() -> dict[str, str]:
    """Environment for a spawned MCP server, with stabbur's own bin/ prepended to PATH.

    So bundled ``stabbur-mcp-*`` scripts (and tools they call) resolve regardless of how stabbur ran.
    """
    env = dict(os.environ)
    env["PATH"] = _bin_dir() + os.pathsep + env.get("PATH", "")
    return env


_SERVER_PREFIX = "stabbur-mcp-"


def _resolve_command(cmd: str) -> str:
    """Resolve a bare command to an absolute path, searching stabbur's own bin/ too.

    subprocess resolves a bare executable name against the *parent's* PATH, so putting stabbur's
    bin/ only in the child env isn't enough — resolve it here. A command already found on PATH
    (or absolute) is returned as-is; an unfound one is passed through unchanged.
    """
    return shutil.which(cmd, path=os.environ.get("PATH", "") + os.pathsep + _bin_dir()) or cmd


def command_found(command: str) -> bool:
    """Whether a spawn command resolves to an executable (stabbur's own bin/ searched too).

    The cheap pre-flight behind the CLI's ``--mcp`` warning: a mistyped command is by far the most
    common way a hand-passed server fails, and spotting it before the spawn lets the CLI say so on
    stderr instead of leaving the session quietly toolless.
    """
    return shutil.which(command, path=os.environ.get("PATH", "") + os.pathsep + _bin_dir()) is not None


def _slug(text: str) -> str:
    """Identifier slug: runs of non-alphanumerics → single underscores, trimmed."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")


def _default_name(command: list[str]) -> str:
    """Short server prefix from its launch command (e.g. stabbur-mcp-datetime → datetime)."""
    base = Path(command[0]).name if command else "mcp"
    return _slug(_NAME_STRIP.sub("", base)) or "mcp"


def _server_prefix(name: str | None, command: list[str]) -> str:
    """The tool namespace for a server: its manifest ``name`` if set, else derived."""
    if name:
        return _slug(name) or _default_name(command)
    return _default_name(command)


def _tool_schema(tool: Any) -> dict[str, Any] | None:
    """A tool's JSON-Schema parameters, across the MCP SDK's rename of the field.

    SDK v2 renamed ``inputSchema`` to ``input_schema`` and left the old name as a deprecated
    alias — which warns on *access*, so every `stabbur serve` printed a FastMCPDeprecationWarning
    per tool at startup. Read the new name first and only fall back for an older SDK, rather than
    reading the alias and suppressing what it says.
    """
    schema = getattr(tool, "input_schema", None)
    return schema if schema is not None else getattr(tool, "inputSchema", None)


def _openai_schema(tool: Any) -> dict[str, Any]:
    """Convert one MCP tool to an OpenAI ``tools`` entry."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": _tool_schema(tool) or {"type": "object", "properties": {}},
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
       parts, then **empty**.

    That last rung is deliberately empty rather than ``str(result)``. An image-only result (a
    screenshot tool: fastmcp leaves ``data`` and ``structured_content`` ``None``, and an
    ``ImageContent`` block has no ``.text``) falls through every rung, and the result's repr
    inlines the whole base64 payload — so a repr fallback fed the model a wall of base64: a
    context-blowing one for a text-only model, and the same image *twice* for a vision model
    (once as repr text, once as the follow-up image message). No textual content means no text;
    :func:`_result_content` keeps the image itself, and the agent loop turns an empty text plus
    images into exactly one representation of the image.
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
    return "\n".join(parts)


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


def assign_prefixes(specs: "Sequence[McpSpec]") -> list[str]:
    """The tool prefix each spec gets, in order — the **one** place that decides namespacing.

    A duplicate prefix is suffixed ``prefix2`` / ``prefix3`` (two *different* names can slug to the
    same prefix — e.g. ``a-b`` and ``a_b``). :func:`connect` assigns from this rather than counting as
    it spawns, so the assignment depends only on the spec *list*: a server that fails to start no
    longer shifts the next same-prefix server onto the bare prefix, and :func:`_prefix_by_name` (which
    routing is built from) predicts the live namespacing exactly instead of replaying a second copy of
    this rule.
    """
    used: dict[str, int] = {}
    out: list[str] = []
    for spec in specs:
        base = _server_prefix(spec[0], spec[1])
        n = used.get(base, 0)
        used[base] = n + 1
        out.append(base if n == 0 else f"{base}{n + 1}")
    return out


def _prefix_by_name(resolved: "Sequence[McpServer]") -> dict[str, str]:
    """Map each resolved server's ``.mcp.json`` name to the tool prefix :func:`connect` gives it.

    Straight through :func:`assign_prefixes`, so this is a *use* of connect's rule rather than a
    second copy of it. Callers that prepend extra servers to the connect list (the CLI's ``--mcp``)
    must append them **after** the resolved ones, or these prefixes are not the ones connect assigns.
    Precedent: ``cli/project.py``.
    """
    return dict(zip([s.name for s in resolved], assign_prefixes([s.to_spec() for s in resolved]), strict=True))


def build_target_routing(resolved: "Sequence[McpServer]", registry: "AssistantRegistry") -> TargetRouting:
    """The per-target routing table for a registry against the resolved MCP servers.

    The one production builder shared by the serve lifespan (``app.state.target_routing``) and the CLI
    ``stabbur chat --target`` path — so both narrow tools identically. Each target's raw ``mcp_servers``
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
async def connect(servers: Sequence[McpSpec]) -> AsyncGenerator[MCPToolset, None]:
    """Spawn one or more MCP servers over stdio and yield a merged, namespaced toolset.

    Each server is ``(name, command)`` or ``(name, command, env)``: tools are namespaced under
    the given ``name`` when set (the ``.mcp.json`` server key), else a prefix derived from the
    executable. ``name`` may be ``None`` for a bare command (e.g. CLI ``--mcp``). A server's
    ``env`` (from its ``.mcp.json`` entry) is merged over stabbur's base environment for it only.

    Prefixes come from :func:`assign_prefixes` up front, so the namespace a server lands in is a
    function of the spec list alone — a failed spawn can't shift the next same-prefix server onto the
    bare prefix, and routing built from :func:`_prefix_by_name` names the same servers this does.

    Ownership discipline mirrors :func:`_spawn_into` (the two are one spawn body in two shapes): the
    client is entered on a **local** stack and handed to the shared one only after ``toolset.add``
    (``list_tools``) succeeds, so a server whose handshake completes but whose tool listing fails has
    its stdio subprocess reaped here instead of living on, unusable, until teardown.
    """
    toolset = MCPToolset()
    base_env = _mcp_env()  # stabbur's bin/ on PATH so bundled stabbur-mcp-* servers resolve
    prefixes = assign_prefixes(servers)
    async with AsyncExitStack() as stack:
        for spec, prefix in zip(servers, prefixes, strict=True):
            name, command = spec[0], spec[1]
            server_env = spec[2] if len(spec) > 2 else {}
            env = {**base_env, **server_env} if server_env else base_env
            local_stack = AsyncExitStack()
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
                client = await local_stack.enter_async_context(Client(transport, init_timeout=60))
                await toolset.add(client, prefix)
            except Exception as exc:  # noqa: BLE001 - surface any spawn/connect failure without aborting the rest
                await local_stack.aclose()  # kill a half-entered client's subprocess before moving on
                toolset.errors.append((name or command[0], str(exc)))
                continue
            stack.push_async_callback(local_stack.pop_all().aclose)
        yield toolset


async def _spawn_into(
    stack: AsyncExitStack,
    toolset: MCPToolset,
    prefix: str,
    server: "McpServer",
    *,
    is_closing: "Callable[[], bool] | None" = None,
) -> bool:
    """Spawn one ``McpServer`` on ``stack`` and register its tools under the fixed ``prefix``.

    The single per-server spawn path shared by eager startup and lazy first-use: mirrors :func:`connect`'s
    per-server body (env merge, command resolution, bounded init handshake, stderr discarded). Returns
    ``True`` on success; on failure it records ``(label, error)`` in ``toolset.errors`` — the **same shape
    a startup failure records** — and returns ``False`` (never raises), so one bad server can't abort the
    rest and a caller can decide whether to keep it pending for a retry. The prior error for the same label
    is dropped first, so a retried lazy spawn never stacks duplicate entries.

    Ownership discipline (no leaked subprocesses): the client is entered on a **local** exit stack and only
    handed to the shared ``stack`` after ``toolset.add`` (``list_tools``) succeeds. If the add fails, the
    local stack is closed here so the just-spawned stdio subprocess dies before we return ``False`` —
    otherwise a retried lazy spawn would stack a fresh live subprocess on every attempt. ``is_closing`` (the
    bridge's teardown flag) is re-checked at the transfer point: if teardown began while we were entering,
    the client is closed instead of being registered on the about-to-drain shared stack (would orphan it).
    """
    base_env = _mcp_env()
    label = server.name or server.command
    command = [server.command, *server.args]
    env = {**base_env, **server.env} if server.env else base_env
    local_stack = AsyncExitStack()
    try:
        transport = StdioTransport(
            command=_resolve_command(command[0]), args=command[1:], env=env, log_file=Path(os.devnull)
        )
        client = await local_stack.enter_async_context(Client(transport, init_timeout=60))
        await toolset.add(client, prefix)
    except Exception as exc:  # noqa: BLE001 - a spawn/connect failure is recorded, never raised
        toolset.errors = [(lbl, err) for (lbl, err) in toolset.errors if lbl != label]
        toolset.errors.append((label, str(exc)))
        await local_stack.aclose()  # kill the half-entered client's subprocess before returning
        return False
    if is_closing is not None and is_closing():
        # Teardown raced this in-flight spawn: don't register on the shared stack (it is about to drain /
        # already draining) — close the entered client so its subprocess dies instead of being orphaned.
        await local_stack.aclose()
        return False
    # add succeeded and we're not closing: hand ownership of the live client to the shared stack so
    # lifespan teardown closes it alongside the eager clients (pop_all keeps a single close callback).
    stack.push_async_callback(local_stack.pop_all().aclose)
    return True


def _eager_prefixes(
    routing: TargetRouting, primary_id: str | None, all_prefixes: set[str], *, eager_all: bool
) -> set[str]:
    """Which server prefixes to spawn eagerly at startup (the rest are lazy, spawned on first use).

    Eager = every prefix reachable without narrowing to a *non-primary* scoped target:

    - **Full eager** (every prefix) when ``eager_all`` (the ``STABBUR_EAGER_MCP`` escape hatch), when the
      registry is free-play (``routing.empty``), or when the primary is owns-all — the primary then sees
      the whole toolset, so nothing can be deferred without starving it. This pins today's behavior for
      free-play and single-``[assistant]`` projects.
    - **Shared + the primary's own** otherwise: shared prefixes (owned by no target's explicit set) plus
      the primary target's explicit prefixes. A non-primary target's explicitly-owned prefixes are the
      only ones left lazy — so startup scales with targets *used*, not merely declared.
    """
    if eager_all or routing.empty or primary_id is None or primary_id in routing.owns_all:
        return set(all_prefixes)
    all_explicit: set[str] = set().union(*routing.explicit.values()) if routing.explicit else set()
    shared = all_prefixes - all_explicit
    return shared | routing.explicit.get(primary_id, set())


class MCPBridge:
    """A growing :class:`MCPToolset` fed by eager-at-startup and lazy-on-first-use spawns.

    An eager set is spawned at startup plus non-primary targets' servers on first use, all under **one**
    exit stack so lifespan teardown closes everything. :func:`connect_bridge` seeds the eager tools and
    stores the lazy servers as *pending* keyed by the **fixed** prefix :func:`_prefix_by_name` assigned,
    so narrowing keys never shift with spawn order.
    :meth:`ensure_target` spawns a target's pending servers on its first ``/api/chat`` turn, first
    ``?verify=1``, or first bind/unbind. Spawns single-flight per prefix; a spawn failure surfaces like a
    startup failure (recorded in ``toolset.errors``, tools simply absent) and leaves the server pending so
    the next first-use retries it.
    """

    def __init__(self, toolset: MCPToolset, stack: AsyncExitStack) -> None:
        self.toolset = toolset
        self._stack = stack
        self._pending: dict[str, McpServer] = {}  # fixed prefix -> not-yet-spawned server
        self._locks: dict[str, asyncio.Lock] = {}  # fixed prefix -> single-flight spawn lock
        # Set True (before the shared stack drains) by connect_bridge's teardown, so an in-flight lazy
        # spawn racing SIGINT/lifespan-close refuses to register on the about-to-drain stack — see
        # _ensure_one (entry + under-lock check) and _spawn_into's is_closing transfer-point re-check.
        self._closing = False

    @property
    def pending_prefixes(self) -> set[str]:
        """Prefixes declared but not yet spawned — their tools appear only after first use."""
        return set(self._pending)

    def pending_label(self, prefix: str) -> str | None:
        """The launch label (``.mcp.json`` name or command) of a pending prefix's deferred server, if any.

        Lets a caller (e.g. ``/api/doctor``) match a pending prefix against a recorded spawn failure in
        ``toolset.errors`` (keyed by that same label) without reaching into ``_pending`` directly.
        """
        server = self._pending.get(prefix)
        return (server.name or server.command) if server is not None else None

    async def _spawn(self, prefix: str, server: "McpServer") -> bool:
        """Spawn ``server`` under ``prefix`` on the shared stack (indirection kept overridable for tests)."""
        return await _spawn_into(self._stack, self.toolset, prefix, server, is_closing=lambda: self._closing)

    def _lock(self, prefix: str) -> asyncio.Lock:
        """The per-prefix lock guarding *every* mutation of that prefix's spawn state.

        Not only the single-flight spawn: an enable, a settings update, and a disable arriving from the
        API while a lazy spawn is in flight all take this, so a change lands strictly before or after a
        spawn. Without it, ``_pending[prefix]`` is a torn window — mid-spawn the prefix is not yet in
        :meth:`MCPToolset.prefixes` *and* still in ``_pending``, so a writer would report "applied" into
        a dict entry ``_ensure_one`` is about to pop.
        """
        lock = self._locks.get(prefix)
        if lock is None:  # no await between the .get() and the set -> race-free on the single-threaded loop
            lock = asyncio.Lock()
            self._locks[prefix] = lock
        return lock

    async def _ensure_one(self, prefix: str) -> None:
        """Spawn one pending ``prefix`` under its single-flight lock (a racing first-use waits, not respawns).

        Refuses once teardown has begun (``_closing``): both at entry and again under the lock, so a spawn
        started just before close records nothing and returns without registering on the draining stack.
        """
        if self._closing:  # teardown started -> don't begin a new spawn
            return
        async with self._lock(prefix):
            await self._spawn_locked(prefix)

    async def _spawn_locked(self, prefix: str) -> None:
        """Spawn one pending ``prefix``, assuming its lock is **already held** by the caller.

        Split out of :meth:`_ensure_one` so the API paths (:meth:`add_server`) can mutate ``_pending``
        and spawn in one uninterrupted critical section — ``asyncio.Lock`` is not reentrant, so they
        cannot simply call :meth:`_ensure_one` while holding it.
        """
        if self._closing:  # re-check: teardown may have begun while we waited for the lock
            return
        server = self._pending.get(prefix)
        if server is None:  # a racing caller already spawned it (single-flight)
            return
        if await self._spawn(prefix, server):
            self._pending.pop(prefix, None)  # live now; a failure stays pending so first-use retries

    async def ensure(self, prefixes: Iterable[str]) -> None:
        """Spawn any not-yet-live servers among ``prefixes`` (already-live/unknown prefixes are no-ops)."""
        wanted = [p for p in prefixes if p in self._pending]
        if wanted:
            await asyncio.gather(*(self._ensure_one(p) for p in wanted))

    def prefix_of(self, server: "McpServer") -> str:
        """The tool prefix ``server`` spawns under — the namespacing convention stays in this module."""
        return _server_prefix(server.name, [server.command, *server.args])

    def is_live(self, server: "McpServer") -> bool:
        """Whether ``server``'s tools are attached to the toolset **right now**.

        A lock-free read: true the instant a spawn finishes, but a spawn *in flight* still reads False.
        Callers that act on the answer (the disable API) must use :meth:`remove_server`, which decides
        under the prefix lock; this is for display and for tests.
        """
        return self.prefix_of(server) in self.toolset.prefixes()

    def tool_count(self, server: "McpServer") -> int:
        """How many of ``server``'s tools are attached right now (0 when it isn't live)."""
        return len(self.toolset.names_for_prefixes({self.prefix_of(server)}))

    async def update_server(self, server: "McpServer") -> bool:
        """Re-queue a *pending* server so a config change (new env) applies without a restart.

        Returns whether the change is in effect. Spawning is lazy, so the common case for a settings
        edit is a server that is configured but not yet started: its queued spec still carries the env
        read at startup, and swapping it is enough for the next first-use spawn to get the new one.
        An **already-attached** subprocess is a different story — a running process cannot be re-env'd —
        so that answers False and the caller must say "restart", never report a silent success. A server
        that is neither live nor pending has nothing to update, which is trivially applied.

        Taken under the prefix lock (:meth:`_lock`), so it can't land in the window where a lazy spawn
        has read ``_pending[prefix]`` but not yet popped it: the write would have been discarded a
        moment later while the caller had already been told ``applied``.
        """
        prefix = self.prefix_of(server)
        async with self._lock(prefix):
            if prefix in self.toolset.prefixes():
                return False
            if prefix in self._pending:
                self._pending[prefix] = server
            return True

    async def remove_server(self, server: "McpServer") -> bool:
        """Un-queue a switched-off server; report whether the disable is fully in effect.

        Returns True when nothing of ``server`` is running here — the disable took effect completely.
        Returns False when its subprocess is already attached: a running server cannot be detached, so
        the caller must say "restart", never claim the tools are gone.

        Dropping the *pending* entry is the half a plain "is it live?" check missed: a server queued for
        a lazy first-use spawn would otherwise still be spawned — attaching the very tools that were just
        switched off — long after the config said no. Under the prefix lock, so a spawn in flight is
        waited out and reported as live rather than slipping in behind the answer.
        """
        prefix = self.prefix_of(server)
        async with self._lock(prefix):
            if prefix in self.toolset.prefixes():
                return False
            self._pending.pop(prefix, None)
            return True

    async def add_server(self, server: "McpServer") -> tuple[bool, str]:
        """Attach a newly-configured server to the live toolset — an *enable* that needs no restart.

        The bridge already owns a still-open exit stack for lazy first-use spawns, so a server switched
        on while ``stabbur serve`` is running can ride the same path instead of waiting for a restart: it is
        queued as pending and spawned immediately under the fixed prefix it would have had at startup.
        Returns ``(attached, reason)`` — ``reason`` is the recorded spawn error when ``attached`` is False,
        so the caller reports a real failure rather than a fake success. A failed spawn stays *pending*
        (the existing retry contract), so an owns-all target's next first-use tries again.

        An enable **always attempts the spawn**, including for a prefix that is already pending. It used
        to report ``deferred - spawns on first use`` there, which was true only for a target-scoped server
        in a project: first use runs through :meth:`ensure_target`, which spawns nothing when routing is
        empty — so in free-play (and for a prefix left pending by an earlier *failed* enable) "deferred"
        meant never. The queued spec is replaced by the one just enabled, so the spawn also picks up any
        settings written in the same call.

        Not routed: ``app.state.target_routing`` was computed at startup, so a server added later is owned
        by no target and therefore **shared** — visible to every target, which is what a machine-global
        toggle means. A per-target scoping change still needs a restart.
        """
        prefix = self.prefix_of(server)
        async with self._lock(prefix):
            if prefix in self.toolset.prefixes():
                return True, "already attached"
            self._pending[prefix] = server  # newest config wins over a deferred or previously-failed spec
            await self._spawn_locked(prefix)
            if prefix not in self._pending:  # cleared only on a successful spawn
                return True, ""
            label = server.name or server.command
            reason = next((err for lbl, err in reversed(self.toolset.errors) if lbl == label), "could not start")
            return False, reason

    async def ensure_target(self, routing: TargetRouting, target_id: str) -> None:
        """Spawn ``target_id``'s pending servers: its explicit set, or every pending one for owns-all.

        Free-play (an empty routing) spawns nothing. An owns-all target owns the whole toolset, so its
        first use warms every still-pending server; a scoped target warms only its own explicit prefixes
        (its shared servers are always eager, hence never pending).
        """
        if routing.empty:
            return
        if target_id in routing.owns_all:
            wanted = self.pending_prefixes
        else:
            wanted = routing.explicit.get(target_id, set()) & self.pending_prefixes
        await self.ensure(wanted)


@asynccontextmanager
async def connect_bridge(
    resolved: "Sequence[McpServer]",
    routing: TargetRouting,
    primary_id: str | None,
    *,
    eager_all: bool = False,
) -> AsyncGenerator[MCPBridge, None]:
    """Spawn the eager servers now and yield an :class:`MCPBridge` that spawns the rest on first use.

    Splits ``resolved`` into eager vs lazy by :func:`_eager_prefixes`, spawns the eager set into a single
    :class:`MCPToolset` under one :class:`AsyncExitStack`, and records the lazy servers as pending on the
    bridge under the **fixed** prefixes :func:`_prefix_by_name` computes (so a lazily-spawned server lands
    under the same namespace it would have at startup, regardless of order). The stack stays open for the
    whole ``async with`` — a later :meth:`MCPBridge.ensure_target` enters new clients on it — so teardown
    closes eager *and* lazily-spawned clients together.
    """
    name_to_prefix = _prefix_by_name(resolved)
    all_prefixes = set(name_to_prefix.values())
    eager = _eager_prefixes(routing, primary_id, all_prefixes, eager_all=eager_all)
    toolset = MCPToolset()
    async with AsyncExitStack() as stack:
        bridge = MCPBridge(toolset, stack)
        for server in resolved:
            prefix = name_to_prefix[server.name]
            if prefix in eager:
                await _spawn_into(stack, toolset, prefix, server)
            else:
                bridge._pending[prefix] = server
        try:
            yield bridge
        finally:
            # Flag teardown BEFORE the shared stack drains (the finally runs while the stack is still
            # open): an in-flight lazy spawn re-checks this and closes its own client rather than
            # registering it on the draining stack (which would orphan the subprocess).
            bridge._closing = True
