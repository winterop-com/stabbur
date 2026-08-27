# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow>=11"]
# ///
# Inline PEP 723 metadata, so `uv run` builds this script its own environment. Pillow is needed
# once per brand change and by nothing else in the app; putting it in the project's dev group
# would make every contributor's sync carry it.
"""Generate the Chrome extension's toolbar/store icons from the stabbur emblem.

    uv run scripts/gen_extension_icons.py

Writes ``extension/public/icon/{generic,dhis2}-{16,32,48,128}.png``. WXT copies ``public/``
verbatim and ``extension/wxt.config.ts`` wires the per-flavor set into ``manifest.icons``, so the
PNGs are committed and this only needs re-running when the emblem or the flavor mark changes.

WHY A SCRIPT AND NOT EIGHT LOOSE FILES. The icons are a DERIVED ARTIFACT of ``docs/assets/logo.png``
- eight of them, across two flavors, each needing the same square-pad-then-Lanczos treatment. The
favicons under ``frontend/public/`` were derived by hand and are therefore unreproducible; when the
emblem next changes, nobody will remember how they were made. This file is that memory.

WHY THE EMBLEM IS SQUARE-PADDED FIRST. The source is 512px RGBA with a 510x501 alpha bounding box,
so the badge is not quite square. Resizing the raw crop to a square target would squash the circle
into an ellipse - the emblem is only recognisable at small sizes because it is round.

WHY PILLOW RATHER THAN THE HEADLESS-CHROMIUM CANVAS THIS REPLACES. The previous generator drew a
flat glyph, for which a canvas was fine. Downscaling an illustration 32x is a resampling problem,
and Chromium's ``drawImage`` - even smoothed, even stepped down by halves - visibly mushes the
emblem's two concentric rings at 48 and 128 where Lanczos keeps them clean.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "docs" / "assets" / "logo.png"
OUT_DIR = REPO_ROOT / "extension" / "public" / "icon"
SIZES = (16, 32, 48, 128)

# The DHIS2 flavor's mark: the same emblem, ringed in DHIS2 blue. It replaces the old blue corner
# triangle, which was drawn for a square glyph and would float in dead space outside a circular
# badge. A ring was chosen over a corner badge because it obscures none of the artwork and because
# it survives the smallest size: at 16px the emblem is a blob either way, and the only thing that
# distinguishes it there is the rim colour.
DHIS2_BLUE = (44, 102, 147, 255)
# Emblem width as a fraction of the icon box for the ringed flavor; the ring fills what is left.
# 0.875 is not a taste call - it puts the ring at exactly 1/16 of the box per edge, so it lands on
# a whole pixel at the 16px size rather than dissolving into a smear of antialiasing.
DHIS2_EMBLEM_SCALE = 0.875
# The ring is drawn oversized and downsampled: a directly-rasterized ellipse edge is stair-stepped,
# and it has to sit flush against the emblem's own antialiased circle.
SUPERSAMPLE = 4


def square_emblem() -> Image.Image:
    """Load the emblem and pad its alpha bounding box out to a centred square.

    Returns:
        The emblem on a transparent square canvas, at source resolution.
    """
    source = Image.open(SOURCE).convert("RGBA")
    cropped = source.crop(source.getbbox())
    width, height = cropped.size
    side = max(width, height)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(cropped, ((side - width) // 2, (side - height) // 2))
    return square


def ringed(emblem: Image.Image, scale: float, color: tuple[int, int, int, int]) -> Image.Image:
    """Shrink the emblem and fill the freed margin with a coloured ring.

    Args:
        emblem: The square emblem, at source resolution.
        scale: Emblem width as a fraction of the icon box; the remainder becomes the ring.
        color: RGBA fill for the ring.

    Returns:
        A new square image the same size as ``emblem``.
    """
    side = emblem.size[0]
    big = side * SUPERSAMPLE
    ring = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    draw.ellipse((0, 0, big - 1, big - 1), fill=color)
    inset = big * (1 - scale) / 2
    draw.ellipse((inset, inset, big - 1 - inset, big - 1 - inset), fill=(0, 0, 0, 0))

    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.alpha_composite(ring.resize((side, side), Image.Resampling.LANCZOS))
    inner = round(side * scale)
    offset = (side - inner) // 2
    out.alpha_composite(emblem.resize((inner, inner), Image.Resampling.LANCZOS), (offset, offset))
    return out


def main() -> int:
    """Write both flavors' icon sets. Returns a process exit code."""
    if not SOURCE.is_file():
        print(f"missing source emblem: {SOURCE}", file=sys.stderr)
        return 1
    emblem = square_emblem()
    flavors = {
        "generic": emblem,
        "dhis2": ringed(emblem, DHIS2_EMBLEM_SCALE, DHIS2_BLUE),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for flavor, art in flavors.items():
        for size in SIZES:
            path = OUT_DIR / f"{flavor}-{size}.png"
            art.resize((size, size), Image.Resampling.LANCZOS).save(path)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
