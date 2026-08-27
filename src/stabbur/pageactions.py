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

Rule 3 is enforced here by *absence*: the frame has no tab field, so the model can never name a
tab and the client can only ever act on the one it bound. Adding one would be the regression.
Rule 5 (same-origin) is the client's check at execution time and cannot be made here — the server
is never told what the bound origin is — but the server still refuses a URL that is *code*
(:class:`PageNavigateArgs`), which is rule 1 rather than rule 5.

Adding an action means adding a spec to :data:`REGISTRY` with its own argument model; the agent
loop and the channel do not change. An action that is not ``readonly`` also gates — see
:class:`PageActionSpec`.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from stabbur.agent import ConfirmSink
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

# Byte-for-byte the string the agent loop feeds the model when a user denies a gated tool call.
# A page action denied *here* rather than in the loop must be indistinguishable to the model, or
# the same refusal reads as two different failures depending on which gate happened to catch it.
_DECLINED = "error: user declined this action"

PageActionName = Literal["page_read", "page_navigate"]
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


class PageNavigateArgs(BaseModel):
    """Arguments for ``page_navigate``: where to send the tab.

    ``url`` is a plain ``str``, not pydantic's ``AnyHttpUrl``, because the frame is
    ``model_dump()``-ed straight into ``json.dumps`` for the SSE stream and a ``Url`` object is not
    JSON-serializable — a type that validates beautifully and then breaks the wire is worse than
    a validator.

    The validator is rule 1, not rule 5: ``javascript:`` and ``data:`` URLs *are* code, so a
    channel that accepted them would be the ``eval``-shaped channel the whole design exists to
    avoid, whatever the frame is called. Same-origin (rule 5) cannot be checked here — the server
    is never told the bound origin — so it stays the client's check immediately before execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(description="Absolute http(s) URL, on the same site as the page the user is already on.")

    @field_validator("url")
    @classmethod
    def _absolute_http_only(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        return value


PageActionArgs = PageReadArgs | PageNavigateArgs
"""Union of every registered action's argument model."""


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
    # The predicate is "answers a question and leaves the user's tab exactly as it found it", not
    # "does not write to a server": a navigation stores nothing anywhere and still fails it,
    # because it moves the tab the user is looking at and discards whatever state was on the page.
    #
    # readonly=False means GATED, ALWAYS — 5b rule 2 as corrected. It deliberately does NOT just
    # feed the confirm policy through MCPToolset.is_readonly, which was the original plan and was
    # wrong: `confirm_tools` defaults to "none" for free-play and for a read-only assistant, so
    # riding the policy would leave an acting page action ungated by default on exactly the
    # generic, no-project site where page-acting is most wanted. PageActionToolset therefore
    # raises the gate itself when the loop's policy would not have (see its `call`).
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
    "page_navigate": PageActionSpec(
        name="page_navigate",
        description=(
            "Send the tab the user is looking at to a different page on the same site. "
            "The user is asked to approve every navigation, and a URL on another site is "
            "refused, so navigate only where the user asked to go and read the page first if "
            "you are guessing at the address."
        ),
        args_model=PageNavigateArgs,
        readonly=False,
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
    actually undergoes can never drift apart. What is dropped is what pydantic derives from the
    *class* rather than from the contract: the class-name title, and the docstring — which is
    written for whoever maintains this module and would otherwise spend the model's context on
    internal reasoning. The model-facing text is the spec's ``description`` and each field's own.
    """
    parameters = spec.args_model.model_json_schema()
    parameters.pop("title", None)
    parameters.pop("description", None)
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

    It is also where 5b rule 2's "regardless of policy" lives. ``confirm`` and ``confirm_policy``
    describe the turn's *existing* gate; a non-readonly page action is confirmed here whenever
    that gate would not have caught it. Both defaults are the fail-safe ones — "the loop is not
    gating" and "there is no channel to ask on" — so a caller that wires neither denies an acting
    page action rather than running it, which is the failure that costs nothing. And a caller that
    passes a gating *policy* but no channel is the same case: see :meth:`_approved`, which reads
    the channel and never the policy string alone.
    """

    def __init__(
        self,
        base: MCPToolset,
        actions: Sequence[PageActionSpec],
        invoke: PageActionSink,
        confirm: ConfirmSink | None = None,
        confirm_policy: Literal["all", "writes", "none"] = "none",
    ) -> None:
        super().__init__()
        self._base = base
        # Keyed by str, not by the Literal: lookups come from whatever name the *model* emitted.
        self._actions: dict[str, PageActionSpec] = {spec.name: spec for spec in actions}
        self._invoke = invoke
        self._confirm = confirm
        # Annotated, not inferred: pyright widens a literal assigned to an attribute to ``str``,
        # and this value is passed straight back into this same constructor by ``subset``.
        self._confirm_policy: Literal["all", "writes", "none"] = confirm_policy
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
        return PageActionToolset(self._base.subset(names), kept, self._invoke, self._confirm, self._confirm_policy)

    async def _approved(self, spec: PageActionSpec, args: PageActionArgs) -> bool:
        """Whether an acting page action may proceed — 5b rule 2's forced gate.

        A ``readonly`` action never reaches here. For any other, the question is whether a human
        was actually asked, and the only evidence of that is **a confirmation channel** — so the
        channel is what this gates on, and the policy string only decides *who does the asking*:

        * No ``confirm`` sink: nothing can have asked anybody, whatever the policy says. Deny.
          The policy is a caller-supplied string, and ``"writes"`` with no sink is a caller
          asserting a gate that does not exist; trusting the assertion over the missing channel
          is exactly the ungated-acting failure this gate was added to prevent.
        * A sink, under ``"writes"``/``"all"``: the agent loop already gated this exact call
          through this same sink before reaching ``call``. Asking again would prompt the user
          twice for one click.
        * A sink, under ``"none"`` — free-play, and a read-only assistant, i.e. the default on a
          generic site — nothing gated it, so this is the gate: ask.
        """
        if self._confirm is None:
            return False
        if self._confirm_policy != "none":
            return True
        return await self._confirm(spec.name, args.model_dump())

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
        # Confirm *after* validating and *before* invoking: the user is shown the arguments that
        # would actually be sent, and a declined action never reaches the tab at all.
        if not spec.readonly and not await self._approved(spec, args):
            return ToolResult(text=_DECLINED)
        return as_tool_result(await self._invoke(spec.name, args))

    async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
        """Delegated wholesale: this path serves the assistant verify probe, which calls MCP tools.

        A page-action name therefore raises ``KeyError`` here, exactly as any unknown tool does.
        """
        return await self._base.call_structured(name, arguments, timeout=timeout)
