"""stabbur plugin: advertise the git MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-git`` a plain MCP server — no CLI command, never imports stabbur — while letting stabbur
discover it (``--mcp`` resolution, ``stabbur mcp list``, tool pickers) via the ``stabbur.plugins`` entry
point. Read-only by default; not seeded by ``stabbur setup`` — add it deliberately with
``stabbur mcp add git``.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class GitPlugin:
    """Advertises the git server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``stabbur-mcp-git`` (read-only git inspection sandboxed to one repo).

        Only the repo root is declared. ``STABBUR_GIT_ALLOW_WRITE`` is a reserved gate — no mutating
        tool ships yet — so offering a switch for it would be a control that does nothing.
        """
        return [
            {
                "name": "git",
                "command": "stabbur-mcp-git",
                "description": "Read-only git inspection (status, log, diff, show, blame) sandboxed to one repo.",
                "settings": [
                    {
                        "env": "STABBUR_GIT_REPO_ROOT",
                        "label": "Repository",
                        "description": "The one work tree every git command runs in (git -C <root>).",
                        "type": "path",
                        "default": ".",
                    }
                ],
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = GitPlugin()
