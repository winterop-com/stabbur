"""`kodo mcp` - browse the MCP catalog and add/remove servers in .mcp.json."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich import box
from rich.table import Table

from kodo import (
    mcpservers,
    scaffold,
)

if TYPE_CHECKING:
    pass

from kodo.cli._app import mcp_app
from kodo.cli._common import (
    _CURATED_MCP,
    _OPTIONAL_FIRST_PARTY,
    _to_mcp_server,
    _uninstalled_optional,
    console,
)

# Project templates now live in kodo.templates (TEMPLATES).


def _project_mcp_keys() -> set[str]:
    """Names of MCP servers already in the local ``.mcp.json`` (empty if none)."""
    return {s.name for s in mcpservers.read_project()}


@mcp_app.command("list")
@mcp_app.command("ls", hidden=True)  # `ls` alias, mirroring `kodo library ls`
def mcp_list() -> None:
    """List MCP tool servers: first-party plugins first, then an external catalog.

    First-party ``kodo-mcp-*`` plugins are the recommended set — kodo controls them, they
    need no external runtime, and they're always up to date. The external catalog is a
    fallback for tools kodo doesn't ship yet. A [green]✓[/] marks a server already in this
    directory's ``kodo.toml``; add one with ``kodo mcp add <name>``.
    """
    from kodo import plugins  # noqa: PLC0415

    in_project = _project_mcp_keys()

    def mark(name: str, command: str) -> str:
        return "[green]✓[/]" if name in in_project or command in in_project else ""

    # First-party plugins lead: they're what kodo controls and recommends.
    servers = plugins.advertised_servers(plugins.manager())
    if servers:
        plug = Table(
            box=box.SIMPLE,
            header_style="bold",
            title="[bold]Installed plugins[/] [dim]— first-party, kodo-controlled (recommended)[/]",
            title_justify="left",
        )
        plug.add_column("IN", justify="center")
        plug.add_column("NAME", style="cyan")
        plug.add_column("COMMAND")
        plug.add_column("DESCRIPTION", style="dim")
        for s in servers:
            plug.add_row(mark(s.name, s.command), s.name, s.command, s.description)
        console.print(plug)

    # Optional first-party servers not yet installed — show them with an install hint so they're
    # discoverable before `make install-<name>` (once installed they move up to Installed plugins).
    optional = _uninstalled_optional({s.name for s in servers})
    if optional:
        opt = Table(
            box=box.SIMPLE,
            header_style="bold",
            title="[bold]Optional first-party[/] [dim]— not installed yet[/]",
            title_justify="left",
        )
        opt.add_column("NAME", style="cyan")
        opt.add_column("INSTALL")
        opt.add_column("DESCRIPTION", style="dim", overflow="fold")
        for o in optional:
            opt.add_row(o.name, o.setup.replace("install with: ", ""), o.description)
        console.print(opt)

    catalog = Table(
        box=box.SIMPLE,
        header_style="bold",
        title="[bold]External catalog[/] [dim]— third-party servers, added on demand[/]",
        title_justify="left",
    )
    catalog.add_column("IN", justify="center")
    catalog.add_column("NAME", style="cyan")
    catalog.add_column("COMMAND")
    catalog.add_column("DESCRIPTION", style="dim", overflow="fold")
    for c in _CURATED_MCP:
        desc = c.description + (f"\n[yellow]setup:[/] [dim]{c.setup}[/]" if c.setup else "")
        catalog.add_row(mark(c.name, c.command), c.name, c.command, desc)
    console.print(catalog)
    console.print("[dim]Add one to this project's kodo.toml (plugins or catalog):[/] kodo mcp add <name>")


@mcp_app.command("add")
def mcp_add(
    name: Annotated[str, typer.Argument(help="An installed plugin or external-catalog name (see `kodo mcp list`).")],
    to_global: Annotated[
        bool,
        typer.Option(
            "--global", "-g", help="Add to the machine-global ~/.config/kodo/mcp.json instead of this project."
        ),
    ] = False,
) -> None:
    """Add an MCP server to the standard ``mcpServers`` JSON (idempotent — replaces by name).

    Targets this directory's ``.mcp.json`` by default, or the machine-global
    ``~/.config/kodo/mcp.json`` with ``--global`` (tools every free-play chat gets). Resolves
    ``name`` against installed first-party plugins first, then the external catalog; edit the
    entry afterwards if its command has a placeholder (path/profile).
    """
    from kodo import plugins  # noqa: PLC0415

    # First-party plugins win a name clash — kodo controls them, so prefer them over external.
    server = next((s for s in plugins.advertised_servers(plugins.manager()) if s.name == name), None)
    uninstalled_optional = False
    if server is not None:
        entry_name, command, setup = server.name, server.command, ""
    else:
        # An optional first-party server (e.g. `web`) resolves here when its extra isn't installed
        # — add it, but warn it reports 0 tools until installed.
        chosen = next((c for c in _CURATED_MCP if c.name == name), None) or next(
            (c for c in _OPTIONAL_FIRST_PARTY if c.name == name), None
        )
        if chosen is None:
            console.print(f"[red]Unknown MCP {name!r}.[/] See [bold]kodo mcp list[/] for the catalog.")
            raise typer.Exit(1)
        entry_name, command, setup = chosen.name, chosen.command, chosen.setup
        uninstalled_optional = chosen in _OPTIONAL_FIRST_PARTY

    target = mcpservers.global_path() if to_global else mcpservers.project_path()
    existing = mcpservers.read_global() if to_global else mcpservers.read_project()
    if any(s.name == entry_name for s in existing):
        console.print(f"[yellow]{entry_name}[/] is already in {target}.")
        return

    # In a uv *project* (pyproject.toml present, not --global) the server is a pinned dependency,
    # so drop the runtime `uvx` fetch from the command and add the package to pyproject.toml.
    pyproject = Path("pyproject.toml")
    uv_project = not to_global and pyproject.is_file()
    mcpservers.add(_to_mcp_server(entry_name, scaffold.strip_uvx(command) if uv_project else command), glob=to_global)
    console.print(f"[green]Added[/] [cyan]{entry_name}[/] to {target}")

    if uv_project:
        for pkg in scaffold.pip_deps_from_mcp([(entry_name, command)]):  # original (uvx) command -> pip pkg
            if scaffold.add_pyproject_dep(pyproject, pkg):
                console.print(f"  [dim]uv:[/] pinned [cyan]{pkg}[/] in pyproject.toml — run [bold]uv sync[/]")
    if uninstalled_optional:
        console.print(f"  [yellow]not installed[/] — reports 0 tools until you {setup}")
    elif setup:
        console.print(f"  [yellow]setup:[/] {setup}")
    console.print("[dim]Check it:[/] kodo project show")


@mcp_app.command("remove")
@mcp_app.command("rm", hidden=True)
def mcp_remove(
    name: Annotated[str, typer.Argument(help="The server name to remove (see `kodo project show`).")],
    from_global: Annotated[
        bool, typer.Option("--global", "-g", help="Remove from the machine-global mcp.json instead of this project.")
    ] = False,
) -> None:
    """Remove an MCP server from this project's ``.mcp.json`` or the global ``mcp.json`` (``--global``)."""
    written = mcpservers.remove(name, glob=from_global)
    target = mcpservers.global_path() if from_global else mcpservers.project_path()
    if written is None:
        console.print(f"[yellow]{name}[/] is not in {target}.")
        raise typer.Exit(1)
    console.print(f"[green]Removed[/] [cyan]{name}[/] from {target}")
