"""System health checks for ``kodo doctor``.

Each check is a pure function returning a :class:`Check`, so the whole report is
testable without a terminal. The CLI (:func:`kodo.cli.doctor`) renders them.

Checks cover the things that make ``kodo run/serve`` actually work: the runtime
binaries kodo spawns (llama.cpp / MLX), the library location, what's in it, and
the current project manifest.
"""

import platform
import shutil
import sys
from enum import StrEnum

from pydantic import BaseModel

from kodo import library as library_ops
from kodo import project as project_ops
from kodo import runtime
from kodo.config import Settings, get_settings


class CheckStatus(StrEnum):
    """Outcome of a single health check."""

    ok = "ok"
    warn = "warn"
    fail = "fail"


class Check(BaseModel):
    """One health-check result."""

    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None


class DoctorReport(BaseModel):
    """The full set of checks plus a rolled-up worst status."""

    checks: list[Check]

    @property
    def status(self) -> CheckStatus:
        """The worst status across all checks (fail > warn > ok)."""
        if any(c.status is CheckStatus.fail for c in self.checks):
            return CheckStatus.fail
        if any(c.status is CheckStatus.warn for c in self.checks):
            return CheckStatus.warn
        return CheckStatus.ok


def _is_apple_silicon() -> bool:
    """Whether this is an Apple Silicon Mac (where MLX runtimes are relevant)."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _runtime_check(name: str, binary: str, *, required: bool, relevant: bool = True) -> Check:
    """Check that a runtime binary is on PATH.

    Args:
        name: Human label for the check.
        binary: Executable to look for.
        required: If missing, ``fail`` (a needed runtime) vs ``warn`` (optional).
        relevant: If false (e.g. MLX off Apple Silicon), report ``ok`` as N/A.
    """
    path = shutil.which(binary)
    if path is not None:
        return Check(name=name, status=CheckStatus.ok, detail=path)
    if not relevant:
        return Check(name=name, status=CheckStatus.ok, detail="not applicable on this platform")
    return Check(
        name=name,
        status=CheckStatus.fail if required else CheckStatus.warn,
        detail=f"{binary!r} not found on PATH",
        hint=runtime._INSTALL_HINTS.get(binary),
    )


def check_runtimes() -> list[Check]:
    """Check the model-runtime binaries kodo spawns."""
    mlx_relevant = _is_apple_silicon()
    return [
        # GGUF is the cross-platform backbone; without llama-server nothing GGUF runs.
        _runtime_check("llama.cpp (GGUF)", "llama-server", required=True),
        # MLX runtimes are optional and Apple-Silicon-only.
        _runtime_check("MLX text (mlx-lm)", "mlx_lm.server", required=False, relevant=mlx_relevant),
        _runtime_check("MLX vision (mlx-vlm)", "mlx_vlm.server", required=False, relevant=mlx_relevant),
    ]


def check_library(settings: Settings) -> list[Check]:
    """Check the library roots and what's in them."""
    if not library_ops.configured(settings):
        return [
            Check(
                name="Libraries",
                status=CheckStatus.fail,
                detail="not configured",
                hint="Set KODO_LIBRARY_ROOT (e.g. /Volumes/LLM/Library), or run `kodo project init`.",
            )
        ]
    checks: list[Check] = []
    lib_roots = library_ops.roots(settings)
    missing = [r for r in lib_roots if not r.is_dir()]
    detail = "\n".join(f"{r}" + ("" if r.is_dir() else " (not mounted)") for r in lib_roots)
    checks.append(
        Check(
            name="Libraries",
            status=CheckStatus.ok if not missing else CheckStatus.warn,
            detail=detail,
            hint=None if not missing else "A library isn't mounted; models in the others still work.",
        )
    )

    models = [m for m in library_ops.scan() if m.generative and not m.is_ollama]
    by_format: dict[str, int] = {}
    for m in models:
        by_format[m.model_format.value] = by_format.get(m.model_format.value, 0) + 1
    summary = ", ".join(f"{n} {fmt}" for fmt, n in sorted(by_format.items())) if models else "none"
    checks.append(
        Check(
            name="Runnable models",
            status=CheckStatus.ok if models else CheckStatus.warn,
            detail=f"{len(models)} ({summary})",
            hint=None if models else "Pull one with `kodo pull` (or `kodo sources` to see local caches).",
        )
    )
    return checks


def check_project(settings: Settings) -> list[Check]:
    """Check the current project manifest (kodo.toml), if any."""
    proj = project_ops.load()
    if proj is None:
        # No project is a valid mode (free-play: all models, no auto-load), not something to
        # report — so emit no project checks at all rather than a "none in cwd" row.
        return []
    checks = [Check(name="Project (kodo.toml)", status=CheckStatus.ok, detail="found")]
    if proj.model:
        resolved = library_ops.find(proj.model)
        checks.append(
            Check(
                name="Default model",
                status=CheckStatus.ok if resolved else CheckStatus.warn,
                detail=proj.model + ("" if resolved else " (not in library)"),
                hint=None if resolved else f"Pull it: `kodo pull huggingface {proj.model}`.",
            )
        )
    else:
        checks.append(Check(name="Default model", status=CheckStatus.warn, detail="not set"))
    if proj.mcp:
        names = ", ".join(m.name or m.command.split()[0] for m in proj.mcp)
        checks.append(Check(name="Project tools (MCP)", status=CheckStatus.ok, detail=f"{len(proj.mcp)} ({names})"))
    return checks


def run_checks(settings: Settings | None = None) -> DoctorReport:
    """Run every health check and return the aggregated report."""
    conf = settings or get_settings()
    checks = [*check_runtimes(), *check_library(conf), *check_project(conf)]
    return DoctorReport(checks=checks)
