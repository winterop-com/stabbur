"""heim's plugin seam (built on pluginkit).

heim is the host. Plugins register under the ``heim.plugins`` entry-point group (first-party
workspace members and anything a user pip-installs) and contribute top-level CLI command
groups via the ``commands`` extension point.

To avoid an import cycle — heim bundles some plugin packages, so a plugin must not import
heim at runtime — heim passes a :class:`PluginContext` into ``commands``. The plugin calls
back through that context (serve a model, run a completion, resolve a library model) and
references the ``PluginContext`` type only under ``TYPE_CHECKING``. Plugins declare their
implementations with their own ``pluginkit.Extension("heim")`` (matched by project name),
so they depend on pluginkit, never on heim.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

import typer
from pluginkit import ExtensionPoint, PluginManager
from pydantic import BaseModel, ConfigDict, Field

from heim.models import McpSetting

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from rich.console import Console

    from heim.library import LibraryModel

PROJECT = "heim"
ENTRYPOINT_GROUP = "heim.plugins"

extension_point = ExtensionPoint(PROJECT)


class PluginContext(Protocol):
    """Host services heim injects into a plugin so the plugin needn't import heim.

    A plugin's ``commands`` implementation receives this and uses it to reach the model
    runtime: resolve a library model, serve it (yielding an OpenAI ``base`` URL), and run
    single completions against that base.
    """

    console: Console

    def resolve_model(self, name: str | None, model_format: str | None = None) -> LibraryModel:
        """Resolve a generative library model by name (or exit with a helpful message)."""
        ...

    def serve(self, model: LibraryModel) -> AbstractContextManager[str]:
        """Start the model's runtime; the context yields its OpenAI ``base`` URL."""
        ...

    def complete(
        self, base: str, model: LibraryModel, prompt: str, system: str = "", max_tokens: int | None = None
    ) -> str:
        """Run one completion against an already-served ``base`` and return the reply text."""
        ...

    def run_agent(
        self, base: str, model: LibraryModel, prompt: str, servers: list[str]
    ) -> tuple[list[tuple[str, dict[str, object]]], str]:
        """Run the agent loop against ``base`` with ``servers`` (MCP commands) attached.

        Returns the trace of ``(tool_name, args)`` the model called and its final answer —
        the inputs a ``tool`` benchmark scores against.
        """
        ...

    def list_models(self) -> list[LibraryModel]:
        """Every generative library model (for an all-models benchmark sweep)."""
        ...

    def supports_tools(self, model: LibraryModel) -> bool:
        """Whether the model's detected capabilities include tool/function calling."""
        ...

    def model_tags(self, model: LibraryModel) -> list[str]:
        """The library tags on a model (used to gate which models a suite runs)."""
        ...


class McpServer(BaseModel):
    """An MCP tool server a plugin advertises: how to spawn it, what it offers, what it reads."""

    model_config = ConfigDict(frozen=True)

    name: str  # tool namespace / display name, e.g. "datetime"
    command: str  # spawn command, e.g. "heim-mcp-datetime"
    description: str = ""
    settings: list[McpSetting] = Field(default_factory=list)
    """The environment variables this server understands, declared by the server itself.

    A server that reads env is otherwise a black box — the description can say "a configured
    workspace root" but nothing can say *which*. Declaring them lets heim show the value in force
    and offer an edit, instead of sending the user to hand-edit ``mcp.json``.
    """


class Specs:
    """heim's extension points. A plugin implements matching ``@extension`` methods."""

    @staticmethod
    @extension_point
    def commands(context: PluginContext) -> tuple[str, typer.Typer]:
        """Return ``(group_name, app)`` to mount as a top-level heim command group."""
        raise NotImplementedError  # a spec: plugins provide the implementation

    @staticmethod
    @extension_point
    def mcp_servers() -> list[dict[str, object]]:
        """Advertise MCP servers this package ships — dicts of name/command/description/settings.

        Advertise-only: no command, no ``PluginContext``. Plain dicts (not ``McpServer``)
        so a plugin needn't import heim. Lets heim discover/validate ``--mcp`` targets and
        populate tool pickers instead of hardcoding them.

        ``settings`` is optional and JSON-shaped all the way down — a list of
        ``{"env", "label", "description", "type", "default"}`` dicts (see :class:`heim.models.McpSetting`)
        naming the environment variables the server reads. The values stay plain strings/dicts for the
        same reason the entry itself does: a plugin declares what it understands without importing heim.
        """
        raise NotImplementedError


def load_plugins() -> PluginManager:
    """Build a PluginManager and discover plugins from the ``heim.plugins`` entry-point group.

    ``ignore_errors`` keeps one broken third-party plugin from taking down the whole CLI.
    """
    pm = PluginManager(PROJECT)
    pm.add_extension_points(Specs)
    pm.load_entrypoints(ENTRYPOINT_GROUP, ignore_errors=True)
    return pm


@lru_cache(maxsize=1)
def manager() -> PluginManager:
    """The process-wide PluginManager (entry-point discovery runs once)."""
    return load_plugins()


def command_groups(pm: PluginManager, context: PluginContext) -> list[tuple[str, typer.Typer]]:
    """Every ``(name, app)`` command group contributed by the loaded plugins."""
    return list(pm.caller(Specs.commands)(context=context))


def advertised_servers(pm: PluginManager) -> list[McpServer]:
    """Every MCP server advertised by the loaded plugins, sorted by name."""
    # model_validate, not **entry: an advertisement is plain JSON-shaped data (its `settings` are
    # dicts, not McpSetting instances), so it is parsed the way any untrusted payload is.
    servers = [McpServer.model_validate(entry) for group in pm.caller(Specs.mcp_servers)() for entry in group]
    return sorted(servers, key=lambda s: s.name)


def resolve_mcp(value: str) -> tuple[str | None, str]:
    """Resolve a ``--mcp`` value to ``(name, command)``.

    An advertised server *name* (or *command*) maps to that server; anything else is used
    verbatim as a command with no name (so raw commands still work).
    """
    for server in advertised_servers(manager()):
        if value in (server.name, server.command):
            return server.name, server.command
    return None, value
