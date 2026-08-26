"""Durable machine-level config — the write-mode home for ``stabbur config`` / ``stabbur setup``.

Three config layers, kept distinct on purpose:

- a project's ``stabbur.toml`` — portable, per-project, committed (model + tools + libraries);
- **this** — per-machine defaults (the library location, a default model to use outside a
  project), persisted at ``$XDG_CONFIG_HOME/stabbur/config.toml`` so a fresh machine needs no
  shell export or cwd ``.env``;
- ``~/.stabbur`` — ephemeral runtime state (pidfiles/logs), which must never travel.

It's a flat TOML file of scalar ``field = value`` lines whose keys are :class:`stabbur.config.Settings`
field names, fed in as the lowest-priority settings source (a real ``STABBUR_*`` env var or a project
``stabbur.toml`` still wins). Reads go through :mod:`stabbur.config`; writes go through :func:`set_value`.
"""

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from stabbur import fsatomic

# Fields writable via ``stabbur config set`` (friendly CLI key -> Settings field / TOML key).
WRITABLE: dict[str, str] = {
    "library-root": "library_root",
    "model": "default_model",
    "server": "chat_server",
    # Change `stabbur serve`'s address machine-wide (it defaults to a fixed port, so the URL
    # is already stable; set these to move it).
    "port": "port",
    "host": "host",
}


def config_dir() -> Path:
    """Stabbur's XDG config dir (``$XDG_CONFIG_HOME/stabbur``, else ``~/.config/stabbur``)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "stabbur"


def config_path() -> Path:
    """Path to the machine config file (``<config_dir>/config.toml``)."""
    return config_dir() / "config.toml"


def default_library_dir() -> Path:
    """The fallback library location when no drive is configured.

    The XDG data dir (``$XDG_DATA_HOME/stabbur/library``, else ``~/.local/share/stabbur/library``).
    Only a fallback that ``stabbur setup`` offers — the intended home is an external drive set via
    ``stabbur config set library-root``; the library is portable data, distinct from the config
    dir (here) and the ephemeral ``~/.stabbur`` runtime state.
    """
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "stabbur" / "library"


def read() -> dict[str, Any]:
    """Parse the machine config into a dict — ``{}`` if the file doesn't exist.

    Raises:
        ValueError: if the file exists but isn't valid TOML.
    """
    path = config_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} is not valid TOML: {exc}") from exc


def set_value(field: str, value: str | int) -> Path:
    """Set one top-level ``field`` (a :class:`Settings` field name) in the machine config.

    Rewrites the whole flat file with the updated value, re-parsing the result before it
    touches disk so a bad value never corrupts the file (mirrors how ``stabbur.toml`` is edited).
    A JSON-encoded scalar is a valid TOML value, so ``json.dumps`` is the serializer.

    Returns:
        The path written.
    """
    data = read()
    data[field] = value
    text = "".join(f"{key} = {json.dumps(val)}\n" for key, val in sorted(data.items()))
    tomllib.loads(text)  # validate the result before writing
    path = config_path()
    fsatomic.write_text(path, text)
    return path
