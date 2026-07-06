"""The project manifest (``kodo.toml``) — read *and* write in one place.

A project declares which model to use, MCP servers for tools, a system prompt, and which
libraries it composes, so ``kodo chat`` in a project directory picks them up without flags.

This module is the **single owner** of the project side of ``kodo.toml`` (A1):

* :func:`read_raw` is the one TOML parser — both this module and :mod:`kodo.config` (which reads
  the *machine* settings, e.g. ``library_root``, from the same file) go through it, so a malformed
  file fails one way (a clean :class:`ProjectError`), not two.
* :func:`load` turns the parse into a validated :class:`Project` model.
* :func:`render_manifest` and :func:`add_mcp` **own writes** — a fresh file is rendered from
  values, and an edit is validated (re-parsed) before it's written, so a write never leaves a
  broken ``kodo.toml`` behind.

``kodo.toml`` has two readers by design: *machine* settings (env-overridable, per-machine) live in
:class:`kodo.config.Settings`; the *portable* assistant manifest (``[project]`` / ``[[mcp]]`` /
``[voice]`` / ``libraries``) lives here. Same file, two purposes, one parser.
"""

import json
import shlex
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

_DEFAULT_PATH = Path("kodo.toml")


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


# --- reading ---------------------------------------------------------------------------------


def read_raw(path: Path = _DEFAULT_PATH) -> dict[str, Any]:
    """Parse ``kodo.toml`` into a raw dict — the one TOML parser for the whole app.

    Returns ``{}`` if the file doesn't exist. Both :func:`load` (the manifest) and
    :mod:`kodo.config` (the machine settings) call this, so malformed TOML raises a single
    :class:`ProjectError` from one place instead of crashing differently in each reader.
    """
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"{path} is not valid TOML: {exc}") from exc


def load(path: Path = _DEFAULT_PATH) -> Project | None:
    """Load the project manifest from ``path``, or ``None`` if the file doesn't exist.

    Raises :class:`ProjectError` (with a readable message) on malformed TOML or a bad manifest —
    ``kodo mcp add`` tells users to hand-edit this file, so a typo must not crash every command.
    """
    if not path.is_file():
        return None
    data = read_raw(path)
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


# --- writing (owned here so a write never leaves a broken kodo.toml) -------------------------

SHARED_LIBRARY_TOKEN = "@shared"


def _inline_env(env: dict[str, str]) -> str:
    """Render an env dict as a TOML inline table: ``{ K = "V", … }``."""
    return "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }"


def _mcp_block(mcp: ProjectMcp) -> str:
    """Serialize one ``[[mcp]]`` array-of-tables entry (name/command/env)."""
    block = f"[[mcp]]\nname = {json.dumps(mcp.name)}\ncommand = {json.dumps(mcp.command)}\n"
    if mcp.env:
        block += f"env = {_inline_env(mcp.env)}\n"
    return block


def render_manifest(
    *,
    model: str,
    system_prompt: str = "",
    mcp: list[ProjectMcp] | None = None,
    local_library_dir: str | None = None,
    chat_voice: str | None = None,
) -> str:
    """Render a fresh ``kodo.toml`` from values (used by ``project init`` / ``project new``).

    Portable and git-committable — no machine-specific paths. ``libraries`` lists a project-local
    store (``local_library_dir``, created alongside this file) plus ``@shared``, the token for the
    machine's default library (``KODO_LIBRARY_ROOT``); ``None`` means the project uses only the
    shared library. ``[project]`` / ``[[mcp]]`` define the assistant. Override anything per machine
    with ``KODO_*``.
    """
    if mcp:
        blocks = [_mcp_block(m) for m in mcp]
        tools_block = "# Tools via MCP — the assistant's toolset.\n" + "\n".join(blocks) + "\n"
    else:
        tools_block = (
            "# Tools via MCP (repeatable; add a server to give the assistant tools):\n"
            "# [[mcp]]\n"
            '# name = "datetime"\n'
            '# command = "kodo-mcp-datetime"\n'
        )
    if local_library_dir:
        libraries_block = (
            f'# This project ships its own "{local_library_dir}/" store (the model was downloaded there);\n'
            "# @shared is the machine default library (KODO_LIBRARY_ROOT) if you set one.\n"
            f'libraries = ["{local_library_dir}", "{SHARED_LIBRARY_TOKEN}"]\n\n'
        )
    else:
        libraries_block = (
            "# Uses your machine library (KODO_LIBRARY_ROOT). To also read a project-local\n"
            f'# store, add:  libraries = ["models", "{SHARED_LIBRARY_TOKEN}"]  (relative to this file).\n\n'
        )
    # Kokoro (tiny) is the default speak-replies voice for every project, so any assistant
    # can talk back without loading a second multi-GB model.
    voice_line = f"chat_voice = {json.dumps(chat_voice)}  # spoken-reply voice (Kokoro)\n" if chat_voice else ""
    return (
        "# kodo project — a purpose-built assistant (model + system prompt + tools).\n"
        "# Portable + committable: no machine-specific paths.\n\n"
        f"{libraries_block}"
        "[project]\n"
        f"model = {json.dumps(model)}\n"
        f"system_prompt = {json.dumps(system_prompt)}\n"
        f"{voice_line}\n"
        f"{tools_block}"
    )


def add_mcp(path: Path, mcp: ProjectMcp) -> None:
    """Append one ``[[mcp]]`` server to an existing ``kodo.toml``, validating before it writes.

    Appending is the least destructive edit (it preserves the user's comments and formatting),
    but a blind append can produce broken TOML if the file ends oddly. So this normalizes the
    separator, builds the new text in memory, **re-parses it to validate**, and only then writes —
    a malformed result raises :class:`ProjectError` and leaves the file untouched (A1).

    Raises:
        ProjectError: The file is missing, or the resulting TOML would be invalid.
    """
    if not path.is_file():
        raise ProjectError(f"{path} does not exist — run `kodo project init` first.")
    current = path.read_text(encoding="utf-8")
    separator = "" if current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
    new_text = f"{current}{separator}{_mcp_block(mcp)}"
    try:
        tomllib.loads(new_text)  # validate the *result* before touching the file
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"adding the MCP server would make {path} invalid TOML: {exc}") from exc
    path.write_text(new_text, encoding="utf-8")
