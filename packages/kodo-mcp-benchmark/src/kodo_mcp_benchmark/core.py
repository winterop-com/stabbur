"""Benchmark core: suites, a Docker code executor, and scoring — no MCP, no kodo deps.

A *suite* is a set of coding *problems*. Each problem gives a prompt plus hidden test
cases; a candidate solution is a full program that reads stdin / argv and writes stdout.
We run each candidate in a throwaway Docker container (no network; capped memory, CPU,
and pids; a wall-clock timeout) and compare its trimmed stdout to the expected output.

Language-agnostic on purpose: Python and Rust score through the same stdin/stdout path,
so adding a language is one entry in ``_RUNTIMES`` (image + how to build/run the source).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import tomllib
import uuid
from importlib import resources
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class _Runtime(BaseModel):
    """How to execute a language: its image and the container argv that runs the source."""

    model_config = ConfigDict(frozen=True)

    image: str
    filename: str  # where the candidate source is written inside /work
    command: list[str]  # container argv; trailing args (test argv) are appended


# Rust compiles to a tmpfs (rootfs is read-only) then execs, forwarding any test argv via
# "$@". Python runs the source directly. Both read the source from a read-only /work mount.
_RUNTIMES: dict[str, _Runtime] = {
    "python": _Runtime(image="python:3.13-slim", filename="main.py", command=["python", "/work/main.py"]),
    "rust": _Runtime(
        image="rust:1-slim",
        filename="main.rs",
        command=["sh", "-c", 'rustc -O /work/main.rs -o /tmp/prog && exec /tmp/prog "$@"', "_"],
    ),
}

SUPPORTED_LANGUAGES = tuple(_RUNTIMES)


# --- suite / result models -------------------------------------------------


class TestCase(BaseModel):
    """One hidden check: feed ``stdin`` (and ``args``), expect ``expected_stdout``."""

    model_config = ConfigDict(frozen=True)

    stdin: str = ""
    args: list[str] = Field(default_factory=list)
    expected_stdout: str


class Problem(BaseModel):
    """A single coding task: a prompt, its language, and the hidden test cases."""

    model_config = ConfigDict(frozen=True)

    id: str
    prompt: str
    language: str
    timeout_s: float = 10.0
    tests: list[TestCase]


class Suite(BaseModel):
    """A named set of problems (all one language)."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    language: str
    problems: list[Problem]

    @model_validator(mode="before")
    @classmethod
    def _propagate_language(cls, data: object) -> object:
        """Fill each problem's language from the suite's, so the TOML sets it once."""
        if isinstance(data, dict):
            language = data.get("language")
            for problem in data.get("problems", []):
                if isinstance(problem, dict):
                    problem.setdefault("language", language)
        return data


class RunResult(BaseModel):
    """The raw outcome of executing a program once."""

    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_s: float


class CaseResult(BaseModel):
    """Whether one test case passed, with the expected/actual output for diffing."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    expected: str
    actual: str
    error: str = ""  # stderr excerpt / "timed out" when it failed to run cleanly


class ProblemResult(BaseModel):
    """A problem's verdict: passed only if every case passed."""

    model_config = ConfigDict(frozen=True)

    problem_id: str
    passed: bool
    cases: list[CaseResult]
    duration_s: float


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


def _suites_dir() -> resources.abc.Traversable:
    return resources.files(__package__) / "suites"


def available_suites() -> list[str]:
    """Names of the bundled suites (their TOML file stems), sorted."""
    return sorted(p.name.removesuffix(".toml") for p in _suites_dir().iterdir() if p.name.endswith(".toml"))


def load_suite(name: str) -> Suite:
    """Load a suite by bundled name (e.g. ``python-basics``) or path to a ``.toml`` file."""
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


class DockerError(RuntimeError):
    """Docker is unavailable or failed to start a container."""


def docker_available() -> bool:
    """Whether a working Docker daemon is reachable."""
    try:
        subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def run_code(
    language: str,
    code: str,
    stdin: str = "",
    args: list[str] | None = None,
    timeout_s: float = 10.0,
    memory: str = "512m",
    cpus: str = "1.0",
) -> RunResult:
    """Run ``code`` in a locked-down throwaway container and capture its output.

    The container has no network, a read-only rootfs (source mounted read-only at
    ``/work``, an exec-able tmpfs at ``/tmp``), capped memory/CPU/pids, and is killed at
    ``timeout_s``. Returns the program's stdout/stderr, exit code, and whether it timed out.
    """
    runtime = _RUNTIMES.get(language)
    if runtime is None:
        raise ValueError(f"unsupported language {language!r}; use one of {', '.join(SUPPORTED_LANGUAGES)}")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / runtime.filename).write_text(code)
        name = f"kodo-bench-{uuid.uuid4().hex[:12]}"
        cmd = [
            "docker", "run", "--rm", "-i", "--name", name,
            "--network=none", f"--memory={memory}", f"--cpus={cpus}", "--pids-limit=256",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=256m",
            "-v", f"{tmp}:/work:ro", "-w", "/work",
            runtime.image, *runtime.command, *(args or []),
        ]  # fmt: skip
        start = monotonic()
        try:
            proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # best-effort kill
            return RunResult(
                stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr="timed out",
                exit_code=124,
                timed_out=True,
                duration_s=monotonic() - start,
            )
        except FileNotFoundError as exc:  # docker binary missing
            raise DockerError("docker not found on PATH") from exc
        return RunResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            timed_out=False,
            duration_s=monotonic() - start,
        )


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
        passed=bool(cases) and all(c.passed for c in cases),
        cases=cases,
        duration_s=monotonic() - start,
    )
