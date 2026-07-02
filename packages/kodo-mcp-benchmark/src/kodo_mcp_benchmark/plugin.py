"""kodo plugin: the ``kodo benchmark`` command group (a pluginkit extension).

Contributes ``kodo benchmark list`` and ``kodo benchmark run <suite>``. The run driver
serves a model once, prompts it per problem, extracts the code from its reply, executes it
in the Docker sandbox (``core``), and prints a scored report. It reaches kodo's runtime
only through the injected :class:`~kodo.plugins.PluginContext`, so this package never
imports kodo at runtime — the plugin is matched to the host purely by pluginkit's project
name (``kodo``) and the ``commands`` method name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer
from pluginkit import Extension

from kodo_mcp_benchmark import core

if TYPE_CHECKING:
    from kodo.plugins import PluginContext

extension = Extension("kodo")

_SYSTEM = (
    "You are a coding benchmark candidate. Given a task, respond with a single complete "
    "{language} program that reads input from standard input and writes the answer to "
    "standard output. Put the program in one fenced code block and include nothing else."
)


def _build_app(context: PluginContext) -> typer.Typer:
    """Build the ``benchmark`` Typer group, closing over the host ``context``."""
    bench = typer.Typer(help="Run language coding benchmarks against a model.", no_args_is_help=True)

    @bench.command("list")
    def list_suites() -> None:
        """List the available benchmark suites."""
        for name in core.available_suites():
            suite = core.load_suite(name)
            cases = sum(len(p.tests) for p in suite.problems)
            summary = f"{suite.language} · {len(suite.problems)} problems · {cases} test cases"
            context.console.print(f"[bold]{name}[/]  [dim]{summary}[/]")

    @bench.command("run")
    def run(
        suite: Annotated[str, typer.Argument(help="Suite name (see `kodo benchmark list`).")],
        model: Annotated[
            str | None, typer.Option("--model", "-m", help="Library model; defaults to the project's.")
        ] = None,
        limit: Annotated[int | None, typer.Option("--limit", "-l", help="Only the first N problems.")] = None,
        max_tokens: Annotated[
            int | None, typer.Option("--max-tokens", "-n", help="Cap generated tokens per answer.")
        ] = None,
    ) -> None:
        """Prompt the model on each problem, run its code in the sandbox, and score it."""
        console = context.console
        if not core.docker_available():
            console.print("[red]Docker is required[/] to run benchmarks (it sandboxes model code). Start it and retry.")
            raise typer.Exit(1)
        try:
            loaded = core.load_suite(suite)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        mdl = context.resolve_model(model)
        problems = loaded.problems[:limit] if limit else loaded.problems
        console.print(f"[bold]{loaded.name}[/] · {mdl.name} · {len(problems)} problems\n")
        results = []
        with context.serve(mdl) as base:
            for problem in problems:
                system = _SYSTEM.format(language=problem.language)
                answer = context.complete(base, mdl, problem.prompt, system=system, max_tokens=max_tokens)
                result = core.evaluate(problem, core.extract_code(answer, problem.language))
                mark = "[green]PASS[/]" if result.passed else "[red]FAIL[/]"
                cases = f"{sum(c.passed for c in result.cases)}/{len(result.cases)} cases"
                console.print(f"  {mark}  {problem.id}  [dim]{cases} · {result.duration_s:.1f}s[/]")
                results.append(result)
        report = core.SuiteReport(suite=loaded.name, model=mdl.name, results=results)
        console.print(f"\n[bold]Score: {report.passed}/{report.total}[/] ([bold]{round(report.score * 100)}%[/])")

    return bench


class BenchmarkPlugin:
    """kodo plugin object: contributes the ``benchmark`` command group."""

    @extension
    def commands(self, context: PluginContext) -> tuple[str, typer.Typer]:
        """Mount ``kodo benchmark`` (``list`` / ``run``)."""
        return "benchmark", _build_app(context)


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = BenchmarkPlugin()
