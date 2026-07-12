"""Benchmark core: suites and scoring — no MCP, no heim deps.

A *suite* is a set of coding *problems*. Each problem gives a prompt plus hidden test
cases; a candidate solution is a full program that reads stdin / argv and writes stdout.
We run each candidate in a throwaway Docker container (via :mod:`heim_sandbox`) and compare
its trimmed stdout to the expected output.
"""

from __future__ import annotations

import re
import tomllib
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol

# Re-exported (``import X as X``) so callers can keep importing them from ``core`` — heim's
# driver, the MCP app, and tests do.
from heim_sandbox import RunResult as RunResult
from heim_sandbox import docker_available as docker_available
from heim_sandbox import run_code as run_code
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

Difficulty = Literal["basics", "intermediate", "advanced", "expert"]
ProblemType = Literal["code", "tool"]

#: Reserved args key under which heim's recording agent-loop (``heim.cli._app.run_agent``)
#: stashes each tool call's result text, so a stateful scorer can inspect outcomes without
#: changing ``run_agent``'s ``(name, args)`` shape — the ``PluginContext`` protocol and the
#: read suite both rely on that shape. Expected args never use this key, so the read suite's
#: superset :func:`_args_match` is unaffected. Kept in sync with the same literal in
#: ``heim.cli._app`` (a deliberate cross-package literal — heim must not import this package).
RESULT_ARG_KEY = "__heim_tool_result__"


# --- suite / result models -------------------------------------------------


class TestCase(BaseModel):
    """One hidden check: feed ``stdin`` (and ``args``), expect ``expected_stdout``."""

    model_config = ConfigDict(frozen=True)

    stdin: str = ""
    args: list[str] = Field(default_factory=list)
    expected_stdout: str


class ExpectAbsent(BaseModel):
    """A metadata object a write lifecycle creates then deletes: verify it is ABSENT at the end.

    ``type`` is the DHIS2 API resource (e.g. ``dataElements``); ``name`` is the object's name
    or ``HEIM_`` prefix, queried live with ``filter=name:like:<name>``. A stateful ``tool``
    problem passes only if the instance reports zero matches at the end of the run — real proof
    the object was created-then-deleted, not a token the model merely claimed to emit.
    """

    model_config = ConfigDict(frozen=True)

    type: str
    name: str


class Problem(BaseModel):
    """A benchmark task, either a ``code`` or a ``tool`` problem.

    A ``code`` problem is scored by running a program against its test cases; a ``tool``
    problem by whether the model calls the expected MCP tool and answers correctly. A ``tool``
    problem carrying :attr:`expect_absent` is scored *statefully* — verified against real
    end-state on the live instance (see :func:`score_tool_stateful`).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    prompt: str
    type: ProblemType = "code"
    difficulty: Difficulty = "basics"
    language: str = ""
    timeout_s: float = 10.0
    # code problems: hidden stdin/stdout checks.
    tests: list[TestCase] = Field(default_factory=list)
    # tool problems: attach these MCP servers, expect this namespaced tool called with
    # (a superset of) these args, and the final answer to contain this text.
    servers: list[str] = Field(default_factory=list)
    expect_tool: str = ""
    expect_args: dict[str, Any] = Field(default_factory=dict)
    expect_answer_contains: str = ""
    # stateful (write) tool problems: metadata objects the lifecycle creates then deletes.
    # Their presence switches scoring to :func:`score_tool_stateful` (real end-state, not a
    # self-reported token); each must be ABSENT on the live instance at the end of the run.
    expect_absent: list[ExpectAbsent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> Problem:
        """Require the fields each problem type actually needs."""
        if self.type == "code" and not self.tests:
            raise ValueError(f"code problem {self.id!r} needs at least one test case")
        if self.type == "tool" and not self.expect_tool:
            raise ValueError(f"tool problem {self.id!r} needs an expect_tool")
        return self


class Suite(BaseModel):
    """A named set of problems (one language, one type)."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    type: ProblemType = "code"
    language: str
    # On a `heim benchmark run --all`, only models carrying this library tag are run against
    # this suite (empty = run all). Tool suites also require the tools capability (see run()).
    requires_tag: str = ""
    problems: list[Problem]

    @model_validator(mode="before")
    @classmethod
    def _propagate(cls, data: object) -> object:
        """Fill each problem's type/language from the suite's, so the TOML sets them once."""
        if isinstance(data, dict):
            for problem in data.get("problems", []):
                if isinstance(problem, dict):
                    problem.setdefault("language", data.get("language"))
                    problem.setdefault("type", data.get("type", "code"))
        return data


class CaseResult(BaseModel):
    """Whether one test case passed, with the expected/actual output for diffing."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    expected: str
    actual: str
    error: str = ""  # stderr excerpt / "timed out" when it failed to run cleanly


class ProblemResult(BaseModel):
    """A problem's verdict: passed only if every case passed, with timing split out."""

    model_config = ConfigDict(frozen=True)

    problem_id: str
    difficulty: str = "basics"
    type: str = "code"
    passed: bool
    cases: list[CaseResult]
    gen_s: float = 0.0  # model response time (waiting on the model)
    exec_s: float = 0.0  # sandbox execution / verification time

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_s(self) -> float:
        """Total wall-clock for this problem (generation + execution)."""
        return self.gen_s + self.exec_s


class SuiteReport(BaseModel):
    """Aggregate over a suite run — the scored, serializable summary."""

    model_config = ConfigDict(frozen=True)

    suite: str
    model: str = ""
    results: list[ProblemResult]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        """Number of problems attempted."""
        return len(self.results)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> int:
        """Number of problems fully passed."""
        return sum(r.passed for r in self.results)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score(self) -> float:
        """Fraction passed in [0, 1]."""
        return self.passed / self.total if self.total else 0.0


# --- suites ----------------------------------------------------------------


def _suites_dir() -> Traversable:
    return resources.files(__package__) / "suites"


def available_suites() -> list[str]:
    """Names of the bundled suites (their TOML file stems), sorted."""
    return sorted(p.name.removesuffix(".toml") for p in _suites_dir().iterdir() if p.name.endswith(".toml"))


def load_suite(name: str) -> Suite:
    """Load a suite by bundled name (e.g. ``python``) or path to a ``.toml`` file."""
    path = Path(name)
    if path.suffix == ".toml" and path.is_file():
        return Suite(**tomllib.loads(path.read_text()))
    resource = _suites_dir() / f"{name}.toml"
    if not resource.is_file():
        raise FileNotFoundError(f"unknown suite {name!r}; available: {', '.join(available_suites())}")
    return Suite(**tomllib.loads(resource.read_text()))


# --- code extraction -------------------------------------------------------


_FENCE = re.compile(r"```([\w+-]*)\n(.*?)```", re.DOTALL)


def extract_code(text: str, language: str = "") -> str:
    """Pull the solution out of a model reply.

    The last fenced code block, preferring one tagged with ``language``; falls back to the
    whole text if there's no fence.
    """
    blocks: list[tuple[str, str]] = _FENCE.findall(text)  # (tag, body) per fenced block
    if not blocks:
        return text.strip()
    tagged = [body for tag, body in blocks if tag.lower() == language.lower()]
    return (tagged[-1] if tagged else blocks[-1][1]).strip()


# --- execution -------------------------------------------------------------


def _normalize(text: str) -> str:
    """Trim trailing whitespace per line and surrounding blank lines for output comparison."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def evaluate(problem: Problem, code: str, **limits: str) -> ProblemResult:
    """Run ``code`` against every test case of ``problem`` and score it (all-or-nothing)."""
    cases: list[CaseResult] = []
    start = monotonic()
    for tc in problem.tests:
        res = run_code(problem.language, code, stdin=tc.stdin, args=tc.args, timeout_s=problem.timeout_s, **limits)
        ok = res.exit_code == 0 and not res.timed_out and _normalize(res.stdout) == _normalize(tc.expected_stdout)
        error = "" if ok else ("timed out" if res.timed_out else (res.stderr.strip()[:500] or "wrong output"))
        cases.append(CaseResult(passed=ok, expected=tc.expected_stdout, actual=res.stdout, error=error))
    return ProblemResult(
        problem_id=problem.id,
        difficulty=problem.difficulty,
        type="code",
        passed=bool(cases) and all(c.passed for c in cases),
        cases=cases,
        exec_s=monotonic() - start,  # gen_s is filled in by the driver (it times the model)
    )


# --- tool scoring ----------------------------------------------------------


def _args_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Whether ``actual`` tool args include every expected key with an equal value (superset)."""
    return all(actual.get(key) == value for key, value in expected.items())


def _display_args(args: dict[str, Any]) -> dict[str, Any]:
    """Drop the reserved result key so a trace's display doesn't dump whole tool payloads."""
    return {k: v for k, v in args.items() if k != RESULT_ARG_KEY}


def _answer_contains(answer: str, want: str) -> bool:
    """Case-insensitive substring match, tolerant of thousands separators in numbers.

    Models routinely format counts as ``1,332`` (or ``1 332``); a bare ``1332`` expectation
    should still match. We check the raw text first, then retry with digit-group separators
    (a comma or space *between digits*) stripped from both the answer and the expected text.
    """
    a, w = answer.lower(), want.lower()
    if w in a:
        return True
    strip = lambda s: re.sub(r"(?<=\d)[,\s](?=\d)", "", s)  # noqa: E731 - comma/space between digits
    return strip(w) in strip(a)


def score_tool(problem: Problem, calls: list[tuple[str, dict[str, Any]]], answer: str, gen_s: float) -> ProblemResult:
    """Score a ``tool`` problem: the expected tool called with expected args AND a matching answer.

    ``calls`` is the trace of (tool_name, args) the model made during the agent loop; ``gen_s``
    is that loop's wall-clock. Both the tool check and the answer check must pass (strictest).
    """
    tool_ok = any(name == problem.expect_tool and _args_match(problem.expect_args, args) for name, args in calls)
    want = problem.expect_answer_contains
    answer_ok = _answer_contains(answer, want) if want else True
    made = ", ".join(f"{n}({_display_args(a)})" for n, a in calls) or "(no tool calls)"
    cases = [
        CaseResult(
            passed=tool_ok,
            expected=f"{problem.expect_tool}({problem.expect_args})",
            actual=made,
            error="" if tool_ok else "expected tool/args not called",
        ),
        CaseResult(
            passed=answer_ok,
            expected=want,
            actual=answer[:500],
            error="" if answer_ok else "answer missing expected text",
        ),
    ]
    return ProblemResult(
        problem_id=problem.id,
        difficulty=problem.difficulty,
        type="tool",
        passed=tool_ok and answer_ok,
        cases=cases,
        gen_s=gen_s,  # the agent loop interleaves generation and tool exec; count it all as gen
    )


# --- stateful tool scoring (real end-state) --------------------------------


class StateReader(Protocol):
    """Reads live metadata counts so a write lifecycle can be verified against real state.

    The one method a scorer needs; :class:`heim.benchmark.dhis2_state.Dhis2StateReader` is the
    concrete DHIS2 implementation. Unit tests stub it. ``count`` returns ``None`` when the
    instance is unreachable, which degrades scoring to the trace-only signals rather than crashing.
    """

    def count(self, resource: str, name: str) -> int | None:
        """Number of ``resource`` objects whose name matches ``name`` (a ``like`` prefix); None if unreachable."""
        ...


_CREATE_VERBS = frozenset({"create"})  # d2w write verb: `metadata <type> create ...`
_DELETE_VERBS = frozenset({"delete"})  # d2w write verb: `metadata <type> delete <uid>`
#: A create call whose result text matches this is a *failed* create (non-zero d2w exit code),
#: so it is NOT counted as evidence the object ever existed mid-lifecycle.
_FAILED_RESULT = re.compile(r'"exit_code"\s*:\s*[1-9]')


def _argv(args: dict[str, Any]) -> list[str]:
    """The d2w token list the model passed (the bridge tool's ``args`` parameter)."""
    tokens = args.get("args")
    return [str(t) for t in tokens] if isinstance(tokens, list) else []


def _command_path(argv: list[str]) -> set[str]:
    """The d2w command tokens BEFORE the first ``--flag`` (group / subgroup / verb), lowercased.

    Stopping at the first flag means a flag VALUE (e.g. ``--name create``, ``--code delete``) can
    never be mistaken for a verb — the verb only ever lives in the command path, not in an argument.
    """
    path: set[str] = set()
    for tok in argv:
        if tok.startswith("-"):
            break
        path.add(tok.lower())
    return path


def _has_verb(calls: list[tuple[str, dict[str, Any]]], verbs: frozenset[str]) -> bool:
    """Whether any recorded call's d2w command path (before flags) contains one of ``verbs``."""
    return any(verbs & _command_path(_argv(args)) for _, args in calls)


def _created_ok(calls: list[tuple[str, dict[str, Any]]], name: str) -> bool:
    """Whether the object called ``name`` was really created.

    Requires a create-verb call whose argv carries ``name`` AND whose stashed result
    (:data:`RESULT_ARG_KEY`) is not a d2w error. Tying "a create happened" to THIS object is what
    stops a decoy create+delete of an unrelated object from scoring a pass on a target that is
    trivially absent because it was never touched (a failed create, or no result, also does not
    count, so a rejected create can't sneak past the absent-at-end check).
    """
    for _, args in calls:
        argv = _argv(args)
        if not (_CREATE_VERBS & _command_path(argv)) or name not in argv:
            continue
        result = str(args.get(RESULT_ARG_KEY, ""))
        if result and not _FAILED_RESULT.search(result):
            return True
    return False


def score_tool_stateful(
    problem: Problem,
    calls: list[tuple[str, dict[str, Any]]],
    answer: str,
    gen_s: float,
    reader: StateReader | None,
) -> ProblemResult:
    """Score a write ``tool`` problem against REAL end-state, not a self-reported token.

    Primary signal (when ``reader`` is reachable): every :attr:`Problem.expect_absent` object
    is gone on the live instance at the end AND a create actually succeeded mid-lifecycle — i.e.
    the object was created-then-deleted. Secondary signals (the expected tool was called, the
    ``LIFECYCLE_OK`` token was emitted, both write verbs appear in the trace) are recorded and,
    when the instance is UNREACHABLE, become the fallback verdict so an offline run degrades to a
    trace-only score instead of crashing. This supersedes the old (vacuous) ``expect_args`` check.
    """
    tool_ok = any(name == problem.expect_tool for name, _ in calls)
    created = _has_verb(calls, _CREATE_VERBS)
    deleted = _has_verb(calls, _DELETE_VERBS)
    # Require a successful create of EVERY expected object (not merely "a create happened"), so a
    # create/delete of an unrelated decoy can't pass on a target that is trivially absent. Only used
    # on the stateful path (the degraded path below has no expect_absent to tie names to).
    create_ok = bool(problem.expect_absent) and all(_created_ok(calls, spec.name) for spec in problem.expect_absent)
    want = problem.expect_answer_contains
    token_ok = _answer_contains(answer, want) if want else True

    # Live read-back: count each expected object; a None (unreachable) degrades to trace scoring.
    counts: dict[str, int] = {}
    degraded = reader is None or not problem.expect_absent
    if not degraded and reader is not None:
        for spec in problem.expect_absent:
            found = reader.count(spec.type, spec.name)
            if found is None:
                degraded = True
                break
            counts[f"{spec.type}:{spec.name}"] = found
    absent_ok = not degraded and all(v == 0 for v in counts.values())

    if degraded:
        passed = tool_ok and created and deleted and token_ok
        residue = "live state unavailable — scored on trace verbs + token"
        state_actual = f"created={created} deleted={deleted} token={token_ok}"
        state_passed = created and deleted
    else:
        passed = tool_ok and create_ok and absent_ok
        residue = "" if absent_ok else "residue remains on the live instance"
        state_actual = ", ".join(f"{k}={v}" for k, v in counts.items()) or "(nothing to check)"
        state_passed = absent_ok

    made = ", ".join(f"{n}({_display_args(a)})" for n, a in calls) or "(no tool calls)"
    cases = [
        CaseResult(
            passed=tool_ok,
            expected=problem.expect_tool,
            actual=made,
            error="" if tool_ok else "expected tool not called",
        ),
        CaseResult(
            passed=create_ok if not degraded else created,
            expected="a create succeeded (object existed mid-lifecycle)",
            actual=f"create verb={created}, create succeeded={create_ok}",
            error="" if (create_ok if not degraded else created) else "no successful create in the trace",
        ),
        CaseResult(
            passed=state_passed,
            expected="all HEIM_ objects absent on the live instance at end",
            actual=state_actual,
            error=residue,
        ),
        CaseResult(
            passed=token_ok,
            expected=want,
            actual=answer[:500],
            error="" if token_ok else "answer missing expected token",
        ),
    ]
    return ProblemResult(
        problem_id=problem.id,
        difficulty=problem.difficulty,
        type="tool",
        passed=passed,
        cases=cases,
        gen_s=gen_s,  # the agent loop interleaves generation and tool exec; count it all as gen
    )


# --- results & leaderboard -------------------------------------------------


class ProblemOutcome(BaseModel):
    """One problem's pass/fail and timing in a saved run (kept light for the leaderboard)."""

    model_config = ConfigDict(frozen=True)

    id: str
    difficulty: str
    passed: bool
    gen_s: float = 0.0
    exec_s: float = 0.0


class RunRecord(BaseModel):
    """A saved benchmark run — one ``(suite, model)`` result, persisted as JSON."""

    model_config = ConfigDict(frozen=True)

    suite: str
    language: str
    type: str
    model: str
    timestamp: str = ""
    load_s: float = 0.0  # time to load/serve the model before the first prompt
    outcomes: list[ProblemOutcome]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gen_s(self) -> float:
        """Total model response time across the run's problems."""
        return sum(o.gen_s for o in self.outcomes)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        """Problems attempted."""
        return len(self.outcomes)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> int:
        """Problems passed."""
        return sum(o.passed for o in self.outcomes)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score(self) -> float:
        """Fraction passed in [0, 1]."""
        return self.passed / self.total if self.total else 0.0


def make_record(
    suite: Suite, model: str, results: list[ProblemResult], load_s: float = 0.0, timestamp: str = ""
) -> RunRecord:
    """Build a persistable :class:`RunRecord` from a suite run's results and its load time."""
    return RunRecord(
        suite=suite.name,
        language=suite.language,
        type=suite.type,
        model=model,
        timestamp=timestamp,
        load_s=load_s,
        outcomes=[
            ProblemOutcome(id=r.problem_id, difficulty=r.difficulty, passed=r.passed, gen_s=r.gen_s, exec_s=r.exec_s)
            for r in results
        ],
    )


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def result_path(results_dir: Path | str, suite: str, model: str) -> Path:
    """The JSON path a ``(suite, model)`` run saves to (one file per pair)."""
    return Path(results_dir) / f"{_slug(suite)}__{_slug(model)}.json"


def save_run(record: RunRecord, results_dir: Path | str) -> Path:
    """Write ``record`` to ``<results_dir>/<suite>__<model>.json`` (one file per suite×model)."""
    path = result_path(results_dir, record.suite, record.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2) + "\n")
    return path


def load_results(results_dir: Path | str) -> list[RunRecord]:
    """Load every saved :class:`RunRecord` from a results directory (skipping unreadable ones)."""
    directory = Path(results_dir)
    if not directory.is_dir():
        return []
    records: list[RunRecord] = []
    for path in sorted(directory.glob("*.json")):
        try:
            records.append(RunRecord.model_validate_json(path.read_text()))
        except (OSError, ValueError):
            continue
    return records


def render_leaderboard(records: list[RunRecord]) -> str:
    """Render saved results as a Markdown leaderboard: models × suites, ranked by overall score."""
    if not records:
        return "_No benchmark results yet. Run `heim benchmark run <suite> --model <name> --save`._\n"
    suites = sorted({r.suite for r in records})
    by_model: dict[str, dict[str, RunRecord]] = {}
    for record in records:
        by_model.setdefault(record.model, {})[record.suite] = record

    def totals(model: str) -> tuple[int, int]:
        runs = by_model[model].values()
        return sum(r.passed for r in runs), sum(r.total for r in runs)

    # Rank by absolute problems passed (then rate) so coverage matters: a model that solved
    # 30/31 across all suites outranks one that only ran the easy tool suites and went 9/9.
    def _rank_key(model: str) -> tuple[int, float]:
        passed, total = totals(model)
        return passed, (passed / total if total else 0.0)

    ranked = sorted(by_model, key=_rank_key, reverse=True)
    header = "| Rank | Model | " + " | ".join(suites) + " | Overall |"
    separator = "|" + "---|" * (len(suites) + 3)
    rows = [header, separator]
    for rank, model in enumerate(ranked, 1):
        cells = []
        for suite in suites:
            run = by_model[model].get(suite)
            # Non-breaking space so "100% (11/11)" stays on one line in a narrow doc column.
            cells.append(f"{round(run.score * 100)}% ({run.passed}/{run.total})" if run else "—")
        passed, total = totals(model)
        overall = f"**{round(passed / total * 100) if total else 0}%** ({passed}/{total})"
        rows.append(f"| {rank} | `{model}` | " + " | ".join(cells) + f" | {overall} |")
    return "\n".join(rows) + "\n\n" + _render_performance(ranked, by_model)


def _render_performance(ranked: list[str], by_model: dict[str, dict[str, RunRecord]]) -> str:
    """A second table: average model load time and total response (generation) time."""
    rows = ["## Performance", "", "| Model | Avg load | Avg response / problem |", "|---|---|---|"]
    for model in ranked:
        runs = list(by_model[model].values())
        loads = [r.load_s for r in runs if r.load_s]
        avg_load = f"{sum(loads) / len(loads):.1f}s" if loads else "—"
        problems = sum(r.total for r in runs)
        avg_gen = f"{sum(r.gen_s for r in runs) / problems:.1f}s" if problems else "—"
        rows.append(f"| `{model}` | {avg_load} | {avg_gen} |")
    return "\n".join(rows) + "\n"
