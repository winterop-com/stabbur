"""Tests for the benchmark core: suite loading, code extraction, and the Docker executor.

The executor tests spawn real containers, so they're marked ``slow`` (excluded from the
default ``make test``; run with ``make test-slow`` / ``pytest -m slow``). They skip when
Docker isn't available so the suite still passes on a machine without it.
"""

from pathlib import Path

import pytest

from stabbur.benchmark import core

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


def test_qualifies_gates_by_capability_and_tag() -> None:
    from typing import Any, cast

    from stabbur.benchmark.plugin import _qualifies
    from stabbur.plugins import PluginContext

    class _Ctx:
        def __init__(self, tools: bool, tags: list[str]) -> None:
            self._tools, self._tags = tools, tags

        def supports_tools(self, model: Any) -> bool:
            return self._tools

        def model_tags(self, model: Any) -> list[str]:
            return self._tags

    def ctx(tools: bool, tags: list[str]) -> PluginContext:
        return cast(PluginContext, _Ctx(tools, tags))

    code = core.load_suite("python")  # requires_tag="coding"
    tool = core.load_suite("tools-datetime")  # type == "tool"
    m: Any = object()
    # Code suite: needs the 'coding' tag.
    assert _qualifies(ctx(True, ["coding"]), code, m)[0]
    assert not _qualifies(ctx(True, []), code, m)[0]
    # Tool suite: needs tool capability OR a 'tools' tag override.
    assert _qualifies(ctx(True, []), tool, m)[0]
    assert _qualifies(ctx(False, ["tools"]), tool, m)[0]
    assert not _qualifies(ctx(False, []), tool, m)[0]


def test_tool_suite_loads_with_expectations() -> None:
    suite = core.load_suite("tools-datetime")
    assert suite.type == "tool"
    assert suite.requires_tag == ""  # tool suites gate by capability, not a tag
    problem = next(p for p in suite.problems if p.id == "day-of-week")
    assert problem.type == "tool"  # propagated from the suite
    assert problem.expect_tool == "datetime__day_of_week"
    assert problem.servers == ["stabbur-mcp-datetime"]


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


# --- stateful DHIS2 write scoring -----------------------------------------


class _StubReader:
    """A stand-in :class:`stabbur.benchmark.core.StateReader` returning canned live counts."""

    def __init__(self, counts: dict[tuple[str, str], int] | None = None, unreachable: bool = False) -> None:
        self._counts = counts or {}
        self._unreachable = unreachable

    def count(self, resource: str, name: str) -> int | None:
        return None if self._unreachable else self._counts.get((resource, name), 0)


def _call(argv: list[str], result: str = '{"exit_code": 0, "stdout": "{\\"id\\": \\"abc\\"}"}') -> tuple[str, dict]:
    """A recorded (name, args) tool call with a d2w argv and a result under the reserved key."""
    return ("env__dhis2_cli", {"args": argv, core.RESULT_ARG_KEY: result})


def _write_problem(problem_id: str = "de-create-delete") -> core.Problem:
    return next(p for p in core.load_suite("tools-dhis2-write").problems if p.id == problem_id)


def test_write_suite_loads_with_end_state_expectations() -> None:
    suite = core.load_suite("tools-dhis2-write")
    assert suite.type == "tool"
    problem = _write_problem("de-create-delete")
    assert problem.expect_absent == [core.ExpectAbsent(type="dataElements", name="STABBUR_DE1")]
    # A problem that creates two objects lists both.
    two = _write_problem("deg-add-member-delete")
    assert {s.type for s in two.expect_absent} == {"dataElements", "dataElementGroups"}


def test_stateful_pass_only_when_created_and_absent_at_end() -> None:
    problem = _write_problem("de-create-delete")
    calls = [
        _call(["metadata", "data-elements", "create", "--name", "STABBUR_DE1", "--short-name", "STABBUR_DE1"]),
        _call(["metadata", "data-elements", "delete", "abc"]),
    ]
    reader = _StubReader({("dataElements", "STABBUR_DE1"): 0})  # gone at end
    assert core.score_tool_stateful(problem, calls, "LIFECYCLE_OK", 0.1, reader).passed


def test_stateful_fails_when_residue_remains() -> None:
    problem = _write_problem("de-create-delete")
    calls = [_call(["metadata", "data-elements", "create", "--name", "STABBUR_DE1"])]
    reader = _StubReader({("dataElements", "STABBUR_DE1"): 1})  # created but never deleted
    assert not core.score_tool_stateful(problem, calls, "LIFECYCLE_OK", 0.1, reader).passed


def test_stateful_vacuous_pass_is_gone() -> None:
    problem = _write_problem("de-create-delete")
    reader = _StubReader({("dataElements", "STABBUR_DE1"): 0})  # trivially absent
    # Old scoring passed on "one tool call + token"; now doing NOTHING fails despite absence.
    assert not core.score_tool_stateful(problem, [], "LIFECYCLE_OK", 0.1, reader).passed
    # Calling the tool for a READ only (no create verb) + emitting the token also fails now.
    read_only = [_call(["metadata", "list", "dataElements", "--count"])]
    assert not core.score_tool_stateful(problem, read_only, "LIFECYCLE_OK", 0.1, reader).passed


def test_stateful_fails_when_create_result_errored() -> None:
    problem = _write_problem("de-create-delete")
    # A create that returned a non-zero d2w exit code is not evidence the object existed.
    calls = [_call(["metadata", "data-elements", "create", "--name", "STABBUR_DE1"], result='{"exit_code": 1}')]
    reader = _StubReader({("dataElements", "STABBUR_DE1"): 0})
    assert not core.score_tool_stateful(problem, calls, "LIFECYCLE_OK", 0.1, reader).passed


def test_stateful_fails_on_decoy_create_of_unrelated_object() -> None:
    # A create+delete of an UNRELATED object must not pass a problem whose target was never touched
    # (and is thus trivially absent). The create has to target the EXPECTED object.
    problem = _write_problem("de-create-delete")  # expects dataElements/STABBUR_DE1
    calls = [
        _call(["metadata", "data-elements", "create", "--name", "STABBUR_DECOY"]),
        _call(["metadata", "data-elements", "delete", "abc"]),
    ]
    reader = _StubReader({("dataElements", "STABBUR_DE1"): 0})  # STABBUR_DE1 trivially absent (never created)
    assert not core.score_tool_stateful(problem, calls, "LIFECYCLE_OK", 0.1, reader).passed


def test_stateful_ignores_verbs_that_are_only_flag_values() -> None:
    # "create"/"delete" appearing as a FLAG VALUE (not in the command path before the first --flag)
    # must not count as a write verb, so a pure read whose argument happens to be "create" fails -
    # both against a live reader and in the offline (degraded) fallback.
    problem = _write_problem("de-create-delete")
    calls = [_call(["metadata", "list", "dataElements", "--filter", "create"])]
    reader = _StubReader({("dataElements", "STABBUR_DE1"): 0})
    assert not core.score_tool_stateful(problem, calls, "LIFECYCLE_OK", 0.1, reader).passed
    assert not core.score_tool_stateful(problem, calls, "LIFECYCLE_OK", 0.1, None).passed


def test_stateful_degrades_gracefully_without_a_reader() -> None:
    problem = _write_problem("de-create-delete")
    create_and_delete = [
        _call(["metadata", "data-elements", "create", "--name", "STABBUR_DE1"]),
        _call(["metadata", "data-elements", "delete", "abc"]),
    ]
    # No reader (offline): fall back to trace verbs + token — both present -> pass.
    assert core.score_tool_stateful(problem, create_and_delete, "LIFECYCLE_OK", 0.1, None).passed
    # A reachable-but-down instance (count -> None) also degrades to the same fallback.
    assert core.score_tool_stateful(
        problem, create_and_delete, "LIFECYCLE_OK", 0.1, _StubReader(unreachable=True)
    ).passed
    # Offline with only a create (no delete verb) -> fail.
    create_only = [_call(["metadata", "data-elements", "create", "--name", "STABBUR_DE1"])]
    assert not core.score_tool_stateful(problem, create_only, "LIFECYCLE_OK", 0.1, None).passed


def test_read_suite_scoring_unchanged_with_result_key_present() -> None:
    # The recording loop now stashes results under a reserved key; the read scorer must ignore it.
    problem = next(p for p in core.load_suite("tools-datetime").problems if p.id == "day-of-week")
    calls = [("datetime__day_of_week", {"date": "2026-07-04", core.RESULT_ARG_KEY: "Saturday"})]
    assert core.score_tool(problem, calls, "It's a Saturday.", 0.1).passed


def test_sweep_only_touches_stabbur_prefixed_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur.benchmark import dhis2_state

    reader = dhis2_state.Dhis2StateReader(
        dhis2_state.Dhis2Profile(base_url="http://localhost:8080", username="admin", password="district")
    )
    # `like` is a substring match, so a non-STABBUR_ object can slip into the candidate list.
    found = [
        {"id": "u1", "name": "STABBUR_DE1"},
        {"id": "u2", "name": "ORG_STABBUR_DE1"},  # contains STABBUR_ but does NOT start with it
        {"id": "u3", "name": "STABBUR_DE2"},
    ]
    deleted: list[str] = []

    def _fake_delete(resource: str, uid: str) -> bool:
        deleted.append(uid)
        return True

    monkeypatch.setattr(reader, "_find", lambda resource, name: found)
    monkeypatch.setattr(reader, "_delete", _fake_delete)
    removed = reader.sweep("dataElements")
    assert removed == ["u1", "u3"] and deleted == ["u1", "u3"]  # the non-prefixed one is untouched


def test_sweep_skips_when_instance_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur.benchmark import dhis2_state

    reader = dhis2_state.Dhis2StateReader(dhis2_state.Dhis2Profile(base_url="http://x", username="a", password="b"))
    monkeypatch.setattr(reader, "_find", lambda resource, name: None)  # unreachable
    assert reader.sweep("dataElements") == []
    assert dhis2_state.sweep_residue(reader, ["dataElements", "dataElementGroups"]) == {
        "dataElements": [],
        "dataElementGroups": [],
    }


def test_profile_resolution_from_servers_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur.benchmark import dhis2_state

    monkeypatch.delenv("STABBUR_BENCH_DHIS2_URL", raising=False)
    servers = ["env DHIS2_PROFILE=local_basic uvx dhis2w-mcp-bridge"]
    assert dhis2_state.profile_from_servers(servers) == "local_basic"
    profiles = tmp_path / "profiles.toml"
    profiles.write_text(
        '[profiles.local_basic]\nbase_url = "http://localhost:8080/"\nusername = "admin"\npassword = "district"\n'
    )
    resolved = dhis2_state.load_profile("local_basic", profiles)
    assert resolved is not None and resolved.base_url == "http://localhost:8080" and resolved.username == "admin"
    assert dhis2_state.load_profile("missing", profiles) is None


def test_sweep_types_derived_from_expect_absent() -> None:
    from stabbur.benchmark import dhis2_state

    suite = core.load_suite("tools-dhis2-write")
    assert dhis2_state.sweep_types(list(suite.problems)) == [
        "dataElementGroups",
        "dataElements",
        "indicatorGroups",
        "indicators",
    ]


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
