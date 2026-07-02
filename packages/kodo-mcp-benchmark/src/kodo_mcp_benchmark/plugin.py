"""kodo plugin: the ``kodo benchmark`` command group (a pluginkit extension).

Contributes ``kodo benchmark list | run | leaderboard``. The run driver serves a model
once and, per problem, either prompts it for a program and runs that in the Docker sandbox
(``code`` problems) or runs the agent loop with MCP servers attached and scores the tool
trace + answer (``tool`` problems). Results can be saved as JSON and rendered into a docs
leaderboard. It reaches kodo's runtime only through the injected
:class:`~kodo.plugins.PluginContext`, so this package never imports kodo at runtime.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Annotated

import typer
from pluginkit import Extension

from kodo_mcp_benchmark import core

if TYPE_CHECKING:
    from kodo.library import LibraryModel
    from kodo.plugins import PluginContext

extension = Extension("kodo")

_DEFAULT_RESULTS = "benchmarks/results"
_DEFAULT_PAGE = Path("docs/benchmarks.md")

_SYSTEM = (
    "You are a coding benchmark candidate. Given a task, respond with a single complete "
    "{language} program that reads input from standard input and writes the answer to "
    "standard output. Put the program in one fenced code block and include nothing else."
)

_PAGE_INTRO = """# Benchmark leaderboard

How local models score on kodo's coding and tool-use benchmarks. Each cell is the
percentage of problems fully passed (a problem passes only if every check passes).

Regenerate with `kodo benchmark leaderboard` after `kodo benchmark run ... --save`.

"""


def _run_problem(
    context: PluginContext, model: LibraryModel, base: str, problem: core.Problem, max_tokens: int | None
) -> core.ProblemResult:
    """Score one problem against a served model: code via the sandbox, tool via the agent loop."""
    if problem.type == "tool":
        start = monotonic()
        calls, answer = context.run_agent(base, model, problem.prompt, problem.servers)
        return core.score_tool(problem, calls, answer, monotonic() - start)
    system = _SYSTEM.format(language=problem.language)
    answer = context.complete(base, model, problem.prompt, system=system, max_tokens=max_tokens)
    return core.evaluate(problem, core.extract_code(answer, problem.language))


def _run_suite_for_model(
    context: PluginContext, model: LibraryModel, suite: core.Suite, problems: list[core.Problem], max_tokens: int | None
) -> list[core.ProblemResult]:
    """Serve ``model`` once and score every problem, printing per-problem progress."""
    console = context.console
    results: list[core.ProblemResult] = []
    with context.serve(model) as base:
        for problem in problems:
            result = _run_problem(context, model, base, problem, max_tokens)
            mark = "[green]PASS[/]" if result.passed else "[red]FAIL[/]"
            cases = f"{sum(c.passed for c in result.cases)}/{len(result.cases)}"
            console.print(
                f"  {mark}  [dim]{problem.difficulty:12}[/] {problem.id}  [dim]{cases} · {result.duration_s:.1f}s[/]"
            )
            results.append(result)
    return results


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
        suite: Annotated[str, typer.Argument(help="Suite name (see `kodo benchmark list`).")],
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
        models = context.list_models() if all_models else [context.resolve_model(model)]
        problems = loaded.problems[:limit] if limit else loaded.problems
        for mdl in models:
            if skip_done and core.result_path(results_dir, loaded.name, mdl.name).exists():
                console.print(f"[dim]skip {mdl.name} (already saved)[/]")
                continue
            console.print(f"\n[bold]{loaded.name}[/] · {mdl.name} · {len(problems)} problems")
            try:
                results = _run_suite_for_model(context, mdl, loaded, problems, max_tokens)
            except Exception as exc:  # noqa: BLE001 - in a sweep one bad model must not stop the rest
                console.print(f"  [red]skipped {mdl.name}[/]: {exc}")
                if not all_models:
                    raise
                continue
            report = core.SuiteReport(suite=loaded.name, model=mdl.name, results=results)
            console.print(f"[bold]Score: {report.passed}/{report.total}[/] ([bold]{round(report.score * 100)}%[/])")
            if save:
                stamp = datetime.now().isoformat(timespec="seconds")  # noqa: DTZ005 - local wall clock is fine here
                path = core.save_run(core.make_record(loaded, mdl.name, results, stamp), results_dir)
                console.print(f"[dim]saved {path}[/]")

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
    """kodo plugin object: contributes the ``benchmark`` command group."""

    @extension
    def commands(self, context: PluginContext) -> tuple[str, typer.Typer]:
        """Mount ``kodo benchmark`` (``list`` / ``run`` / ``leaderboard``)."""
        return "benchmark", _build_app(context)


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = BenchmarkPlugin()
