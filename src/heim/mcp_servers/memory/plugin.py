"""heim plugin: advertise the memory MCP server (advertise-only, no command).

Keeps ``heim-mcp-memory`` a plain MCP server — it contributes no CLI command and never
imports heim — while letting heim discover it (``--mcp`` resolution, ``heim mcp list``, tool
pickers) instead of hardcoding it. Matched to the host by pluginkit's project name (``heim``)
via the ``heim.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class MemoryPlugin:
    """Advertises the memory server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``heim-mcp-memory`` (persistent notes / key-value memory).

        The declared default is *computed*, not the literal ``None`` the setting holds: unset, the
        store lands under the library (or ``./.heim/memory`` without one), which is a location the
        user can only learn by reading the source. ``memory_dir=None`` forces the fallback branch, so
        this reports where the notes go when nobody configures it — even if someone already has.
        ``.core`` is this package's own module (never the heim host), so no import cycle and nothing
        heavier than what running the server already costs.
        """
        from .core import MemorySettings  # noqa: PLC0415 - only for the computed default; keeps discovery cheap

        fallback = MemorySettings(memory_dir=None).notes_path().parent
        return [
            {
                "name": "memory",
                "command": "heim-mcp-memory",
                "description": "Persistent notes / key-value memory saved in the library (survives sessions).",
                "settings": [
                    {
                        "env": "HEIM_MEMORY_DIR",
                        "label": "Notes directory",
                        "description": "Where notes.json lives. Unset, it travels with the library.",
                        "type": "path",
                        "default": str(fallback),
                    }
                ],
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = MemoryPlugin()
