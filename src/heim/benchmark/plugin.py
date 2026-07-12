"""heim plugin: the ``heim benchmark`` command group (a pluginkit extension).

Contributes ``heim benchmark list | run | leaderboard``. The run driver serves a model
once and, per problem, either prompts it for a program and runs that in the Docker sandbox
(``code`` problems) or runs the agent loop with MCP servers attached and scores the tool
trace + answer (``tool`` problems). Results can be saved as JSON and rendered into a docs
leaderboard. It reaches heim's runtime only through the injected
:class:`~heim.plugins.PluginContext`, so this package never imports heim at runtime.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Annotated

import typer
from pluginkit import Extension

from heim.benchmark import core

if TYPE_CHECKING:
    from rich.console import Console

    from heim.benchmark.dhis2_state import Dhis2StateReader
    from heim.library import LibraryModel
    from heim.plugins import PluginContext

extension = Extension("heim")

_DEFAULT_RESULTS = "benchmarks/results"
_DEFAULT_PAGE = Path("docs/benchmarks.md")

_SYSTEM = (
    "You are a coding benchmark candidate. Given a task, respond with a single complete "
    "{language} program that reads input from standard input and writes the answer to "
    "standard output. Put the program in one fenced code block and include nothing else."
)

_PAGE_INTRO = """# Benchmark leaderboard

How local models score on heim's coding and tool-use benchmarks. Each cell is the
percentage of problems fully passed (a problem passes only if every check passes).

Regenerate with `heim benchmark leaderboard` after `heim benchmark run ... --save`.

"""


def _qualifies(context: PluginContext, suite: core.Suite, model: LibraryModel) -> tuple[bool, str]:
    """Whether ``model`` should run against ``suite`` (the ``--all`` gate).

    Tool suites need tool-calling — the detected capability, or a manual ``tools`` tag to
    override a model that fakes support. Code (or any tagged) suites need the suite's
    ``requires_tag`` (e.g. ``coding``). Returns ``(ok, reason)``; reason is set when skipped.
    """
    if suite.type == "tool" and not context.supports_tools(model) and "tools" not in context.model_tags(model):
        return False, "no tool capability (tag it 'tools' to force)"
    if suite.requires_tag and suite.requires_tag not in context.model_tags(model):
        return False, f"not tagged '{suite.requires_tag}'"
    return True, ""


def _make_reader(suite: core.Suite) -> tuple[Dhis2StateReader | None, list[str]]:
    """Build a live-state reader + the resource types to sweep for a stateful (write) suite.

    Only suites whose problems carry ``expect_absent`` are stateful; anything else (the read
    suite) gets ``(None, [])`` and is scored unchanged. Best-effort: a missing profile, missing
    httpx, or any construction error yields ``(None, [])`` so the run degrades to trace scoring.
    """
    if not any(p.expect_absent for p in suite.problems):
        return None, []
    try:
        from heim.benchmark import dhis2_state  # noqa: PLC0415 - optional; only for write suites

        servers = next((p.servers for p in suite.problems if p.servers), [])
        profile = dhis2_state.load_profile(dhis2_state.profile_from_servers(servers))
        if profile is None:
            return None, []
        return dhis2_state.Dhis2StateReader(profile), dhis2_state.sweep_types(list(suite.problems))
    except Exception:  # noqa: BLE001 - state scoring is best-effort; never block a run
        return None, []


def _sweep(console: Console, reader: Dhis2StateReader | None, resources: list[str], when: str, model: str) -> None:
    """Best-effort HEIM_ residue sweep around a model's write suite (``when`` = before/after)."""
    if reader is None or not resources:
        return
    try:
        from heim.benchmark import dhis2_state  # noqa: PLC0415

        removed = dhis2_state.sweep_residue(reader, resources)
        total = sum(len(uids) for uids in removed.values())
        if total:
            detail = ", ".join(f"{res} {len(uids)}" for res, uids in removed.items() if uids)
            console.print(f"  [dim]swept {total} HEIM_ residue {when} {model} ({detail})[/]")
    except Exception as exc:  # noqa: BLE001 - a sweep failure must never stop the benchmark
        console.print(f"  [dim]residue sweep {when} {model} skipped: {exc}[/]")


def _run_problem(
    context: PluginContext,
    model: LibraryModel,
    base: str,
    problem: core.Problem,
    max_tokens: int | None,
    reader: Dhis2StateReader | None,
) -> core.ProblemResult:
    """Score one problem against a served model: code via the sandbox, tool via the agent loop.

    A ``tool`` problem carrying ``expect_absent`` is scored *statefully* — verified against real
    end-state on the live instance (``reader``); a plain read ``tool`` problem keeps the trace +
    answer scoring.
    """
    if problem.type == "tool":
        start = monotonic()
        calls, answer = context.run_agent(base, model, problem.prompt, problem.servers)
        gen_s = monotonic() - start
        if problem.expect_absent:
            return core.score_tool_stateful(problem, calls, answer, gen_s, reader)
        return core.score_tool(problem, calls, answer, gen_s)
    system = _SYSTEM.format(language=problem.language)
    start = monotonic()
    answer = context.complete(base, model, problem.prompt, system=system, max_tokens=max_tokens)
    gen_s = monotonic() - start  # time waiting on the model; exec time is set by evaluate()
    return core.evaluate(problem, core.extract_code(answer, problem.language)).model_copy(update={"gen_s": gen_s})


def _run_suite_for_model(
    context: PluginContext,
    model: LibraryModel,
    suite: core.Suite,
    problems: list[core.Problem],
    max_tokens: int | None,
    reader: Dhis2StateReader | None,
) -> tuple[list[core.ProblemResult], float]:
    """Serve ``model`` once and score every problem; return the results and the load time."""
    console = context.console
    results: list[core.ProblemResult] = []
    start = monotonic()
    with context.serve(model) as base:  # __enter__ blocks until the runtime is ready
        load_s = monotonic() - start
        console.print(f"  [dim]loaded in {load_s:.1f}s[/]")
        for problem in problems:
            result = _run_problem(context, model, base, problem, max_tokens, reader)
            mark = "[green]PASS[/]" if result.passed else "[red]FAIL[/]"
            cases = f"{sum(c.passed for c in result.cases)}/{len(result.cases)}"
            timing = f"gen {result.gen_s:.1f}s" + (f" · exec {result.exec_s:.1f}s" if result.exec_s else "")
            console.print(f"  {mark}  [dim]{problem.difficulty:12}[/] {problem.id}  [dim]{cases} · {timing}[/]")
            results.append(result)
    return results, load_s


def _build_app(context: PluginContext) -> typer.Typer:
    """Build the ``benchmark`` Typer group, closing over the host ``context``."""
    bench = typer.Typer(help="Run language + tool-use benchmarks against your models.", no_args_is_help=True)

    @bench.command("list")
    def list_suites() -> None:
        """List the available benchmark suites."""
        for name in core.available_suites():
            suite = core.load_suite(name)
            cases = sum(len(p.tests) for p in suite.problems)
            kind = suite.type if suite.type == "tool" else suite.language
            summary = f"{kind} · {len(suite.problems)} problems"
            summary += f" · {cases} test cases" if suite.type == "code" else ""
            context.console.print(f"[bold]{name}[/]  [dim]{summary}[/]")

    @bench.command("run")
    def run(
        suite: Annotated[str, typer.Argument(help="Suite name (see `heim benchmark list`).")],
        model: Annotated[
            str | None, typer.Option("--model", "-m", help="Library model; defaults to the project's.")
        ] = None,
        all_models: Annotated[bool, typer.Option("--all", help="Run every generative library model.")] = False,
        limit: Annotated[int | None, typer.Option("--limit", "-l", help="Only the first N problems.")] = None,
        max_tokens: Annotated[
            int | None, typer.Option("--max-tokens", "-n", help="Cap generated tokens per answer.")
        ] = None,
        save: Annotated[bool, typer.Option("--save", help="Save results as JSON for the leaderboard.")] = False,
        results_dir: Annotated[str, typer.Option("--results-dir", help="Where to save results.")] = _DEFAULT_RESULTS,
        skip_done: Annotated[
            bool, typer.Option("--skip-done", help="Skip (suite, model) pairs already saved.")
        ] = False,
    ) -> None:
        """Prompt each model on every problem, score it (sandbox or tool trace), and report."""
        console = context.console
        try:
            loaded = core.load_suite(suite)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        if any(p.type == "code" for p in loaded.problems) and not core.docker_available():
            console.print("[red]Docker is required[/] for code problems (it sandboxes model code). Start it and retry.")
            raise typer.Exit(1)
        if all_models:
            models = []
            for candidate in context.list_models():
                ok, reason = _qualifies(context, loaded, candidate)
                if ok:
                    models.append(candidate)
                else:
                    console.print(f"[dim]skip {candidate.name}: {reason}[/]")
        else:
            chosen = context.resolve_model(model)
            ok, reason = _qualifies(context, loaded, chosen)
            if not ok:  # explicit --model runs regardless, but flag the mismatch
                console.print(f"[yellow]note[/]: {chosen.name} {reason} — running anyway (explicit --model)")
            models = [chosen]
        problems = loaded.problems[:limit] if limit else loaded.problems
        # For a stateful (write) suite, build a live-state reader once and the resource types to
        # sweep; None/[] for read or offline suites (scoring then degrades to the trace signals).
        reader, sweep_resources = _make_reader(loaded)
        for mdl in models:
            if skip_done and core.result_path(results_dir, loaded.name, mdl.name).exists():
                console.print(f"[dim]skip {mdl.name} (already saved)[/]")
                continue
            console.print(f"\n[bold]{loaded.name}[/] · {mdl.name} · {len(problems)} problems")
            # Clear any HEIM_ residue a prior model abandoned so it can't compound into this run.
            _sweep(console, reader, sweep_resources, "before", mdl.name)
            try:
                results, load_s = _run_suite_for_model(context, mdl, loaded, problems, max_tokens, reader)
            except Exception as exc:  # noqa: BLE001 - in a sweep one bad model must not stop the rest
                console.print(f"  [red]skipped {mdl.name}[/]: {exc}")
                _sweep(console, reader, sweep_resources, "after", mdl.name)
                if not all_models:
                    raise
                continue
            # Delete this model's own leftovers so the instance is clean for the next model.
            _sweep(console, reader, sweep_resources, "after", mdl.name)
            report = core.SuiteReport(suite=loaded.name, model=mdl.name, results=results)
            console.print(f"[bold]Score: {report.passed}/{report.total}[/] ([bold]{round(report.score * 100)}%[/])")
            if save:
                stamp = datetime.now().isoformat(timespec="seconds")  # noqa: DTZ005 - local wall clock is fine here
                record = core.make_record(loaded, mdl.name, results, load_s, stamp)
                console.print(f"[dim]saved {core.save_run(record, results_dir)}[/]")

    @bench.command("leaderboard")
    def leaderboard(
        results_dir: Annotated[str, typer.Option("--results-dir", help="Where results are saved.")] = _DEFAULT_RESULTS,
        out: Annotated[Path, typer.Option("--out", help="Markdown page to write.")] = _DEFAULT_PAGE,
    ) -> None:
        """Render saved results into a Markdown leaderboard page for the docs."""
        records = core.load_results(results_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_PAGE_INTRO + core.render_leaderboard(records))
        context.console.print(f"[green]wrote[/] {out} [dim]({len(records)} results)[/]")

    return bench


class BenchmarkPlugin:
    """heim plugin object: contributes the ``benchmark`` command group."""

    @extension
    def commands(self, context: PluginContext) -> tuple[str, typer.Typer]:
        """Mount ``heim benchmark`` (``list`` / ``run`` / ``leaderboard``)."""
        return "benchmark", _build_app(context)

    # Note: benchmark deliberately does NOT advertise itself via `mcp_servers`. It's a
    # dev/benchmarking tool (driven by `heim benchmark` + its own MCP app), not an
    # everyday assistant tool — so it stays out of the assistant tool picker, which lists
    # only genuine assistant tools (datetime, utils, …). Re-add an `mcp_servers` hook if a
    # narrow subset (e.g. the sandboxed code executor) ever becomes a real assistant tool.


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = BenchmarkPlugin()
