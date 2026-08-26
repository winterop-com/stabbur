"""stabbur plugin: advertise the memory MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-memory`` a plain MCP server — it contributes no CLI command and never
imports stabbur — while letting stabbur discover it (``--mcp`` resolution, ``stabbur mcp list``, tool
pickers) instead of hardcoding it. Matched to the host by pluginkit's project name (``stabbur``)
via the ``stabbur.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class MemoryPlugin:
    """Advertises the memory server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``stabbur-mcp-memory`` (persistent notes / key-value memory).

        The declared default is *computed*, not the literal ``None`` the setting holds: unset, the
        store lands under the library (or ``./.stabbur/memory`` without one), which is a location the
        user can only learn by reading the source. ``memory_dir=None`` forces the fallback branch, so
        this reports where the notes go when nobody configures it — even if someone already has.
        ``.core`` is this package's own module (never the stabbur host), so no import cycle and nothing
        heavier than what running the server already costs.
        """
        from .core import MemorySettings  # noqa: PLC0415 - only for the computed default; keeps discovery cheap

        fallback = MemorySettings(memory_dir=None).notes_path().parent
        return [
            {
                "name": "memory",
                "command": "stabbur-mcp-memory",
                "description": "Persistent notes / key-value memory saved in the library (survives sessions).",
                "settings": [
                    {
                        "env": "STABBUR_MEMORY_DIR",
                        "label": "Notes directory",
                        "description": "Where notes.json lives. Unset, it travels with the library.",
                        "type": "path",
                        "default": str(fallback),
                    }
                ],
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = MemoryPlugin()
