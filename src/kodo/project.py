"""Load a project manifest (``kodo.toml``) — a thin assistant definition.

A project declares which model to use, MCP servers for tools, and a system
prompt, so `kodo chat` in a project directory picks them up without flags.
"""

import tomllib
from pathlib import Path

from pydantic import BaseModel


class ProjectMcp(BaseModel):
    """One MCP server the project uses for tools."""

    command: str
    name: str | None = None


class Project(BaseModel):
    """A kodo project (assistant) manifest."""

    model: str | None = None
    system_prompt: str = ""
    mcp: list[ProjectMcp] = []
    # Libraries this project uses, in priority order (read: first match wins).
    # Entries are paths relative to the project dir (e.g. ``.kodo/library``), or the
    # token ``@shared`` for the machine's default library (``library_root``). Empty
    # → just the default library. See :func:`kodo.library.roots`.
    libraries: list[str] = []


def load(path: Path = Path("kodo.toml")) -> Project | None:
    """Load ``kodo.toml`` from ``path``, or ``None`` if it doesn't exist."""
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text())
    project = data.get("project", {})
    libraries = data.get("libraries", [])
    return Project(
        model=project.get("model"),
        system_prompt=project.get("system_prompt", ""),
        mcp=[ProjectMcp(**entry) for entry in data.get("mcp", [])],
        libraries=[str(x) for x in libraries] if isinstance(libraries, list) else [],
    )
