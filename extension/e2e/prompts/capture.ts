// Capture step: plain Playwright (no extension) visits each site once and records
// {url, title, pageText (whitespace-collapsed, <= 8000 chars), selection}. Results
// cache to captures.json so a rerun with --no-capture replays without re-fetching.
//
// The page-text collapse MIRRORS extension/lib/pageContext.ts::collect so the
// captured text matches what the extension would send.

import { chromium, type Browser, type Page } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { SITES, type Site } from "./sites";

export interface Capture {
  key: string;
  url: string;
  title: string;
  pageText: string;
  selection: string;
  /** Non-fatal note (blocked/short/selection-missing) surfaced in the report. */
  note?: string;
}

const PAGE_TEXT_LIMIT = 8000;
const SELECTION_LIMIT = 2000;

async function captureSite(page: Page, site: Site): Promise<Capture> {
  const notes: string[] = [];
  await page.goto(site.url, { waitUntil: "domcontentloaded", timeout: 45_000 });
  // Give client-rendered pages (GitHub, MDN) a beat to paint.
  await page.waitForTimeout(2500);

  for (const sel of site.consentSelectors ?? []) {
    try {
      const btn = page.locator(sel).first();
      if (await btn.isVisible({ timeout: 1000 })) await btn.click({ timeout: 2000 });
    } catch {
      /* best-effort consent dismissal */
    }
  }

  const data = await page.evaluate(
    ({ textLimit, selLimit, selectors }) => {
      const raw = document.body?.innerText ?? "";
      const pageText = raw
        .replace(/[^\S\n]+/g, " ")
        .replace(/\s*\n\s*/g, "\n")
        .trim()
        .slice(0, textLimit);
      let selection = "";
      for (const s of selectors) {
        const els = Array.from(document.querySelectorAll(s)) as HTMLElement[];
        const hit = els.find((el) => (el.innerText ?? "").trim().length >= 60);
        if (hit) {
          selection = hit.innerText.replace(/[^\S\n]+/g, " ").replace(/\s*\n\s*/g, "\n").trim().slice(0, selLimit);
          break;
        }
      }
      return { title: document.title, url: location.href, pageText, selection };
    },
    { textLimit: PAGE_TEXT_LIMIT, selLimit: SELECTION_LIMIT, selectors: site.selectionSelectors },
  );

  if (data.pageText.length < 200) notes.push(`short page text (${data.pageText.length} chars) - site may block headless`);
  if (!data.selection) notes.push("no selection captured");

  return {
    key: site.key,
    url: data.url,
    title: data.title,
    pageText: data.pageText,
    selection: data.selection,
    note: notes.length ? notes.join("; ") : undefined,
  };
}

/** Capture all sites and write the cache. */
export async function captureAll(cachePath: string): Promise<Capture[]> {
  const browser: Browser = await chromium.launch({ headless: true });
  const captures: Capture[] = [];
  try {
    const context = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      locale: "en-US",
    });
    const page = await context.newPage();
    for (const site of SITES) {
      try {
        const cap = await captureSite(page, site);
        captures.push(cap);
        console.log(`[capture] ${site.key}: ${cap.pageText.length} chars, selection ${cap.selection.length} chars${cap.note ? ` (${cap.note})` : ""}`);
      } catch (e) {
        captures.push({ key: site.key, url: site.url, title: "", pageText: "", selection: "", note: `capture failed: ${String(e)}` });
        console.log(`[capture] ${site.key}: FAILED ${String(e)}`);
      }
    }
  } finally {
    await browser.close();
  }
  writeFileSync(cachePath, JSON.stringify(captures, null, 2));
  return captures;
}

/** Load cached captures (for --no-capture reruns). */
export function loadCaptures(cachePath: string): Capture[] {
  if (!existsSync(cachePath)) throw new Error(`no capture cache at ${cachePath}; run without --no-capture first`);
  return JSON.parse(readFileSync(cachePath, "utf8")) as Capture[];
}
