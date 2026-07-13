"""`heim ext-dev` - interactive extension test-drive launcher (repo-only dev tool).

Thin Python front end for ``extension/e2e/try.ts``: it discovers the source checkout, checks the
dev preconditions (bun + installed deps), builds the extension fresh, then hands off to bun to
launch a HEADED Chromium with the extension loaded against a real ``heim serve`` (the live-tier
fixture). All the Playwright/browser logic lives in ``try.ts``; Python owns discovery,
preconditions, the build, the env contract, and the process lifecycle.
"""

import os
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from heim.cli._app import app
from heim.cli._common import console


class ExtFlavor(str, Enum):
    """Which extension build flavor to load (mirrors the wxt.config.ts flavors)."""

    generic = "generic"
    dhis2 = "dhis2"


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for the extension source (``extension/wxt.config.ts``).

    ``heim ext-dev`` is a source-checkout-only dev tool: it needs the actual extension tree to
    build and load, which a packaged install never ships. Returns the repo root (the dir holding
    ``extension/``) or ``None`` when run outside a checkout.
    """
    for directory in (start, *start.parents):
        if (directory / "extension" / "wxt.config.ts").is_file():
            return directory
    return None


@app.command("ext-dev")
def ext_dev(
    multi: Annotated[
        bool,
        typer.Option("--multi", help="Launch the two-target fixture (play42 + play41) to test tab-driven switching."),
    ] = False,
    flavor: Annotated[
        ExtFlavor,
        typer.Option("--flavor", help="Extension build flavor to build and load."),
    ] = ExtFlavor.generic,
    no_build: Annotated[
        bool,
        typer.Option("--no-build", help="Skip the build and load the existing output dir (default: build first)."),
    ] = False,
) -> None:
    """Test-drive the browser extension: headed Chromium + a real `heim serve`, until Ctrl+C.

    A repo-only dev tool (needs the extension source): builds the extension fresh, launches a
    headed Chromium with it loaded, and starts the live-tier `heim serve` (locked model + DHIS2
    bridge -> the play demo) so the side panel can be driven end-to-end. Ctrl+C tears down the
    browser and server. `--multi` swaps in the two-target (play42 + play41) fixture.
    """
    repo_root = _find_repo_root(Path.cwd())
    if repo_root is None:
        console.print("[red]heim ext-dev runs from a heim source checkout[/] (extension/wxt.config.ts not found).")
        raise typer.Exit(1)
    extension_dir = repo_root / "extension"

    if shutil.which("bun") is None:
        console.print("[red]bun not found on PATH.[/] Install it from https://bun.sh, then re-run.")
        raise typer.Exit(1)
    if not (extension_dir / "node_modules").is_dir():
        console.print(f"[red]Extension deps not installed.[/] Run [cyan]bun install[/] in {extension_dir}.")
        raise typer.Exit(1)

    out_suffix = "-dhis2" if flavor is ExtFlavor.dhis2 else ""
    output_dir = extension_dir / ".output" / f"chrome-mv3{out_suffix}"
    build_script = "build:dhis2" if flavor is ExtFlavor.dhis2 else "build"

    should_build = not no_build
    if no_build and not output_dir.is_dir():
        console.print(f"[yellow]No build at {output_dir}[/] — building despite --no-build (nothing to load otherwise).")
        should_build = True
    if should_build:
        console.print(f"[cyan]Building the extension[/] ({flavor.value}) …")
        built = subprocess.run(["bun", "run", build_script], cwd=extension_dir, check=False)  # noqa: S603, S607
        if built.returncode != 0:
            console.print("[red]Extension build failed[/] (see the bun output above).")
            raise typer.Exit(built.returncode)

    # The env-as-API handoff to try.ts (the engine): MULTI selects the two-target fixture, FLAVOR
    # picks which `.output/chrome-mv3*` dir it loads. Both default off so a bare `bun run e2e/try.ts`
    # stays byte-identical.
    env = os.environ.copy()
    if multi:
        env["HEIM_EXT_DEV_MULTI"] = "1"
    env["HEIM_EXT_DEV_FLAVOR"] = flavor.value

    console.print(f"[green]Launching[/] {'multi-target' if multi else 'play42'} test drive — Ctrl+C to stop.\n")
    # Hand off to bun via exec: it replaces this process, so a terminal Ctrl+C (SIGINT) is delivered
    # straight to bun/try.ts, whose handler already tears down the browser + heim serve. try.ts owns
    # all the Playwright logic; Python's job ends here.
    os.chdir(extension_dir)
    os.execvpe("bun", ["bun", "run", "e2e/try.ts"], env)  # noqa: S606
