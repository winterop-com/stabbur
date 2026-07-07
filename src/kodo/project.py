"""The project manifest (``kodo.toml``) — read *and* write in one place.

A project declares which model to use, a system prompt, and which libraries it composes, so
``kodo chat`` in a project directory picks them up without flags. Tools are **not** here: MCP
servers live in the standard ``mcpServers`` JSON (:mod:`kodo.mcpservers`, ``./.mcp.json``).

This module is the **single owner** of the project side of ``kodo.toml`` (A1):

* :func:`read_raw` is the one TOML parser — both this module and :mod:`kodo.config` (which reads
  the *machine* settings, e.g. ``library_root``, from the same file) go through it, so a malformed
  file fails one way (a clean :class:`ProjectError`), not two.
* :func:`load` turns the parse into a validated :class:`Project` model.
* :func:`render_manifest` **owns writes** — a fresh file is rendered from values.

``kodo.toml`` has two readers by design: *machine* settings (env-overridable, per-machine) live in
:class:`kodo.config.Settings`; the *portable* assistant manifest (``[project]`` / ``[voice]`` /
``libraries``) lives here. Same file, two purposes, one parser.
"""

import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

_DEFAULT_PATH = Path("kodo.toml")


class Project(BaseModel):
    """A kodo project (assistant) manifest. Tools live in ``.mcp.json`` (:mod:`kodo.mcpservers`)."""

    model: str | None = None
    system_prompt: str = ""
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
    users hand-edit this file, so a typo must not crash every command.
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
            libraries=[str(x) for x in libraries] if isinstance(libraries, list) else [],
        )
    except (TypeError, ValidationError) as exc:
        raise ProjectError(f"{path} has an invalid value: {exc}") from exc


def resolve_model(explicit: str | None, proj: "Project | None") -> str | None:
    """The model to use: explicit CLI name > project model > machine default.

    Outside a project (free-play), the machine default (``settings.default_model``, set via
    ``kodo config set model``) supplies a model so ``kodo chat`` / ``serve --ui`` have one to
    load without a project or an explicit argument. In a project, its ``model`` still wins.
    """
    from kodo.config import get_settings  # noqa: PLC0415 - lazy: config imports project

    return explicit or (proj.model if proj else None) or get_settings().default_model


# --- writing (owned here so a write never leaves a broken kodo.toml) -------------------------

SHARED_LIBRARY_TOKEN = "@shared"


def render_manifest(
    *,
    model: str,
    system_prompt: str = "",
    local_library_dir: str | None = None,
    chat_voice: str | None = None,
) -> str:
    """Render a fresh ``kodo.toml`` from values (used by ``project init`` / ``project new``).

    Portable and git-committable — no machine-specific paths. ``libraries`` lists a project-local
    store (``local_library_dir``, created alongside this file) plus ``@shared``, the token for the
    machine's default library (``KODO_LIBRARY_ROOT``); ``None`` means the project uses only the
    shared library. ``[project]`` defines the assistant; tools live in ``.mcp.json``. Override
    anything per machine with ``KODO_*``.
    """
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
        "# kodo project — a purpose-built assistant (model + system prompt).\n"
        "# Portable + committable: no machine-specific paths. Tools live in .mcp.json.\n\n"
        f"{libraries_block}"
        "[project]\n"
        f"model = {json.dumps(model)}\n"
        f"system_prompt = {json.dumps(system_prompt)}\n"
        f"{voice_line}"
    )
