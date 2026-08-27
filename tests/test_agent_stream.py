"""Direct tests for :func:`stabbur.agent._stream_turn` against real SSE bytes.

Every other agent test monkeypatches ``_stream_turn`` wholesale, so the actual SSE
parser (the ``data:`` framing, cross-chunk arg accumulation, ``include_usage`` final
chunk, reasoning-vs-content routing, and the HTTP-error detail path) has no direct
coverage. These drive the real parser through an ``httpx.AsyncClient`` backed by a
``MockTransport`` that streams hand-written frames in awkwardly-split byte chunks, so
line-boundary handling in ``aiter_lines`` is genuinely exercised, not assumed.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from stabbur import agent


def _chop(body: bytes, size: int) -> list[bytes]:
    """Split ``body`` into fixed-size byte chunks so frames straddle network boundaries.

    Yielding a ``data:`` line across two chunks proves the parser reassembles it via
    ``aiter_lines`` rather than assuming one frame per network read.
    """
    return [body[i : i + size] for i in range(0, len(body), size)] or [b""]


def _client(
    chunks: list[bytes], *, status_code: int = 200, content_type: str = "text/event-stream"
) -> httpx.AsyncClient:
    """An AsyncClient whose transport streams ``chunks`` as the response body."""

    def handler(request: httpx.Request) -> httpx.Response:
        async def stream() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return httpx.Response(status_code, headers={"content-type": content_type}, content=stream())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _error_client(status_code: int, body: bytes) -> httpx.AsyncClient:
    """An AsyncClient returning a non-streaming error body (read via ``resp.aread``)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tool_call_args_split_across_deltas_and_chunks() -> None:
    # A single tool call whose function.arguments arrives as several deltas (and whose
    # bytes are split across network chunks) must accumulate into the full, parseable JSON.
    frames = [
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        '"function":{"name":"get_weather","arguments":"{\\"loc"}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ation\\": \\"Par"}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"is\\"}"}}]}}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    # Tiny chunks so lines land mid-frame across reads.
    async with _client(_chop(body, 9)) as http:
        content, calls, usage, _finish = await agent._stream_turn(http, "http://runtime", {}, None)

    assert content == ""
    assert usage is None
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["name"] == "get_weather"
    # The accumulated fragments must reconstitute valid JSON — the whole point of buffering.
    assert json.loads(calls[0]["args"]) == {"location": "Paris"}


async def test_two_parallel_tool_calls_ordered_by_index() -> None:
    # Two parallel tool calls arrive interleaved by `index`; the parser keys them by index
    # and returns them index-ordered with each id/name/args kept distinct (not concatenated).
    frames = [
        '{"choices":[{"delta":{"tool_calls":['
        '{"index":0,"id":"a","function":{"name":"first","arguments":"{\\"x\\":"}},'
        '{"index":1,"id":"b","function":{"name":"second","arguments":"{\\"y\\":"}}]}}]}',
        # Second call's tail arrives before the first's — order must come from index, not arrival.
        '{"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"arguments":"2}"}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]}}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    async with _client(_chop(body, 13)) as http:
        _content, calls, _usage, _finish = await agent._stream_turn(http, "http://runtime", {}, None)

    assert [c["id"] for c in calls] == ["a", "b"]  # ordered by index
    assert [c["name"] for c in calls] == ["first", "second"]
    assert json.loads(calls[0]["args"]) == {"x": 1}
    assert json.loads(calls[1]["args"]) == {"y": 2}


async def test_include_usage_final_chunk_with_empty_choices() -> None:
    # The stream_options include_usage tail chunk carries `usage` and `choices: []`; usage
    # must be captured and the missing delta must not crash (choices[0] would IndexError).
    frames = [
        '{"choices":[{"delta":{"content":"hi"}}]}',
        '{"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":2,"total_tokens":13}}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    async with _client(_chop(body, 17)) as http:
        content, calls, usage, _finish = await agent._stream_turn(http, "http://runtime", {}, None)

    assert content == "hi"
    assert calls == []
    assert usage == {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13}


async def test_reasoning_content_routed_separately_from_content() -> None:
    # Reasoning models stream their thinking as `reasoning_content`; it must go to
    # on_reasoning and NOT be folded into content (which would otherwise leak thoughts
    # into the reply, or — when content is empty — blank the reply out).
    tokens: list[str] = []
    thoughts: list[str] = []
    frames = [
        '{"choices":[{"delta":{"reasoning_content":"let me think"}}]}',
        '{"choices":[{"delta":{"content":"The answer"}}]}',
        '{"choices":[{"delta":{"reasoning_content":" harder"}}]}',
        '{"choices":[{"delta":{"content":" is 42"}}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    async with _client(_chop(body, 11)) as http:
        content, _calls, _usage, _finish = await agent._stream_turn(
            http, "http://runtime", {}, tokens.append, thoughts.append
        )

    assert content == "The answer is 42"
    assert "".join(tokens) == "The answer is 42"  # content deltas -> on_token
    assert "".join(thoughts) == "let me think harder"  # reasoning deltas -> on_reasoning
    assert "think" not in content  # reasoning never bleeds into the reply


async def test_done_terminates_and_non_data_lines_skipped() -> None:
    # `[DONE]` ends the stream cleanly; SSE comments, blank lines, and non-`data:` fields
    # (event:, id:) are skipped rather than parsed as JSON. Anything after `[DONE]` is ignored.
    body = (
        b": this is an SSE comment\n"
        b"\n"
        b"event: message\n"
        b'data: {"choices":[{"delta":{"content":"one"}}]}\n'
        b"\n"
        b"id: 42\n"
        b'data: {"choices":[{"delta":{"content":" two"}}]}\n'
        b"\n"
        b"data: [DONE]\n"
        b"\n"
        b'data: {"choices":[{"delta":{"content":" IGNORED"}}]}\n'
    )
    async with _client(_chop(body, 5)) as http:
        content, calls, usage, _finish = await agent._stream_turn(http, "http://runtime", {}, None)

    assert content == "one two"  # frames after [DONE] are not consumed
    assert calls == []
    assert usage is None


async def test_http_error_raises_with_body_detail() -> None:
    # A >= 400 response must raise HTTPStatusError whose message carries the runtime's JSON
    # detail (agent.py reads the body so a context-overflow cause isn't discarded by raise_for_status).
    body = json.dumps({"error": {"message": "the prompt exceeds the context window"}}).encode()
    async with _error_client(400, body) as http:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await agent._stream_turn(http, "http://runtime", {}, None)

    message = str(excinfo.value)
    assert "runtime returned 400" in message
    assert "context window" in message  # the real cause is surfaced, not swallowed


async def test_finish_reason_is_carried_out_of_the_stream() -> None:
    # A reply the runtime cut at max_tokens looks exactly like a complete one from the frames
    # alone — and when the whole budget went to reasoning_content, exactly like an empty one.
    # finish_reason is the only thing that separates them, so the parser must not drop it.
    frames = [
        '{"choices":[{"delta":{"reasoning_content":"thinking and thinking"},"finish_reason":null}]}',
        '{"choices":[{"delta":{},"finish_reason":"length"}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    async with _client(_chop(body, 15)) as http:
        content, calls, _usage, finish = await agent._stream_turn(http, "http://runtime", {}, None)

    assert content == ""  # not one content delta: the visible reply is empty
    assert calls == []
    assert finish == "length"  # ...and this is what says it was cut off rather than answered


async def test_tool_call_deltas_without_index_still_accumulate() -> None:
    # OpenAI identifies which call a delta continues with `index`; a compatible server that omits
    # it used to raise KeyError and kill the whole stream. The first delta names the call, the
    # rest are arguments-only fragments of it.
    frames = [
        '{"choices":[{"delta":{"tool_calls":[{"id":"call_1","function":{"name":"get_weather","arguments":""}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"{\\"city\\": "}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"\\"Oslo\\"}"}}]}}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    async with _client(_chop(body, 21)) as http:
        _content, calls, _usage, _finish = await agent._stream_turn(http, "http://runtime", {}, None)

    assert len(calls) == 1  # one call, not three fragments
    assert calls[0]["id"] == "call_1"
    assert calls[0]["name"] == "get_weather"
    assert json.loads(calls[0]["args"]) == {"city": "Oslo"}


async def test_parallel_calls_without_ids_get_distinct_ones() -> None:
    # A server that streams no `id` left every call with `tool_call_id: ""`, so two calls in one
    # round produced two indistinguishable tool answers — a model cannot match either to what it
    # asked for, and nothing about the exchange looks broken.
    frames = [
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"{}"}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"name":"write","arguments":"{}"}}]}}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    async with _client(_chop(body, 19)) as http:
        _content, calls, _usage, _finish = await agent._stream_turn(http, "http://runtime", {}, None)

    ids = [c["id"] for c in calls]
    assert [c["name"] for c in calls] == ["read", "write"]
    assert all(ids)  # every call is named
    assert len(set(ids)) == 2  # ...and no two calls share a name


async def test_a_minted_id_never_shadows_one_the_server_sent() -> None:
    # Mixed: one call arrives with an id, the other without. The minted id must not collide with
    # the real one, or the model's two tool answers would again be indistinguishable.
    collision = f"{agent._SYNTHETIC_ID}0"
    frames = [
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"{}"}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":1,"id":"'
        + collision
        + '","function":{"name":"write","arguments":"{}"}}]}}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    async with _client(_chop(body, 23)) as http:
        _content, calls, _usage, _finish = await agent._stream_turn(http, "http://runtime", {}, None)

    ids = [c["id"] for c in calls]
    assert ids[1] == collision  # the server's own id is kept verbatim
    assert len(set(ids)) == 2


async def test_malformed_frames_are_skipped_rather_than_fatal() -> None:
    # Every one of these used to be an unhandled exception mid-generation: a frame that is not
    # JSON, a chunk that is not an object, choices of the wrong shape, a delta that is not a
    # mapping, and a tool_calls entry that is not one either. A bad frame costs its own delta.
    frames = [
        "not json at all",
        "[1, 2, 3]",
        '{"choices":"nope"}',
        '{"choices":[]}',
        '{"choices":[{"delta":"nope"}]}',
        '{"choices":[{"delta":{"tool_calls":["nope",{}]}}]}',
        '{"choices":[{"delta":{"content":"still here"},"finish_reason":"stop"}]}',
    ]
    body = "".join(f"data: {f}\n\n" for f in frames).encode() + b"data: [DONE]\n\n"
    tokens: list[str] = []
    async with _client(_chop(body, 12)) as http:
        content, calls, _usage, finish = await agent._stream_turn(http, "http://runtime", {}, tokens.append)

    assert content == "still here"  # the stream survived every malformed frame before it
    assert tokens == ["still here"]
    assert calls == []  # and no phantom call was conjured from the empty tool_calls entry
    assert finish == "stop"
