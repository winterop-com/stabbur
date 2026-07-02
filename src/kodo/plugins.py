"""kodo's plugin seam (built on pluginkit).

kodo is the host. Plugins register under the ``kodo.plugins`` entry-point group (first-party
workspace members and anything a user pip-installs) and contribute top-level CLI command
groups via the ``commands`` extension point.

To avoid an import cycle — kodo bundles some plugin packages, so a plugin must not import
kodo at runtime — kodo passes a :class:`PluginContext` into ``commands``. The plugin calls
back through that context (serve a model, run a completion, resolve a library model) and
references the ``PluginContext`` type only under ``TYPE_CHECKING``. Plugins declare their
implementations with their own ``pluginkit.Extension("kodo")`` (matched by project name),
so they depend on pluginkit, never on kodo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import typer
from pluginkit import ExtensionPoint, PluginManager

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from rich.console import Console

    from kodo.library import LibraryModel

PROJECT = "kodo"
ENTRYPOINT_GROUP = "kodo.plugins"

extension_point = ExtensionPoint(PROJECT)


class PluginContext(Protocol):
    """Host services kodo injects into a plugin so the plugin needn't import kodo.

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


class Specs:
    """kodo's extension points. A plugin implements matching ``@extension`` methods."""

    @staticmethod
    @extension_point
    def commands(context: PluginContext) -> tuple[str, typer.Typer]:
        """Return ``(group_name, app)`` to mount as a top-level kodo command group."""
        raise NotImplementedError  # a spec: plugins provide the implementation


def load_plugins() -> PluginManager:
    """Build a PluginManager and discover plugins from the ``kodo.plugins`` entry-point group.

    ``ignore_errors`` keeps one broken third-party plugin from taking down the whole CLI.
    """
    pm = PluginManager(PROJECT)
    pm.add_extension_points(Specs)
    pm.load_entrypoints(ENTRYPOINT_GROUP, ignore_errors=True)
    return pm


def command_groups(pm: PluginManager, context: PluginContext) -> list[tuple[str, typer.Typer]]:
    """Every ``(name, app)`` command group contributed by the loaded plugins."""
    return list(pm.caller(Specs.commands)(context=context))
