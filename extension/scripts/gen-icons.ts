// Generate the extension toolbar/store icons for both flavors, deterministically.
//
//   bun run scripts/gen-icons.ts        (from extension/)
//
// Renders a flat rounded-square "k" glyph to a canvas at each manifest size and writes PNGs to
// public/icon/{generic,dhis2}-{16,32,48,128}.png. The generic mark is a dark slate square; the
// dhis2 mark adds a small blue corner accent. WXT copies public/ verbatim, and wxt.config.ts
// wires the per-flavor set into manifest.icons. Re-run only when the mark changes — the PNGs are
// committed alongside the source.

import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const EXT_ROOT = path.resolve(HERE, "..");
const OUT_DIR = path.join(EXT_ROOT, "public", "icon");
const SIZES = [16, 32, 48, 128] as const;
const FLAVORS = ["generic", "dhis2"] as const;

// Draw one icon onto a fresh canvas and return it as a PNG data URL. Runs in the page context.
function draw(size: number, flavor: string): string {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const r = Math.max(2, Math.round(size * 0.22));
  ctx.fillStyle = "#0b1220"; // near-black slate
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.arcTo(size, 0, size, size, r);
  ctx.arcTo(size, size, 0, size, r);
  ctx.arcTo(0, size, 0, 0, r);
  ctx.arcTo(0, 0, size, 0, r);
  ctx.closePath();
  ctx.fill();
  if (flavor === "dhis2") {
    const a = Math.round(size * 0.42);
    ctx.fillStyle = "#2c6693"; // DHIS2 blue corner accent
    ctx.beginPath();
    ctx.moveTo(size - a, 0);
    ctx.lineTo(size, 0);
    ctx.lineTo(size, a);
    ctx.closePath();
    ctx.fill();
  }
  ctx.fillStyle = "#f8fafc";
  ctx.font = `700 ${Math.round(size * 0.68)}px -apple-system, "Segoe UI", Roboto, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("k", size / 2, size / 2 + Math.round(size * 0.04));
  return canvas.toDataURL("image/png");
}

async function main(): Promise<void> {
  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({ channel: "chromium" });
  try {
    const page = await browser.newPage();
    await page.setContent("<!doctype html><body></body>");
    await page.addScriptTag({ content: `window.__draw = ${draw.toString()};` });
    for (const flavor of FLAVORS) {
      for (const size of SIZES) {
        const dataUrl = await page.evaluate(
          ([s, f]) => (window as unknown as { __draw: (a: number, b: string) => string }).__draw(s, f),
          [size, flavor] as [number, string],
        );
        const b64 = dataUrl.split(",")[1];
        const file = path.join(OUT_DIR, `${flavor}-${size}.png`);
        writeFileSync(file, Buffer.from(b64, "base64"));
        console.log(`  wrote ${path.relative(EXT_ROOT, file)}`);
      }
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
