"""Tests for the core-utils MCP server (in-memory client)."""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from kodo_mcp_utils import mcp


async def _call(name: str, **kw: Any) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, kw)).data


async def test_server_exposes_a_rich_toolset() -> None:
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert {"reverse_text", "base64_encode", "sha256", "calc", "to_base", "json_keys"} <= names


async def test_text_tools() -> None:
    assert await _call("reverse_text", text="hello") == "olleh"
    assert await _call("slugify", text="Hello, World!") == "hello-world"
    assert await _call("word_count", text="the quick brown fox") == 4
    assert await _call("char_count", text="a b c", include_spaces=False) == 3


async def test_encoding_roundtrips_and_rejects_bad_input() -> None:
    assert await _call("base64_encode", text="hi") == "aGk="
    assert await _call("base64_decode", data="aGk=") == "hi"
    assert await _call("hex_encode", text="hi") == "6869"
    assert await _call("url_encode", text="a b&c") == "a%20b%26c"
    with pytest.raises(ToolError):
        await _call("base64_decode", data="not!base64!")


async def test_hashing_is_correct() -> None:
    # Known SHA-256 of "abc".
    assert await _call("sha256", text="abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert len(await _call("md5", text="abc")) == 32


async def test_json_tools() -> None:
    assert await _call("json_minify", json_text='{"a": 1,  "b": 2}') == '{"a":1,"b":2}'
    assert await _call("json_keys", json_text='{"x": 1, "y": 2}') == ["x", "y"]
    with pytest.raises(ToolError):
        await _call("json_keys", json_text="[1, 2, 3]")  # not an object
    with pytest.raises(ToolError):
        await _call("json_format", json_text="{bad}")


async def test_calc_is_safe_and_correct() -> None:
    assert await _call("calc", expression="6 * 7") == 42.0
    assert await _call("calc", expression="2 ** 10") == 1024.0
    assert await _call("calc", expression="(1 + 2) * 3 - 4 / 2") == 7.0
    with pytest.raises(ToolError):  # no names / calls / eval
        await _call("calc", expression="__import__('os').system('echo hi')")
    with pytest.raises(ToolError):  # exponent guard
        await _call("calc", expression="9 ** 99999")


async def test_number_tools() -> None:
    assert await _call("gcd", a=48, b=36) == 12
    assert await _call("lcm", a=4, b=6) == 12
    assert await _call("factorial", n=5) == 120
    assert await _call("is_prime", number=97) is True
    assert await _call("is_prime", number=1) is False
    assert await _call("to_base", number=255, base=16) == "ff"
    assert await _call("from_base", digits="ff", base=16) == 255
    with pytest.raises(ToolError):
        await _call("to_base", number=5, base=99)


async def test_stats_tools() -> None:
    assert await _call("mean", numbers=[1, 2, 3, 4]) == 2.5
    assert await _call("median", numbers=[1, 3, 2]) == 2
    assert await _call("total", numbers=[0.1, 0.2]) == pytest.approx(0.3)
    with pytest.raises(ToolError):
        await _call("mean", numbers=[])


async def test_calc_division_by_zero_is_a_clean_error() -> None:
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("calc", {"expression": "1/0"})
