"""Tests for browser-executed page actions: the channel, and the ``page_read`` action (WEBMCP.md 5b).

Three layers, because the interesting failures live at different ones:

* **Structural** — what the types make impossible. The wire frame cannot name an action outside the
  registry and cannot carry an undeclared field, so "the server never sends code" is checked here
  rather than asserted in prose.
* **Endpoint** — the 404 paths and the app-level guard the channel inherits, mirroring
  ``test_chat_confirm.py``: the two mechanisms have the same shape and must keep it.
* **End-to-end** — the *real* :func:`stabbur.agent.run` loop over a stubbed runtime turn, so what is
  exercised is the model calling the tool, the loop blocking, a test client answering the POST, and
  the client's payload arriving as the tool result the model reads next round.

The interleaving tests run /api/chat as a background task and answer the action once its future is
registered in ``app.state.pending_page_actions`` — httpx's ASGITransport buffers the whole SSE body
before returning, so a "read the stream, then answer" client loop would deadlock.
"""

import asyncio
import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from stabbur import agent, pageactions
from stabbur.app import create_app
from stabbur.config import Settings
from stabbur.pageactions import (
    PageActionFrame,
    PageActionResult,
    PageActionToolset,
    PageNavigateArgs,
    PageReadArgs,
)
from stabbur.routers import serving
from stabbur.routers.serving.chat import ChatRequest, chat
from stabbur.tools import MCPToolset


@pytest.fixture
def app() -> FastAPI:
    """App with a clean (no model loaded) manager and an empty page-action registry."""
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    """Async client running the app's lifespan."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class FakeManager:
    """A model is 'loaded' so /api/chat proceeds into the stream."""

    current = type("M", (), {"load_target": Path("/models/x")})()
    base_url = "http://runtime"


def _events(text: str) -> list[dict[str, Any]]:
    """Parse the SSE data events from a buffered /api/chat response body."""
    return [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]


def _install_action_turn(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    action: str = "page_read",
    args: str = "{}",
) -> None:
    """Stub the runtime round-trip so the model calls one page action, then answers.

    Only :func:`stabbur.agent._stream_turn` (the HTTP call to llama-server) is replaced — the agent
    loop itself, the toolset, the confirmation gate and the page-action channel are the real ones,
    so these tests fail if any of them stops carrying the result through.
    """
    rounds = {"n": 0}
    captured["bodies"] = []

    async def fake_stream_turn(
        http: Any, base_url: str, body: dict[str, Any], on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[dict[str, str]], dict[str, Any] | None, str | None]:
        rounds["n"] += 1
        captured["bodies"].append(body)
        if rounds["n"] == 1:
            return "", [{"id": "call_1", "name": action, "args": args}], None, "tool_calls"
        # Second round: the tool result is now in `messages` — capture what the model actually sees.
        captured["messages"] = [dict(m) for m in body["messages"]]
        await agent._emit(on_token, "answered")
        return "answered", [], None, "stop"

    monkeypatch.setattr(agent, "_stream_turn", fake_stream_turn)


async def _run_with_client_answer(
    app: FastAPI, client: AsyncClient, request_json: dict[str, Any], answer: dict[str, Any]
) -> list[dict[str, Any]]:
    """Drive one /api/chat turn, answering its page action with ``answer``; return its SSE events."""
    holder: dict[str, Any] = {}

    async def collect() -> None:
        holder["resp"] = await client.post("/api/chat", json=request_json)

    task = asyncio.create_task(collect())
    try:
        async with asyncio.timeout(10):
            while not app.state.pending_page_actions:
                await asyncio.sleep(0.01)  # let the loop reach the tool call + register the future
            pid = next(iter(app.state.pending_page_actions))
            r = await client.post("/api/chat/page-action", json={"id": pid, **answer})
            assert r.status_code == 200
            assert r.json() == {"ok": True}
            await task
    finally:
        task.cancel()
    return _events(holder["resp"].text)


async def _run_with_confirm(
    app: FastAPI,
    client: AsyncClient,
    request_json: dict[str, Any],
    *,
    approve: bool,
    answer: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Drive one /api/chat turn that gates: answer the confirmation, then the action if it runs.

    The mirror of :func:`_run_with_client_answer` for an action that must be approved first, and
    it asserts the ordering that matters — nothing is registered on the page-action channel until
    the confirmation has been answered, so a declined action never reaches the tab.
    """
    holder: dict[str, Any] = {}

    async def collect() -> None:
        holder["resp"] = await client.post("/api/chat", json=request_json)

    task = asyncio.create_task(collect())
    try:
        async with asyncio.timeout(10):
            while not app.state.pending_confirmations:
                assert not app.state.pending_page_actions, "the action reached the tab before the gate"
                await asyncio.sleep(0.01)
            cid = next(iter(app.state.pending_confirmations))
            r = await client.post("/api/chat/confirm", json={"id": cid, "approve": approve})
            assert r.status_code == 200
            if approve:
                while not app.state.pending_page_actions:
                    await asyncio.sleep(0.01)
                pid = next(iter(app.state.pending_page_actions))
                r = await client.post("/api/chat/page-action", json={"id": pid, **(answer or {"ok": True})})
                assert r.status_code == 200
            await task
    finally:
        task.cancel()
    return _events(holder["resp"].text)


async def _until(predicate: Callable[[], bool], ticks: int = 1000) -> None:
    """Poll ``predicate`` on the event loop until it holds (bounded, so a hang fails the test)."""
    for _ in range(ticks):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


def _tool_message(captured: dict[str, Any]) -> dict[str, Any]:
    """The ``tool`` turn the model was fed after the page action resolved."""
    return next(m for m in reversed(captured["messages"]) if m.get("role") == "tool")


# --- structural: the server cannot put code on the wire ----------------------------------------


def test_frame_rejects_an_unregistered_action() -> None:
    # The action name is a Literal over the registry, so "run this instead" is not expressible.
    with pytest.raises(ValidationError):
        PageActionFrame(id="abc", action="eval_js", args=PageReadArgs())  # type: ignore[arg-type]


def test_action_args_reject_undeclared_fields() -> None:
    # extra="forbid": there is no field a script could ride in, not even an ignored one.
    with pytest.raises(ValidationError):
        PageReadArgs(script="alert(1)")  # type: ignore[call-arg]


def test_every_registered_action_forbids_extra_args() -> None:
    # The property has to hold for actions added later too, not just for the first one.
    for spec in pageactions.REGISTRY.values():
        assert spec.args_model.model_config.get("extra") == "forbid", spec.name


def test_frame_serializes_to_the_documented_wire_shape() -> None:
    frame = PageActionFrame(id="deadbeef", action="page_read", args=PageReadArgs())
    assert frame.model_dump() == {"type": "page_action", "id": "deadbeef", "action": "page_read", "args": {}}


# --- the tool as the model sees it -------------------------------------------------------------


def test_page_read_tool_schema() -> None:
    schema = pageactions.tool_schema(pageactions.REGISTRY["page_read"])
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "page_read"
    assert schema["function"]["parameters"]["properties"] == {}
    assert "page" in schema["function"]["description"].lower()


def test_resolve_ignores_unknown_names_and_deduplicates() -> None:
    assert pageactions.resolve(None) == []
    assert pageactions.resolve([]) == []
    assert pageactions.resolve(["nope"]) == []
    assert [s.name for s in pageactions.resolve(["page_read", "page_read", "nope"])] == ["page_read"]


def test_page_read_is_declared_read_only() -> None:
    # 5b rule 2: reads are ungated. is_readonly is also what the loop's "writes" policy reads, so
    # a read stays ungated under that policy too (see test_read_is_not_gated_by_the_confirm_policy).
    toolset = PageActionToolset(MCPToolset(), pageactions.resolve(["page_read"]), _never_invoked)
    assert toolset.is_readonly("page_read") is True
    assert toolset.is_readonly("some_mcp_tool") is False  # unknown → fail-safe, delegated to the base


def test_navigate_is_not_declared_read_only() -> None:
    # Navigation answers no question and moves the tab the user is looking at, so it is an action.
    toolset = PageActionToolset(MCPToolset(), pageactions.resolve(["page_navigate"]), _never_invoked)
    assert toolset.is_readonly("page_navigate") is False


def test_navigate_refuses_a_url_that_is_code() -> None:
    # A `javascript:`/`data:` URL is the eval-shaped channel rule 1 forbids, wearing a URL field's
    # clothes; a relative one has no origin for the client's same-origin check to compare.
    for bad in ("javascript:alert(1)", "data:text/html,<script>x</script>", "/relative", "file:///etc/passwd", ""):
        with pytest.raises(ValidationError):
            PageNavigateArgs(url=bad)
    assert PageNavigateArgs(url="https://example.test/a?b=c").url == "https://example.test/a?b=c"


def test_navigate_frame_serializes_to_the_documented_wire_shape() -> None:
    frame = PageActionFrame(id="deadbeef", action="page_navigate", args=PageNavigateArgs(url="https://a.test/x"))
    assert frame.model_dump() == {
        "type": "page_action",
        "id": "deadbeef",
        "action": "page_navigate",
        "args": {"url": "https://a.test/x"},
    }
    # The frame is json.dumps-ed onto the SSE stream, so every field must survive that as-is.
    assert json.loads(json.dumps(frame.model_dump()))["args"] == {"url": "https://a.test/x"}


def test_tool_schema_does_not_spend_the_model_s_context_on_docstrings() -> None:
    # The args model's docstring is written for maintainers; only the spec description and the
    # per-field descriptions are model-facing.
    params = pageactions.tool_schema(pageactions.REGISTRY["page_navigate"])["function"]["parameters"]
    assert "description" not in params
    assert params["properties"]["url"]["description"]


# --- 5b rule 2: an acting page action is gated regardless of policy ----------------------------


def _gating_toolset(
    confirm: Any, policy: str = "none", invoke: pageactions.PageActionSink | None = None
) -> PageActionToolset:
    """A toolset offering ``page_navigate`` with a given confirm channel and turn policy."""
    return PageActionToolset(
        MCPToolset(),
        pageactions.resolve(["page_navigate"]),
        invoke or _never_invoked,
        confirm,
        cast(Any, policy),
    )


async def test_acting_action_is_gated_even_when_the_policy_gates_nothing() -> None:
    # The hole rule 2 was corrected for: "none" is the DEFAULT for free-play and for a read-only
    # assistant, i.e. exactly the generic site page-acting is meant for. Nothing may reach the tab.
    asked: list[tuple[str, dict[str, Any]]] = []

    async def deny(name: str, args: dict[str, Any]) -> bool:
        asked.append((name, args))
        return False

    result = await _gating_toolset(deny).call("page_navigate", {"url": "https://evil.test/"})
    assert result.text == "error: user declined this action"
    assert asked == [("page_navigate", {"url": "https://evil.test/"})]  # the user saw the real URL


async def test_approving_the_forced_gate_lets_the_action_through() -> None:
    invoked: list[str] = []

    async def approve(name: str, args: dict[str, Any]) -> bool:
        return True

    async def invoke(action: pageactions.PageActionName, args: pageactions.PageActionArgs) -> PageActionResult:
        invoked.append(action)
        return PageActionResult(ok=True, result="navigated")

    result = await _gating_toolset(approve, invoke=invoke).call("page_navigate", {"url": "https://a.test/x"})
    assert result.text == "navigated"
    assert invoked == ["page_navigate"]


async def test_no_confirm_channel_denies_rather_than_acting() -> None:
    # Fail-safe, and the default: a caller that wires no channel (the CLI, a test) must not be a
    # way to act ungated. Same posture as the agent loop's missing-sink deny.
    result = await _gating_toolset(None).call("page_navigate", {"url": "https://a.test/x"})
    assert result.text == "error: user declined this action"


async def test_the_forced_gate_does_not_ask_twice_under_a_gating_policy() -> None:
    # Under "writes"/"all" the agent loop already gated this exact call before reaching `call`;
    # asking again would prompt the user twice for one navigation.
    async def must_not_ask(name: str, args: dict[str, Any]) -> bool:
        raise AssertionError("the loop already gated this call")

    async def invoke(action: pageactions.PageActionName, args: pageactions.PageActionArgs) -> PageActionResult:
        return PageActionResult(ok=True, result="navigated")

    for policy in ("writes", "all"):
        toolset = _gating_toolset(must_not_ask, policy, invoke)
        assert (await toolset.call("page_navigate", {"url": "https://a.test/x"})).text == "navigated"


async def test_invalid_arguments_are_refused_before_the_user_is_asked() -> None:
    # No point prompting a human about a call that cannot be made; and the rejected URL must not
    # appear in a confirmation dialog as though it were about to happen.
    async def must_not_ask(name: str, args: dict[str, Any]) -> bool:
        raise AssertionError("nothing to confirm: the arguments never validated")

    result = await _gating_toolset(must_not_ask).call("page_navigate", {"url": "javascript:alert(1)"})
    assert result.text.startswith("error: invalid arguments for page_navigate")


async def test_a_narrowed_toolset_keeps_the_gate() -> None:
    # subset() rebuilds the view; dropping the confirm channel there would silently un-gate acting.
    async def must_not_ask(name: str, args: dict[str, Any]) -> bool:
        raise AssertionError("unreachable")

    narrowed = _gating_toolset(must_not_ask, "writes").subset({"page_navigate"})
    assert narrowed._confirm is must_not_ask
    assert narrowed._confirm_policy == "writes"


async def _never_invoked(action: pageactions.PageActionName, args: pageactions.PageActionArgs) -> PageActionResult:
    raise AssertionError(f"the channel must not be reached for {action}")


async def test_invalid_arguments_never_reach_the_browser() -> None:
    # A model inventing an argument gets a parse error back; nothing it invented is transmitted.
    toolset = PageActionToolset(MCPToolset(), pageactions.resolve(["page_read"]), _never_invoked)
    result = await toolset.call("page_read", {"script": "alert(1)"})
    assert result.text.startswith("error: invalid arguments for page_read")


async def test_subset_keeps_the_channel_working() -> None:
    toolset = PageActionToolset(MCPToolset(), pageactions.resolve(["page_read"]), _never_invoked)
    assert toolset.subset({"page_read"}).names == ["page_read"]
    assert toolset.subset(set()).names == []


# --- result conversion -------------------------------------------------------------------------


def test_failure_reports_read_as_a_tool_error() -> None:
    assert pageactions.as_tool_result(PageActionResult(ok=False, error="tab closed")).text == "error: tab closed"
    assert pageactions.as_tool_result(PageActionResult(ok=False)).text.startswith("error: ")


def test_success_carries_the_client_payload() -> None:
    payload = {"title": "Data Entry", "text": "hello"}
    assert json.loads(pageactions.as_tool_result(PageActionResult(ok=True, result=payload)).text) == payload
    assert pageactions.as_tool_result(PageActionResult(ok=True, result="plain")).text == "plain"
    assert pageactions.as_tool_result(PageActionResult(ok=True)).text == "ok"


def test_oversized_page_content_is_truncated() -> None:
    huge = "x" * (pageactions._MAX_RESULT + 100)
    text = pageactions.as_tool_result(PageActionResult(ok=True, result=huge)).text
    assert len(text) < len(huge) + len(pageactions._TRUNCATED)
    assert text.endswith(pageactions._TRUNCATED)


def test_timeout_reuses_the_tool_timeout_and_never_waits_forever() -> None:
    assert pageactions.timeout_seconds(Settings(tool_timeout=42.0)) == 42.0
    # tool_timeout=0 means "no bound" for a local MCP server; here it must NOT mean "hang forever".
    assert pageactions.timeout_seconds(Settings(tool_timeout=0.0, confirm_timeout=7)) == 7.0


# --- endpoint: 404 paths + the app-level guard -------------------------------------------------


async def test_page_action_unknown_id_is_404(client: AsyncClient) -> None:
    r = await client.post("/api/chat/page-action", json={"id": "does-not-exist", "ok": True, "result": {}})
    assert r.status_code == 404


async def test_page_action_resolving_twice_second_is_404(app: FastAPI, client: AsyncClient) -> None:
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[PageActionResult] = loop.create_future()
    app.state.pending_page_actions["pid1"] = fut

    r1 = await client.post("/api/chat/page-action", json={"id": "pid1", "ok": True, "result": {"t": 1}})
    assert r1.status_code == 200
    assert fut.result().result == {"t": 1}

    # Already resolved (or already expired and popped) → that action is over.
    r2 = await client.post("/api/chat/page-action", json={"id": "pid1", "ok": True, "result": {}})
    assert r2.status_code == 404


async def test_page_action_cross_site_is_blocked(client: AsyncClient) -> None:
    # Inherits the app-level cross-site guard on /api, exactly as the confirm route does.
    r = await client.post(
        "/api/chat/page-action",
        json={"id": "x", "ok": True},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert r.status_code == 403


# --- end-to-end through the real agent loop ----------------------------------------------------


async def test_no_page_tools_unless_the_client_declares_them(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default (a plain browser tab, curl, the CLI): the model is offered nothing it cannot run.
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    try:
        r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert "tools" not in captured["bodies"][0]
    assert app.state.pending_page_actions == {}


async def test_unknown_declared_action_offers_nothing(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    try:
        r = await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "page_actions": ["fly_the_ship"]}
        )
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert "tools" not in captured["bodies"][0]


async def test_loop_blocks_and_resumes_with_the_client_result(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    payload = {"title": "Data Entry", "fields": [{"name": "BCG doses", "value": "12"}]}
    try:
        events = await _run_with_client_answer(
            app,
            client,
            {"messages": [{"role": "user", "content": "what is on screen?"}], "page_actions": ["page_read"]},
            {"ok": True, "result": payload},
        )
    finally:
        app.dependency_overrides.clear()

    # The model was offered exactly the declared action.
    assert [t["function"]["name"] for t in captured["bodies"][0]["tools"]] == ["page_read"]
    # The documented frame went out mid-turn.
    frame = next(e for e in events if e["type"] == "page_action")
    assert frame["action"] == "page_read"
    assert frame["args"] == {}
    assert len(frame["id"]) == 32  # an unguessable uuid4 hex, not a counter
    # The loop resumed and fed the client's payload back as the tool result.
    assert json.loads(_tool_message(captured)["content"]) == payload
    assert any(e["type"] == "token" and e["text"] == "answered" for e in events)
    assert app.state.pending_page_actions == {}  # popped, no leak


async def test_client_failure_report_reaches_the_model_as_an_error(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    try:
        await _run_with_client_answer(
            app,
            client,
            {"messages": [{"role": "user", "content": "hi"}], "page_actions": ["page_read"]},
            {"ok": False, "error": "no bound tab"},
        )
    finally:
        app.dependency_overrides.clear()
    assert _tool_message(captured)["content"] == "error: no bound tab"
    assert app.state.pending_page_actions == {}


async def test_read_is_not_gated_by_the_confirm_policy(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 5b rule 2: a read runs ungated even under a write-confirming policy — no confirm prompt.
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    try:
        events = await _run_with_client_answer(
            app,
            client,
            {
                "messages": [{"role": "user", "content": "hi"}],
                "page_actions": ["page_read"],
                "confirm_tools": "writes",
            },
            {"ok": True, "result": {"text": "seen"}},
        )
    finally:
        app.dependency_overrides.clear()
    assert not [e for e in events if e["type"] == "confirm"]
    assert json.loads(_tool_message(captured)["content"]) == {"text": "seen"}


async def test_acting_is_gated_end_to_end_under_the_default_policy(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5b rule 2 through the real stack: no project, no policy, and the click still needs a human.

    ``confirm_tools`` is omitted, so this is the shipped default for a generic site — the case
    where riding the policy would have left an acting page action ungated.
    """
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured, "page_navigate", '{"url": "https://elsewhere.test/x"}')
    try:
        events = await _run_with_confirm(
            app,
            client,
            {"messages": [{"role": "user", "content": "go there"}], "page_actions": ["page_navigate"]},
            approve=False,
        )
    finally:
        app.dependency_overrides.clear()

    gate = next(e for e in events if e["type"] == "confirm")
    assert gate["tool"] == "page_navigate"
    assert gate["args"] == {"url": "https://elsewhere.test/x"}
    assert not [e for e in events if e["type"] == "page_action"]  # declined → never reached the tab
    assert _tool_message(captured)["content"] == "error: user declined this action"
    assert app.state.pending_page_actions == {}


async def test_approved_acting_action_then_runs_in_the_tab(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured, "page_navigate", '{"url": "https://a.test/data-entry"}')
    try:
        events = await _run_with_confirm(
            app,
            client,
            {"messages": [{"role": "user", "content": "go there"}], "page_actions": ["page_navigate"]},
            approve=True,
            answer={"ok": True, "result": {"url": "https://a.test/data-entry"}},
        )
    finally:
        app.dependency_overrides.clear()

    frame = next(e for e in events if e["type"] == "page_action")
    assert frame["action"] == "page_navigate"
    assert frame["args"] == {"url": "https://a.test/data-entry"}
    assert json.loads(_tool_message(captured)["content"]) == {"url": "https://a.test/data-entry"}
    assert app.state.pending_page_actions == {}
    assert app.state.pending_confirmations == {}


async def test_an_action_the_client_cannot_execute_is_never_offered(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # page_navigate is registered server-side before any client implements it. A client that
    # declares only page_read must be offered only page_read — an action nobody listens for buys
    # a guaranteed timeout, and a gated one would buy a prompt for something that cannot happen.
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    try:
        await _run_with_client_answer(
            app,
            client,
            {"messages": [{"role": "user", "content": "hi"}], "page_actions": ["page_read"]},
            {"ok": True, "result": {"text": "seen"}},
        )
    finally:
        app.dependency_overrides.clear()
    assert [t["function"]["name"] for t in captured["bodies"][0]["tools"]] == ["page_read"]


async def test_timeout_fails_safe_instead_of_hanging(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nobody answers: the turn must finish, the model must see a failure, and nothing may leak.
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    monkeypatch.setattr(app.state.settings, "tool_timeout", 0.05)
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    try:
        async with asyncio.timeout(10):  # a hang here is the bug this test exists for
            r = await client.post(
                "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "page_actions": ["page_read"]}
            )
        assert r.status_code == 200
        events = _events(r.text)
    finally:
        app.dependency_overrides.clear()

    assert _tool_message(captured)["content"].startswith("error: the browser did not answer")
    assert events[-1]["type"] == "done"  # the stream ends cleanly (the frame also carries finish_reason)
    assert app.state.pending_page_actions == {}


async def test_late_answer_after_a_timeout_is_404(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The id is popped on timeout, so a client that finally finishes cannot un-fail the action.
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    monkeypatch.setattr(app.state.settings, "tool_timeout", 0.05)
    captured: dict[str, Any] = {}
    _install_action_turn(monkeypatch, captured)
    holder: dict[str, Any] = {}
    task = asyncio.create_task(
        client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "page_actions": ["page_read"]})
    )
    try:
        async with asyncio.timeout(10):
            while not app.state.pending_page_actions and not task.done():
                await asyncio.sleep(0.005)
            holder["pid"] = next(iter(app.state.pending_page_actions), None)
            await task  # let the 0.05s bound expire and the turn finish
        r = await client.post("/api/chat/page-action", json={"id": holder["pid"], "ok": True, "result": {}})
    finally:
        task.cancel()
        app.dependency_overrides.clear()
    assert holder["pid"] is not None
    assert r.status_code == 404


async def test_id_is_popped_when_the_stream_is_cancelled(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """A disconnect mid-action must not leave the pending id (or the reservation) behind.

    Driven against the SSE generator directly rather than through the httpx harness: ASGITransport
    buffers the whole body, so cancelling a client call there leaves the app coroutine running
    detached and never delivers the cancellation to the stream. Iterating the body ourselves and
    cancelling that task reproduces what uvicorn does on a real disconnect — the CancelledError
    lands inside the generator, at the very ``wait_for`` this test is about.
    """
    _install_action_turn(monkeypatch, {})
    request = Request({"type": "http", "method": "POST", "path": "/api/chat", "headers": [], "app": app})
    response = await chat(
        ChatRequest(messages=[{"role": "user", "content": "hi"}], page_actions=["page_read"]),
        cast(Any, FakeManager()),
        request,
    )

    async def drain() -> None:
        async for _chunk in response.body_iterator:
            pass

    task = asyncio.create_task(drain())
    try:
        await _until(lambda: bool(app.state.pending_page_actions))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
    assert app.state.pending_page_actions == {}
    assert app.state.active_generations == 0  # the runtime reservation unwound with it
