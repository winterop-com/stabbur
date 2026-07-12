"""heim's command-line interface, split by command group.

Importing this package builds the full Typer ``app``: :mod:`heim.cli._app` defines the app and its
sub-apps, each command-group module (``library``, ``project``, ``mcp``, ``voice``, ``config``,
``health``, ``chat``, ``serve``) hangs its commands off them on import, and ``_mount_plugins`` then
adds any plugin command groups. Run it via the ``heim`` entry point (``heim.cli:main``).

The command modules are imported for their side effect (registering commands); a few helpers are
re-exported so ``heim.cli.<name>`` keeps working for tests and callers. Everything importable from
this package is listed in ``__all__``.
"""

from heim.cli import chat, config, health, library, mcp, project, serve, voice
from heim.cli._app import _mount_plugins, app, main
from heim.cli._common import _normalize_server_url, _uninstalled_optional
from heim.cli.project import _pick_model_interactive, _pick_tools_interactive

_mount_plugins()

__all__ = [
    "app",
    "main",
    # command-group modules — imported to register their commands on the Typer app
    "chat",
    "config",
    "health",
    "library",
    "mcp",
    "project",
    "serve",
    "voice",
    # helpers kept importable as `heim.cli.<name>` (tests / back-compat)
    "_normalize_server_url",
    "_uninstalled_optional",
    "_pick_model_interactive",
    "_pick_tools_interactive",
]
