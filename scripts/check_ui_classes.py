#!/usr/bin/env python3
"""Check the browser UI's Tailwind classes against stabbur's UI conventions.

WHY THIS EXISTS AND WHY IT IS NOT A LINTER RULE. `oxlint` (frontend/.oxlintrc.json) lints the
JS/TS: hooks, unused code, correctness. It cannot help here — to a JS linter a `className` is an
opaque string literal, so `text-[11px]` is invisible to it. That blind spot is exactly how 102
hand-picked sub-12px sizes accumulated in a codebase nobody was linting at all. So the class
conventions get a check of their own, in the same gate.

DELIBERATELY ONE RULE, AND NO ALLOWLIST. A check with exemptions is a check people argue with;
this one has a single, mechanical, zero-false-positive rule, which is why it can be an error
rather than a warning. Everything else in docs/ui-conventions.md — whether a sentence got the
prose size, whether a fill token was used as text — is review's job, not this file's.

Run it alone with `uv run python scripts/check_ui_classes.py`; `make lint` and `make check` both
call it. Ports to a sibling app by changing ROOTS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the conventions apply. The Chrome extension (`extension/`) is a second SPA with arbitrary
#: sizes of its own and is deliberately NOT here yet — see docs/ui-conventions.md, "Not yet swept".
#: Adding it is a sweep, not a config change.
ROOTS = [REPO_ROOT / "frontend" / "src"]

SUFFIXES = {".ts", ".tsx"}

#: Skipped by name, with a reason and an expiry condition — never "this file is special". It is
#: unreferenced (no import anywhere in the tree), so whether it is revived or deleted is a separate
#: decision from what size its text should be. Deleting the file deletes this entry.
SKIP = {REPO_ROOT / "frontend" / "src" / "components" / "ToolsControl.tsx"}

#: An absolute font size written by hand: `text-[11px]`, `text-[0.8rem]`, `text-[13px]` — including
#: inside a variant, e.g. `[&_[cmdk-group-heading]]:text-[11px]`. Relative units are deliberately
#: NOT matched: `text-[0.85em]` is a fixed ratio TO the scale (code inside prose), which composes
#: with it rather than opting out of it.
ARBITRARY_TYPE_SIZE = re.compile(r"text-\[\d*\.?\d+(?:px|rem|pt)\]")

FIX = (
    "no hand-written type sizes. Use text-xs (chips, badges, counts, metadata) or "
    "text-sm (any sentence), or text-base for chat body. See docs/ui-conventions.md."
)


def source_files() -> list[Path]:
    """Every checkable source file under ROOTS, in a stable order.

    Returns:
        The .ts/.tsx files to scan, minus the documented skips.
    """
    found: list[Path] = []
    for root in ROOTS:
        found.extend(p for p in root.rglob("*") if p.suffix in SUFFIXES and p not in SKIP)
    return sorted(found)


def violations(path: Path) -> list[tuple[int, str]]:
    """Find hand-written type sizes in one file.

    Args:
        path: The source file to scan.

    Returns:
        `(line number, offending class)` pairs, in source order.
    """
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        hits.extend((lineno, m.group(0)) for m in ARBITRARY_TYPE_SIZE.finditer(line))
    return hits


def main() -> int:
    """Report every violation, then fail if there were any.

    Returns:
        A process exit code: 0 when clean, 1 when anything was found.
    """
    files = source_files()
    total = 0
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        for lineno, cls in violations(path):
            total += 1
            print(f"{rel}:{lineno}: {cls} — {FIX}")
    if total:
        plural = "" if total == 1 else "es"
        print(f"\n{total} hand-written type size{plural} in {len(files)} files.", file=sys.stderr)
        return 1
    print(f"ui classes ok ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
