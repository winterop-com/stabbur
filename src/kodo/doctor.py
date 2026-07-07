"""System health checks for ``kodo doctor``.

Each check is a pure function returning a :class:`Check`, so the whole report is
testable without a terminal. The CLI (:func:`kodo.cli.doctor`) renders them.

Checks cover the things that make ``kodo run/serve`` actually work: the runtime
binaries kodo spawns (llama.cpp / MLX), the library location, what's in it, and
the current project manifest.
"""

import shutil
from enum import StrEnum

from pydantic import BaseModel

from kodo import host, mcpservers, runtime
from kodo import library as library_ops
from kodo import project as project_ops
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


def check_platform() -> list[Check]:
    """Report the OS/arch so the rest of the report (esp. N/A rows) reads in context."""
    return [Check(name="Platform", status=CheckStatus.ok, detail=host.os_label())]


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
    mlx_relevant = host.is_apple_silicon()
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
                hint="Set KODO_LIBRARY_ROOT to your library path, or run `kodo project init`.",
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
            hint=None
            if models
            else "Pull one with `kodo library pull` (or `kodo library sources` to see local caches).",
        )
    )
    return checks


def check_project(settings: Settings) -> list[Check]:
    """Check the current project (if any) and the effective default model.

    A project is optional (free-play is valid), so its rows only appear when a ``kodo.toml``
    is present. The **default model** row is always shown when one is resolvable — the project's
    model, else the machine default (``kodo config set model``) — since that's what ``kodo chat``
    and ``serve --ui`` load without an explicit name.
    """
    proj = project_ops.load()
    checks: list[Check] = []
    if proj is not None:
        checks.append(Check(name="Project (kodo.toml)", status=CheckStatus.ok, detail="found"))
        # A project that lists @shared but whose library_root is unset silently drops the shared
        # archive (see library.roots): it runs from its own libraries, but the drive's models are
        # invisible with no error. Warn so it's not a mystery.
        if library_ops.SHARED_TOKEN in proj.libraries and "library_root" not in settings.model_fields_set:
            checks.append(
                Check(
                    name="Shared library (@shared)",
                    status=CheckStatus.warn,
                    detail="listed in this project but unreachable — KODO_LIBRARY_ROOT is not set",
                    hint="Set KODO_LIBRARY_ROOT (e.g. export it in your shell profile) so @shared resolves; "
                    "until then this project runs only from its own libraries.",
                )
            )

    # The effective default model: project model > machine default (settings.default_model).
    default_model = project_ops.resolve_model(None, proj)
    if default_model is not None:
        resolved = library_ops.find(default_model)
        from_project = bool(proj and proj.model)
        detail = (
            default_model + ("" if from_project else " (machine default)") + ("" if resolved else " — not in library")
        )
        checks.append(
            Check(
                name="Default model",
                status=CheckStatus.ok if resolved else CheckStatus.warn,
                detail=detail,
                hint=None if resolved else f"Pull it: `kodo library pull huggingface {default_model}`.",
            )
        )
    elif proj is not None:
        # A project usually pins a model; flag when it doesn't. (No project + no machine default
        # is plain free-play — nothing to report.)
        checks.append(
            Check(
                name="Default model",
                status=CheckStatus.warn,
                detail="not set",
                hint="Set one in kodo.toml, or a machine default: `kodo config set model <name>`.",
            )
        )

    # Effective tools: the resolved mcp.json servers (global + project). Shown whenever any exist.
    servers = mcpservers.resolve()
    if servers:
        names = ", ".join(s.name for s in servers)
        checks.append(Check(name="Tools (MCP)", status=CheckStatus.ok, detail=f"{len(servers)} ({names})"))
    return checks


def run_checks(settings: Settings | None = None) -> DoctorReport:
    """Run every health check and return the aggregated report."""
    conf = settings or get_settings()
    checks = [*check_platform(), *check_runtimes(), *check_library(conf), *check_project(conf)]
    return DoctorReport(checks=checks)
