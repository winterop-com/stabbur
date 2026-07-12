"""`heim config` - read and write the machine config (get/set/list/path)."""

from pathlib import Path
from typing import Annotated

import typer

from heim import (
    config,
    userconfig,
)
from heim.cli._app import config_app
from heim.cli._common import (
    console,
)


@config_app.command("path")
def config_path() -> None:
    """Print the machine config file path."""
    console.print(str(userconfig.config_path()))


def _config_field(key: str) -> str:
    """Resolve a `heim config` key to its Settings field name, or exit with a clear error."""
    field = userconfig.WRITABLE.get(key)
    if field is None:
        typer.secho(
            f"Unknown key {key!r}. Known keys: {', '.join(userconfig.WRITABLE)}.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)
    return field


@config_app.command("get")
def config_get(
    key: Annotated[str, typer.Argument(help=f"Config key: {', '.join(userconfig.WRITABLE)}.")],
) -> None:
    """Print the effective value of one config key (empty if unset) — scriptable.

    The effective value folds in env vars / project heim.toml / .env that outrank the machine
    config, so `$(heim config get server)` is what `heim chat` would actually use.
    """
    value = getattr(config.Settings(), _config_field(key))
    if value is not None:
        print(str(value))  # noqa: T201 - raw stdout for command substitution


@config_app.command("list")
@config_app.command("ls", hidden=True)
def config_list() -> None:
    """List the machine config file and the effective (resolved) defaults."""
    path = userconfig.config_path()
    stored = userconfig.read()
    console.print(f"[bold]Machine config[/]  {path}" + ("" if path.is_file() else "  [dim](not created yet)[/]"))
    if stored:
        for key, field in userconfig.WRITABLE.items():
            if field in stored:
                console.print(f"  {key} = {stored[field]!r}")
    # The effective values fold in env vars / project heim.toml / .env that outrank this file.
    settings = config.Settings()
    console.print("\n[bold]Effective[/] [dim](after env / project / .env override)[/]")
    console.print(f"  library-root  {settings.library_root or '[yellow](not set)[/]'}")
    console.print(f"  model         {settings.default_model or '[dim](none — free-play)[/]'}")
    console.print(f"  server        {settings.chat_server or '[dim](none — heim chat loads per call)[/]'}")


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help=f"Config key: {', '.join(userconfig.WRITABLE)}.")],
    value: Annotated[str, typer.Argument(help="The value to store.")],
) -> None:
    """Set a machine default (persisted to the config file), e.g. `heim config set model <name>`."""
    field = _config_field(key)
    stored = value
    if field == "library_root":  # persist an absolute, ~-expanded path so it resolves from any cwd
        stored = str(Path(value).expanduser().resolve())
    written = userconfig.set_value(field, stored)
    console.print(f"[green]Set[/] {key} = {stored}\n[dim]{written}[/]")
