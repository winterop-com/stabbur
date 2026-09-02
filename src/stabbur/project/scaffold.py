"""Project scaffolding — the pure logic behind ``stabbur project new`` / ``init`` (A7).

The interactive wizard (prompts + echoing) stays in the CLI; this module owns the file-writing
and setup operations it drives — mapping MCP commands to pip deps, rendering / editing a project
``pyproject.toml``, copying a model into a project-local library, and git init. Extracted here so
that logic is unit-testable rather than reachable only through Typer. Nothing in this module prints
or prompts: ``git_init`` returns a status for the caller to render.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

#: The project-local library directory ``stabbur project init`` scaffolds (holds this project's models).
LOCAL_LIBRARY = "library"


def stabbur_repo_root() -> Path | None:
    """The stabbur source checkout this code is running from, or ``None`` for an installed copy.

    Found by walking up for stabbur's own ``pyproject.toml`` rather than counting parents: the
    old fixed ``parents[2]`` named ``site-packages`` for an installed stabbur and ``src/`` for a
    checkout — neither is a project, so every scaffolded project failed on ``uv sync`` with "does
    not appear to be a Python project". A published stabbur has no checkout and returns ``None``.
    """
    for root in Path(__file__).resolve().parents[:4]:
        manifest = root / "pyproject.toml"
        if manifest.is_file() and 'name = "stabbur"' in manifest.read_text(encoding="utf-8", errors="ignore"):
            return root
    return None


def strip_uvx(command: str) -> str:
    """Drop a ``uvx `` runner from an MCP command — in a uv project the server is an installed dep."""
    return command.replace("uvx ", "", 1) if "uvx " in command else command


def split_env_prefix(command: str) -> tuple[str, dict[str, str]]:
    """Lift a leading ``env VAR=val …`` prefix out of a command into structured env.

    ``env DHIS2_PROFILE=play42 dhis2w-mcp-bridge`` -> ``("dhis2w-mcp-bridge", {"DHIS2_PROFILE": "play42"})``.
    Lets a curated single-string command lift into an ``.mcp.json`` entry's ``command`` + ``env``.
    """
    toks = shlex.split(command)
    env: dict[str, str] = {}
    i = 0
    if toks and toks[0] == "env":
        i = 1
        while i < len(toks) and "=" in toks[i] and not toks[i].startswith("-"):
            key, val = toks[i].split("=", 1)
            env[key] = val
            i += 1
    return shlex.join(toks[i:]), env


def pip_deps_from_mcp(mcp: list[tuple[str, str]]) -> list[str]:
    """Pip packages a uv project must install for its MCP servers.

    Maps each MCP server command to a PyPI package where possible: ``uvx <pkg>`` -> ``<pkg>``
    (the server the project pins so it need not be fetched at runtime). Skips node servers
    (``bunx``/``npx``) and stabbur's own bundled ``stabbur-mcp-*`` (they ship with the ``stabbur`` dep).
    """
    deps: set[str] = set()
    for _name, command in mcp:
        toks = shlex.split(command)
        i = 0
        if toks and toks[0] == "env":  # skip a leading `env VAR=val ...` prefix
            i = 1
            while i < len(toks) and "=" in toks[i]:
                i += 1
        rest = toks[i:]
        if len(rest) >= 2 and rest[0] == "uvx":
            deps.add(rest[1].split("==")[0].split("@")[0])
    return sorted(deps)


def add_pyproject_dep(path: Path, pkg: str) -> bool:
    """Add ``pkg`` to a pyproject's ``[project].dependencies``. Returns True if it was inserted.

    A light text edit (no TOML round-trip, so comments/formatting survive): inserts into a
    multiline ``dependencies = [`` list, or expands an empty ``dependencies = []``. Idempotent —
    a dep already present (any version/extras form) is left alone.
    """
    text = path.read_text()
    if re.search(rf'["\']{re.escape(pkg)}(?:["\'\[]|==|>=|<=|~=|!=|>|<|@|;|\s)', text):
        return False
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"\s*dependencies\s*=\s*\[\s*\]\s*$", line):  # empty inline list
            lines[i] = line.replace("[]", f'[\n    "{pkg}",\n]')
            path.write_text("".join(lines))
            return True
        if re.match(r"\s*dependencies\s*=\s*\[\s*$", line):  # start of a multiline list
            lines.insert(i + 1, f'    "{pkg}",\n')
            path.write_text("".join(lines))
            return True
    return False


def render_pyproject(name: str, mcp: list[tuple[str, str]], mlx: bool, extras: list[str] | None = None) -> str:
    """Render a project ``pyproject.toml`` that makes the project a self-contained uv project.

    Pins ``stabbur`` from PyPI (at least the version doing the scaffolding) plus any
    pip-installable MCP servers, so ``uv run stabbur serve`` uses this project's own environment —
    no global stabbur, no runtime ``uvx`` fetches. ``extras`` are stabbur extras the project needs
    (e.g. ``voice``, ``web``); ``mlx`` adds the MLX runtime extra when the bound model is MLX.

    Scaffolding *from a source checkout* additionally pins that checkout as an editable source, so
    a stabbur developer's project tracks their working copy. An installed stabbur emits no source
    pin at all: there is no checkout to point at.
    """
    pkg_name = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-._") or "stabbur-project"
    all_extras: set[str] = set(extras or [])
    if mlx:
        all_extras.add("mlx")
    extras_spec = f"[{','.join(sorted(all_extras))}]" if all_extras else ""
    deps = [f"stabbur{extras_spec}>={_stabbur_version()}", *pip_deps_from_mcp(mcp)]
    dep_lines = "".join(f"    {json.dumps(d)},\n" for d in deps)
    checkout = stabbur_repo_root()
    source = (
        ""
        if checkout is None
        else (
            "\n[tool.uv.sources]\n"
            "# Scaffolded from a stabbur checkout, so the project tracks it. This line is\n"
            "# machine-specific -- delete it to use the published stabbur instead.\n"
            f"stabbur = {{ path = {json.dumps(str(checkout))}, editable = true }}\n"
        )
    )
    return (
        "[project]\n"
        f"name = {json.dumps(pkg_name)}\n"
        'version = "0.0.0"\n'
        'description = "A stabbur assistant project."\n'
        'requires-python = ">=3.13"\n'
        "dependencies = [\n"
        f"{dep_lines}"
        "]\n"
        f"{source}"
    )


def _stabbur_version() -> str:
    """The running stabbur's version, as the floor for a scaffolded project's dependency."""
    from stabbur import __version__  # noqa: PLC0415 - avoid an import cycle at module load

    return "0.0.0" if __version__ == "unknown" else __version__


_README = """\
# {name} -- a stabbur assistant

A self-contained [stabbur](https://github.com/winterop-com/stabbur) project: its own uv
environment, model library, and assistant definition (`stabbur.toml`).

## Run

```bash
uv sync                     # build the project's environment (stabbur + its MCP servers)
uv run stabbur serve --ui      # browser UI
uv run stabbur chat            # terminal chat
```

The assistant (model, system prompt, tools) is defined in `stabbur.toml`. The model lives in
`{library}/` (not committed). Machine secrets go in `.env` (not committed).
"""

_GITIGNORE = f"""\
# stabbur project — the local model library holds large weights; don't commit them.
/{LOCAL_LIBRARY}/

# Machine-specific config / secrets.
.env

# Local d2w profile store — holds credentials (minted PATs, session cookies).
.dhis2/

# uv project environment (rebuilt with `uv sync`; keep uv.lock).
.venv/

# Python / OS noise.
__pycache__/
*.py[cod]
.DS_Store
"""


def render_readme(name: str) -> str:
    """Render the project's README.md (a self-contained-uv-project quickstart)."""
    return _README.format(name=name, library=LOCAL_LIBRARY)


def git_init(target: Path) -> tuple[bool, str]:
    """``git init`` the project + write a ``.gitignore``; return ``(ok, status)`` for the caller to print.

    Best-effort: an existing repo is left alone, and a missing/failing ``git`` yields ``ok=False``
    (a warning) rather than raising — the project itself is already written. ``ok`` lets the caller
    color the status (dim vs. yellow).
    """
    gitignore = target / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(_GITIGNORE)
    if (target / ".git").exists():
        return True, f"already a repo — wrote {gitignore.name}"
    try:
        subprocess.run(
            ["git", "init", "-q"],  # noqa: S607 - git on PATH; fixed args
            cwd=target,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # git missing or failed
        return False, f"init skipped ({exc}); wrote {gitignore.name}"
    return True, f"initialized repo + {gitignore.name}"
