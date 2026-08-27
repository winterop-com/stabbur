"""Browser-executed page actions: tools the agent loop runs in the user's tab.

stabbur's agent loop runs server-side and MCP tools execute server-side, while the DOM lives in
the browser. This module is the server half of the channel that closes that gap (``WEBMCP.md``
section 5b): the model calls what looks like an ordinary tool, the request is streamed to the
client as a typed ``page_action`` frame, and the loop blocks until the client reports the result
to ``POST /api/chat/page-action``. It is the same emit-block-resolve shape the write-confirmation
gate already uses, deliberately — a second consumer of a load-bearing mechanism, not a second
mechanism.

**No code on the wire, structurally.** :class:`PageActionFrame` carries a ``Literal`` action name
drawn from a closed registry plus an *arguments model* whose fields are declared per action with
``extra="forbid"``. There is no free-form dict and no untyped string field a script could ride in,
so the server cannot express "run this JavaScript" even if a model asked it to: mypy/pyright
reject it at build time and pydantic rejects it at runtime. Every other rule in 5b is decorative
without that one, which is why it is enforced by the types rather than by a comment.

Two more of 5b's rules are enforced here by *absence*: the frame has no tab field (rule 3 — the
model can never name a tab, so the client can only ever act on the one it bound) and no URL/origin
field today (rule 5 — the first action navigates nowhere). Adding an action means adding a spec to
:data:`REGISTRY` with its own argument model; the agent loop and the channel do not change.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from stabbur.tools import MCPToolset, ToolResult

if TYPE_CHECKING:
    from stabbur.config import Settings

# Cap on the text a page action feeds back to the model. A page's structured content is
# unbounded (a long report, a table with thousands of rows), and unlike an SSE detail — which is
# only ever *displayed*, and is capped separately — this text is spent from the model's context
# window, where overflowing it fails the whole turn rather than one tool call. Generous enough
# that a normal page arrives whole, and the marker says plainly that something was cut.
_MAX_RESULT = 50_000
_TRUNCATED = "\n[truncated: page content exceeded the size a single tool result may return]"

PageActionName = Literal["page_read"]
"""Every action the server can put on the wire. A closed set, by construction."""


class PageReadArgs(BaseModel):
    """Arguments for ``page_read``: none.

    What "the page's structured content" means is the client's decision for the page it is
    actually on (5b) — the server only carries the request, so there is nothing here to
    parameterise yet. Declared as an explicit empty ``extra="forbid"`` model rather than an
    omitted or ``dict``-typed field so the no-code-on-the-wire property holds for this action the
    same way it will for the next one, without every future caller having to remember it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


PageActionArgs = PageReadArgs
"""Union of every registered action's argument model (one member today; ``A | B`` as they land)."""


class PageActionFrame(BaseModel):
    """The SSE frame streamed mid-turn — 5b's wire contract, expressed as a type.

    Serialized straight into the chat stream, so this class *is* the contract: an action name the
    client dispatches on and its typed arguments, and nothing else.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["page_action"] = "page_action"
    id: str
    action: PageActionName
    args: PageActionArgs


class PageActionResult(BaseModel):
    """The outcome of one page action: what the client reported, or what a failure resolved to.

    Failure is representable and success is not the default, because every fail-safe path
    (timeout, closed panel, cancelled stream) has to produce a value here.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    result: Any = None  # opaque JSON from the client — carried to the model, never interpreted
    error: str = ""


class PageActionSpec(BaseModel):
    """One registered action: how the model sees it and how its arguments are validated."""

    model_config = ConfigDict(frozen=True)

    name: PageActionName
    description: str
    args_model: type[PageActionArgs]
    # Drives the existing confirmation gate via MCPToolset.is_readonly, so 5b rule 2 ("reads and
    # navigation are ungated; anything that mutates rides the confirm gate") costs no new code:
    # a mutating action registers readonly=False and is gated by the same policy as an MCP write.
    readonly: bool


REGISTRY: dict[str, PageActionSpec] = {
    "page_read": PageActionSpec(
        name="page_read",
        description=(
            "Read the structured content of the page the user is currently looking at. "
            "Use this when the answer depends on what is on screen right now rather than on "
            "data you can fetch. Takes no arguments and changes nothing."
        ),
        args_model=PageReadArgs,
        readonly=True,
    ),
}

# The channel: given an action name and its validated arguments, deliver it to the client and
# resolve with what came back. Async-only, because blocking the agent loop on a client round-trip
# is the entire point — mirrors ConfirmSink, whose contract this shares.
PageActionSink = Callable[[PageActionName, PageActionArgs], Awaitable[PageActionResult]]


def resolve(requested: list[str] | None) -> list[PageActionSpec]:
    """The action specs to expose for one turn, given the client's declared capabilities.

    ``None``/empty — a plain browser tab, curl, the CLI — exposes nothing: handing the model a
    tool that nobody is listening for buys a guaranteed timeout, not a capability. A client with
    an executor sends the action names it implements. An unrecognized name is *ignored* rather
    than rejected so a newer client against an older stabbur degrades to the actions this server
    actually knows, instead of failing the turn over one it doesn't.
    """
    if not requested:
        return []
    return [REGISTRY[name] for name in dict.fromkeys(requested) if name in REGISTRY]


def tool_schema(spec: PageActionSpec) -> dict[str, Any]:
    """The OpenAI ``tools`` entry for an action — its parameters generated from the args model.

    Generated, not hand-written, so the schema the model is shown and the validation the call
    actually undergoes can never drift apart.
    """
    parameters = spec.args_model.model_json_schema()
    parameters.pop("title", None)  # pydantic's class-name title is noise in a tool schema
    return {
        "type": "function",
        "function": {"name": spec.name, "description": spec.description, "parameters": parameters},
    }


def timeout_seconds(settings: "Settings") -> float:
    """How long the loop waits for the client to answer one page action.

    Reuses ``tool_timeout`` (``STABBUR_TOOL_TIMEOUT``, 120s), not ``confirm_timeout``: a page
    action is a tool call answered by *software* in the panel, so the right bound is the one that
    already means "a tool call is taking too long", not the confirm gate's 300s, which is
    calibrated to a human who may have walked away from the desk. ``tool_timeout = 0`` means "no
    bound" for a local MCP server, which is exactly what 5b rule 4 forbids here — a closed panel
    would hold the turn open forever — so that setting falls back to ``confirm_timeout`` rather
    than waiting indefinitely. Neither setting is page-action-specific yet; when the two bounds
    need to diverge, that is the moment to add ``page_action_timeout``, not before.
    """
    return settings.tool_timeout or float(settings.confirm_timeout)


def as_tool_result(outcome: PageActionResult) -> ToolResult:
    """Turn a client's report into the tool result the model reads.

    A failure becomes ``error: ...`` — the shape the agent loop already gives a tool that raised
    or an action the user declined — so a dead channel reads as a failed tool call the model
    knows how to recover from, and can never be mistaken for an empty page.
    """
    if not outcome.ok:
        return ToolResult(text=f"error: {outcome.error or 'the page action failed'}")
    if outcome.result is None:
        return ToolResult(text="ok")  # done, nothing to report — not the string "null"
    text = (
        outcome.result
        if isinstance(outcome.result, str)
        else json.dumps(outcome.result, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    return ToolResult(text=text if len(text) <= _MAX_RESULT else text[:_MAX_RESULT] + _TRUNCATED)


class PageActionToolset(MCPToolset):
    """An :class:`MCPToolset` view with this turn's page actions appended.

    Composition over mutation: the app-wide toolset is shared by every request and left untouched,
    since page actions are per-turn (only a client that says it can execute them is offered any).
    Presenting them as a toolset rather than as a new parameter is what keeps the agent loop
    unchanged — it still sees one object with ``schemas`` / ``is_readonly`` / ``call``, so a page
    action is gated, narrowed, error-reported and event-emitted by the code that already does that
    for MCP tools.

    Wrap *last*, after any target narrowing and ``enabled_tools`` subsetting: those select among
    the MCP servers a project configured, while the page actions available are decided by what the
    client can execute, not by that list.
    """

    def __init__(self, base: MCPToolset, actions: Sequence[PageActionSpec], invoke: PageActionSink) -> None:
        super().__init__()
        self._base = base
        # Keyed by str, not by the Literal: lookups come from whatever name the *model* emitted.
        self._actions: dict[str, PageActionSpec] = {spec.name: spec for spec in actions}
        self._invoke = invoke
        self.schemas = [*base.schemas, *(tool_schema(spec) for spec in actions)]
        self.errors = base.errors  # the same list object: spawn failures keep surfacing through the view

    def is_readonly(self, name: str) -> bool:
        """A page action's declared ``readonly``; anything else defers to the wrapped toolset."""
        spec = self._actions.get(name)
        return spec.readonly if spec is not None else self._base.is_readonly(name)

    def prefixes(self) -> set[str]:
        """The wrapped toolset's server prefixes — a page action belongs to no MCP server."""
        return self._base.prefixes()

    def names_for_prefixes(self, prefixes: set[str]) -> set[str]:
        """Delegated: prefix routing is about MCP servers, which only the wrapped toolset has."""
        return self._base.names_for_prefixes(prefixes)

    def subset(self, names: set[str]) -> "PageActionToolset":
        """Narrow both halves, so a subset of this view is still a working page-action toolset.

        Nothing subsets after wrapping today; overridden anyway because inheriting the base
        implementation would silently return an MCPToolset with the page actions dropped and the
        channel gone — a landmine for the first caller that does.
        """
        kept = [spec for spec in self._actions.values() if spec.name in names]
        return PageActionToolset(self._base.subset(names), kept, self._invoke)

    async def call(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> ToolResult:
        """Execute a page action in the client's tab, or delegate to the wrapped MCP toolset.

        ``timeout`` bounds an MCP server call and is passed through untouched; a page action is
        bounded by the channel itself (the caller's ``asyncio.wait_for``), the way a confirmation
        is, since what is being waited on is a client round-trip and not a subprocess.
        """
        spec = self._actions.get(name)
        if spec is None:
            return await self._base.call(name, arguments, timeout=timeout)
        try:
            args = spec.args_model.model_validate(arguments)
        except ValidationError as exc:
            # Validation is the no-code-on-the-wire boundary: a model that invents an argument
            # gets the error back and retries, and nothing it invented reaches the browser.
            return ToolResult(text=f"error: invalid arguments for {name} ({exc}); resend valid arguments.")
        return as_tool_result(await self._invoke(spec.name, args))

    async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
        """Delegated wholesale: this path serves the assistant verify probe, which calls MCP tools.

        A page-action name therefore raises ``KeyError`` here, exactly as any unknown tool does.
        """
        return await self._base.call_structured(name, arguments, timeout=timeout)
