"""Load a project manifest (``kodo.toml``) — a thin assistant definition.

A project declares which model to use, MCP servers for tools, and a system
prompt, so `kodo chat` in a project directory picks them up without flags.
"""

import shlex
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class ProjectMcp(BaseModel):
    """One MCP server the project uses for tools."""

    command: str
    name: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    """Environment variables for this server (``[[mcp]].env``), merged over kodo's base env."""

    def to_spec(self) -> tuple[str | None, list[str], dict[str, str]]:
        """The ``(name, argv, env)`` spec :func:`kodo.tools.connect` expects."""
        return self.name, shlex.split(self.command), self.env


class Project(BaseModel):
    """A kodo project (assistant) manifest."""

    model: str | None = None
    system_prompt: str = ""
    mcp: list[ProjectMcp] = []
    chat_voice: str | None = None
    """Voice for spoken replies in chat (e.g. ``kokoro:af_heart``); ``None`` = the UI default."""
    voice_enabled: bool = True
    """``[voice] enabled``; ``false`` hides the Voice surface for a pure-text assistant."""
    # Libraries this project uses, in priority order (read: first match wins).
    # Entries are paths relative to the project dir (e.g. ``.kodo/library``), or the
    # token ``@shared`` for the machine's default library (``library_root``). Empty
    # → just the default library. See :func:`kodo.library.roots`.
    libraries: list[str] = []


class ProjectError(RuntimeError):
    """A ``kodo.toml`` that exists but can't be parsed or validated — surfaced cleanly, not as a traceback."""


def load(path: Path = Path("kodo.toml")) -> Project | None:
    """Load ``kodo.toml`` from ``path``, or ``None`` if it doesn't exist.

    Raises :class:`ProjectError` (with a readable message) on malformed TOML or a bad manifest —
    ``kodo mcp add`` tells users to hand-edit this file, so a typo must not crash every command.
    """
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"{path} is not valid TOML: {exc}") from exc
    project = data.get("project", {})
    voice = data.get("voice", {})
    libraries = data.get("libraries", [])
    try:
        return Project(
            model=project.get("model"),
            system_prompt=project.get("system_prompt", ""),
            chat_voice=project.get("chat_voice"),
            voice_enabled=bool(voice.get("enabled", True)) if isinstance(voice, dict) else True,
            mcp=[
                ProjectMcp(
                    command=entry["command"],
                    name=entry.get("name"),
                    env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                )
                for entry in data.get("mcp", [])
            ],
            libraries=[str(x) for x in libraries] if isinstance(libraries, list) else [],
        )
    except KeyError as exc:
        raise ProjectError(f"{path}: an [[mcp]] entry is missing its required 'command' key ({exc}).") from exc
    except (TypeError, ValidationError) as exc:
        raise ProjectError(f"{path} has an invalid value: {exc}") from exc
