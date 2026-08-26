"""stabbur plugin: advertise the files MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-files`` a plain MCP server — no CLI command, never imports stabbur — while letting
stabbur discover it (``--mcp`` resolution, ``stabbur mcp list``, tool pickers). Matched to the host by
pluginkit's project name (``stabbur``) via the ``stabbur.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class FilesPlugin:
    """Advertises the files server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``stabbur-mcp-files`` (read-only file browse/read/search under a root).

        The root is declared, not just described: it is the single fact that decides what the
        assistant can see, and its default (``.``) resolves to wherever ``stabbur serve`` was launched —
        so a user who never set it gets answers about that directory and no way to tell. The two caps
        (``MAX_READ_BYTES`` / ``MAX_SEARCH_MATCHES``) stay undeclared: they bound one response, they
        don't change what the server reaches.
        """
        return [
            {
                "name": "files",
                "command": "stabbur-mcp-files",
                "description": "Browse, read, and search files under a configured workspace root (read-only).",
                "settings": [
                    {
                        "env": "STABBUR_FILES_ROOT",
                        "label": "Workspace root",
                        "description": "The only directory this server can reach — everything else is refused.",
                        "type": "path",
                        "default": ".",
                    },
                    {
                        "env": "STABBUR_FILES_WRITABLE",
                        "label": "Allow writes",
                        "description": "Add write_file, letting the assistant create and overwrite files.",
                        "type": "boolean",
                        "default": "false",
                    },
                ],
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = FilesPlugin()
