"""Tests for the benchmark core: suite loading, code extraction, and the Docker executor.

The executor tests spawn real containers, so they're marked ``slow`` (excluded from the
default ``make test``; run with ``make test-slow`` / ``pytest -m slow``). They skip when
Docker isn't available so the suite still passes on a machine without it.
"""

from pathlib import Path

import pytest
from kodo_mcp_benchmark import core

pytestmark = pytest.mark.filterwarnings("ignore")

# --- fast: no Docker -------------------------------------------------------


def test_suites_load_and_language_propagates() -> None:
    names = core.available_suites()
    assert {"python", "rust", "tools-datetime"} <= set(names)
    py = core.load_suite("python")
    assert py.language == "python" and py.type == "code"
    assert py.problems and all(p.language == "python" for p in py.problems)  # inherited from the suite
    assert {p.difficulty for p in py.problems} == {"basics", "intermediate", "advanced", "expert"}
    assert core.load_suite("rust").problems[0].language == "rust"


def test_tool_suite_loads_with_expectations() -> None:
    suite = core.load_suite("tools-datetime")
    assert suite.type == "tool"
    problem = next(p for p in suite.problems if p.id == "day-of-week")
    assert problem.type == "tool"  # propagated from the suite
    assert problem.expect_tool == "datetime__day_of_week"
    assert problem.servers == ["kodo-mcp-datetime"]


def test_unknown_suite_errors() -> None:
    with pytest.raises(FileNotFoundError):
        core.load_suite("does-not-exist")


def test_extract_code_prefers_tagged_then_last_block() -> None:
    # Language-tagged block wins even if a later untagged block exists.
    text = "try this:\n```python\nprint(1)\n```\nor maybe\n```\nprint(2)\n```"
    assert core.extract_code(text, "python") == "print(1)"
    # No tag match -> last fenced block.
    assert core.extract_code("```\na\n```\n```\nb\n```", "python") == "b"
    # No fence -> whole text, trimmed.
    assert core.extract_code("  just code  ", "python") == "just code"


def test_docker_available_returns_bool() -> None:
    assert isinstance(core.docker_available(), bool)


def test_unsupported_language_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        core.run_code("cobol", "DISPLAY 'HI'.")


def test_score_tool_requires_correct_call_and_answer() -> None:
    problem = next(p for p in core.load_suite("tools-datetime").problems if p.id == "day-of-week")
    good = core.score_tool(problem, [("datetime__day_of_week", {"date": "2026-07-04"})], "It's a Saturday.", 0.1)
    assert good.passed and good.type == "tool"
    # Right answer but wrong/missing tool call -> fail (isolates tool-calling ability).
    assert not core.score_tool(problem, [], "Saturday", 0.1).passed
    # Right tool call but answer missing the expected value -> fail (strictest scoring).
    assert not core.score_tool(problem, [("datetime__day_of_week", {"date": "2026-07-04"})], "Sunday", 0.1).passed
    # Wrong args -> fail.
    assert not core.score_tool(problem, [("datetime__day_of_week", {"date": "2026-01-01"})], "Saturday", 0.1).passed


def test_results_roundtrip_and_leaderboard(tmp_path: Path) -> None:
    suite = core.load_suite("python")
    results = [
        core.ProblemResult(
            problem_id="a", difficulty="basics", type="code", passed=True, cases=[], gen_s=0.5, exec_s=0.2
        ),
        core.ProblemResult(problem_id="b", difficulty="advanced", type="code", passed=False, cases=[], gen_s=0.3),
    ]
    assert results[0].duration_s == pytest.approx(0.7)  # gen + exec
    record = core.make_record(suite, "pub/Model-X", results, load_s=3.0, timestamp="2026-07-02T10:00:00")
    path = core.save_run(record, tmp_path)
    assert path.exists()
    loaded = core.load_results(tmp_path)
    assert len(loaded) == 1 and loaded[0].passed == 1 and loaded[0].total == 2
    assert loaded[0].load_s == 3.0 and loaded[0].gen_s == pytest.approx(0.8)  # timing survives the roundtrip
    table = core.render_leaderboard(loaded)
    assert "pub/Model-X" in table and "python" in table and "50%" in table
    assert "Performance" in table and "3.0s" in table  # load time in the perf table
    assert core.render_leaderboard([]).startswith("_No benchmark results")


# --- slow: real Docker containers ------------------------------------------

_needs_docker = pytest.mark.skipif(not core.docker_available(), reason="docker not available")


@pytest.mark.slow
@_needs_docker
def test_python_solution_scored_pass_and_fail() -> None:
    problem = next(p for p in core.load_suite("python").problems if p.id == "sum-two")
    good = core.evaluate(problem, "a, b = map(int, input().split())\nprint(a + b)")
    assert good.passed and all(c.passed for c in good.cases)
    bad = core.evaluate(problem, "print(999)")
    assert not bad.passed


@pytest.mark.slow
@_needs_docker
def test_sandbox_blocks_network_and_enforces_timeout() -> None:
    net = core.run_code("python", "import socket; socket.create_connection(('1.1.1.1', 53), 2)")
    assert net.exit_code != 0  # --network=none
    hang = core.run_code("python", "while True: pass", timeout_s=3)
    assert hang.timed_out


@pytest.mark.slow
@_needs_docker
def test_rust_compiles_runs_and_reports_compile_errors() -> None:
    problem = next(p for p in core.load_suite("rust").problems if p.id == "reverse-string")
    good = (
        "use std::io::*;\n"
        "fn main() { let mut s = String::new(); stdin().read_line(&mut s).unwrap(); "
        'println!("{}", s.trim().chars().rev().collect::<String>()); }'
    )
    assert core.evaluate(problem, good).passed
    broken = core.evaluate(problem, 'fn main() { let x: i32 = "nope"; }')
    assert not broken.passed and broken.cases[0].error  # compile error captured, not a crash
