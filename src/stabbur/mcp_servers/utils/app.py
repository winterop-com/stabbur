"""A FastMCP server of core utility tools — text, encoding, hashing, JSON, and math.

Everything is stdlib and deterministic (no network, no randomness, no filesystem), so the
tools are safe to expose and easy to benchmark. ``calc`` evaluates arithmetic through a
restricted AST walker — never Python ``eval`` — so only numbers and arithmetic operators
are allowed.

Run standalone over stdio: ``stabbur-mcp-utils`` (or ``python -m stabbur.mcp_servers.utils``). Point
stabbur at it with ``stabbur chat --mcp utils``.
"""

import ast
import asyncio
import base64
import binascii
import hashlib
import json
import math
import operator
import re
import statistics
import urllib.parse
from collections.abc import Callable

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("stabbur-utils")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


# --- text ------------------------------------------------------------------


@mcp.tool
def reverse_text(text: str) -> str:
    """Reverse a string, character by character."""
    return text[::-1]


@mcp.tool
def to_upper(text: str) -> str:
    """Uppercase a string."""
    return text.upper()


@mcp.tool
def to_lower(text: str) -> str:
    """Lowercase a string."""
    return text.lower()


@mcp.tool
def title_case(text: str) -> str:
    """Title-case a string (capitalize each word)."""
    return text.title()


@mcp.tool
def slugify(text: str) -> str:
    """Make a URL slug: lowercase, non-alphanumeric runs to single hyphens, trimmed."""
    return _SLUG_STRIP.sub("-", text.lower()).strip("-")


@mcp.tool
def word_count(text: str) -> int:
    """Count whitespace-separated words in a string."""
    return len(text.split())


@mcp.tool
def char_count(text: str, include_spaces: bool = True) -> int:
    """Count characters, optionally excluding whitespace."""
    return len(text) if include_spaces else len("".join(text.split()))


# --- encoding --------------------------------------------------------------


@mcp.tool
def base64_encode(text: str) -> str:
    """Base64-encode UTF-8 text."""
    return base64.b64encode(text.encode()).decode()


@mcp.tool
def base64_decode(data: str) -> str:
    """Decode Base64 back to UTF-8 text."""
    try:
        return base64.b64decode(data, validate=True).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"not valid Base64 text: {exc}") from exc


@mcp.tool
def url_encode(text: str) -> str:
    """Percent-encode a string for use in a URL."""
    return urllib.parse.quote(text, safe="")


@mcp.tool
def url_decode(text: str) -> str:
    """Decode a percent-encoded URL string."""
    return urllib.parse.unquote(text)


@mcp.tool
def hex_encode(text: str) -> str:
    """Hex-encode UTF-8 text."""
    return text.encode().hex()


@mcp.tool
def hex_decode(data: str) -> str:
    """Decode hex back to UTF-8 text."""
    try:
        return bytes.fromhex(data).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"not valid hex text: {exc}") from exc


# --- hashing ---------------------------------------------------------------


@mcp.tool
def sha256(text: str) -> str:
    """The SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(text.encode()).hexdigest()


@mcp.tool
def md5(text: str) -> str:
    """The MD5 hex digest of UTF-8 text (checksums only, not for security)."""
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


# --- json ------------------------------------------------------------------


def _parse_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


@mcp.tool
def json_format(json_text: str, indent: int = 2) -> str:
    """Pretty-print JSON with a given indent, keys sorted."""
    return json.dumps(_parse_json(json_text), indent=indent, sort_keys=True)


@mcp.tool
def json_minify(json_text: str) -> str:
    """Minify JSON (no extra whitespace)."""
    return json.dumps(_parse_json(json_text), separators=(",", ":"))


@mcp.tool
def json_keys(json_text: str) -> list[str]:
    """The top-level keys of a JSON object."""
    data = _parse_json(json_text)
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object, so it has no top-level keys")
    return list(data.keys())


# --- math ------------------------------------------------------------------


_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node: ast.AST) -> float:
    """Evaluate a numeric AST node; reject anything that isn't arithmetic on numbers."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1_000_000):
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only numbers and + - * / // % ** are allowed")


@mcp.tool
def calc(expression: str) -> float:
    """Evaluate an arithmetic expression (``+ - * / // % **``, parentheses). Safe: no eval."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"could not parse expression: {exc}") from exc
    try:
        return float(_eval(tree.body))
    except ZeroDivisionError as exc:
        raise ValueError(f"division by zero: {exc}") from exc


@mcp.tool
def factorial(n: int) -> int:
    """The factorial of a non-negative integer (n <= 1000)."""
    if not 0 <= n <= 1000:
        raise ValueError("n must be between 0 and 1000")
    return math.factorial(n)


@mcp.tool
def gcd(a: int, b: int) -> int:
    """The greatest common divisor of two integers."""
    return math.gcd(a, b)


@mcp.tool
def lcm(a: int, b: int) -> int:
    """The least common multiple of two integers."""
    return math.lcm(a, b)


@mcp.tool
def is_prime(number: int) -> bool:
    """Whether an integer is prime (number <= 1,000,000,000)."""
    if number > 1_000_000_000:
        raise ValueError("number too large (max 1,000,000,000)")
    if number < 2:
        return False
    if number % 2 == 0:
        return number == 2
    for divisor in range(3, math.isqrt(number) + 1, 2):
        if number % divisor == 0:
            return False
    return True


@mcp.tool
def to_base(number: int, base: int) -> str:
    """Represent an integer in a base from 2 to 36 (lowercase digits)."""
    if not 2 <= base <= 36:
        raise ValueError("base must be between 2 and 36")
    if number == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    sign = "-" if number < 0 else ""
    n = abs(number)
    out = ""
    while n:
        n, rem = divmod(n, base)
        out = digits[rem] + out
    return sign + out


@mcp.tool
def from_base(digits: str, base: int) -> int:
    """Parse an integer written in a base from 2 to 36."""
    if not 2 <= base <= 36:
        raise ValueError("base must be between 2 and 36")
    try:
        return int(digits, base)
    except ValueError as exc:
        raise ValueError(f"{digits!r} is not a base-{base} integer") from exc


# --- stats -----------------------------------------------------------------


@mcp.tool
def mean(numbers: list[float]) -> float:
    """The arithmetic mean of a non-empty list of numbers."""
    if not numbers:
        raise ValueError("need at least one number")
    return statistics.fmean(numbers)


@mcp.tool
def median(numbers: list[float]) -> float:
    """The median of a non-empty list of numbers."""
    if not numbers:
        raise ValueError("need at least one number")
    return statistics.median(numbers)


@mcp.tool
def total(numbers: list[float]) -> float:
    """The sum of a list of numbers."""
    return math.fsum(numbers)


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn); quiet on Ctrl-C."""
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
