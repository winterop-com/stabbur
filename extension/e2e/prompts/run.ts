// Prompt-catalog verification harness (orchestrator).
//
//   bun run e2e/prompts/run.ts                 # capture + serve + replay + assert
//   bun run e2e/prompts/run.ts --no-capture    # reuse cached captures
//   bun run e2e/prompts/run.ts --only hn-csv,wiki-summary-bullets
//   KODO_PROMPT_BASE_URL=http://127.0.0.1:4611 bun run e2e/prompts/run.ts --no-capture
//
// Writes results/<out>.json + results/outputs/<id>.md and prints a summary. The
// full run gates at >= 25 verified (skipped when --only narrows the set).

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CATALOG, type Category, type Prompt } from "./catalog";
import { runCheck } from "./checks";
import { buildContextBlock, buildUserContent } from "./context";
import { captureAll, loadCaptures, type Capture } from "./capture";
import { replay, waitForIdle } from "./replay";
import { startPromptServer, waitForReady, DEFAULT_MODEL, DEFAULT_LIBRARY_ROOT } from "./promptServer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RESULTS_DIR = path.join(HERE, "results");
const OUTPUTS_DIR = path.join(RESULTS_DIR, "outputs");
const GATE = 25;

interface Args {
  noCapture: boolean;
  only: string[] | null;
  model: string;
  library: string;
  out: string;
  label: string;
}

function parseArgs(argv: string[]): Args {
  const a: Args = {
    noCapture: false,
    only: null,
    model: DEFAULT_MODEL,
    library: DEFAULT_LIBRARY_ROOT,
    out: "results.json",
    label: "gemma-4-12B",
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--no-capture") a.noCapture = true;
    else if (arg === "--only") a.only = (argv[++i] ?? "").split(",").map((s) => s.trim()).filter(Boolean);
    else if (arg === "--model") a.model = argv[++i] ?? a.model;
    else if (arg === "--library") a.library = argv[++i] ?? a.library;
    else if (arg === "--out") a.out = argv[++i] ?? a.out;
    else if (arg === "--label") a.label = argv[++i] ?? a.label;
  }
  return a;
}

interface PromptResult {
  id: string;
  site: string;
  category: Category;
  mode: string;
  title: string;
  prompt: string;
  checkKind: string;
  pass: boolean;
  checkDetail: string;
  latencyMs: number;
  error: string | null;
  unreliable: boolean;
  note: string | null;
  outputFile: string;
}

function capMap(caps: Capture[]): Map<string, Capture> {
  return new Map(caps.map((c) => [c.key, c]));
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  mkdirSync(OUTPUTS_DIR, { recursive: true });
  const capturePath = path.join(RESULTS_DIR, "captures.json");

  const captures = args.noCapture ? loadCaptures(capturePath) : await captureAll(capturePath);
  const byKey = capMap(captures);

  const prompts: Prompt[] = args.only ? CATALOG.filter((p) => args.only!.includes(p.id)) : CATALOG;
  if (prompts.length === 0) throw new Error("no prompts selected");

  let server = startPromptServer({ model: args.model, libraryRoot: args.library });
  const external = !!process.env.KODO_PROMPT_BASE_URL;
  const startedAt = new Date().toISOString();
  const results: PromptResult[] = [];
  try {
    console.log(`[run] waiting for ${server.baseUrl} (model ${args.model}) ...`);
    await waitForReady(server.baseUrl);
    console.log(`[run] ready; replaying ${prompts.length} prompts`);

    for (const p of prompts) {
      const cap = byKey.get(p.site);
      if (!cap) {
        console.log(`[run] ${p.id}: SKIP (no capture for site ${p.site})`);
        continue;
      }
      const userContent = buildUserContent(p.mode, cap, p.prompt);
      // A raised token budget needs a raised wall clock (6000 tokens can exceed 180s).
      const timeoutMs = p.maxTokens && p.maxTokens > 2500 ? 360_000 : undefined;
      const r = await replay(server.baseUrl, userContent, timeoutMs, p.maxTokens);
      const check = r.error ? { pass: false, detail: `replay error: ${r.error}` } : runCheck(p.check, r.output);
      const outputFile = path.join("outputs", `${args.label}__${p.id}.md`);
      writeFileSync(
        path.join(RESULTS_DIR, outputFile),
        `# ${p.id} (${p.category}/${p.mode})\n\n` +
          `**Prompt**\n\n\`\`\`\n${p.prompt}\n\`\`\`\n\n` +
          `**Context block (sent, truncated to 1500 chars)**\n\n\`\`\`\n${buildContextBlock(p.mode, cap).slice(0, 1500)}\n\`\`\`\n\n` +
          `**Check**: ${p.check.kind} -> ${check.pass ? "PASS" : "FAIL"} (${check.detail}); latency ${r.latencyMs}ms\n\n` +
          `**Model output**\n\n\`\`\`\n${r.output}\n\`\`\`\n`,
      );
      results.push({
        id: p.id,
        site: p.site,
        category: p.category,
        mode: p.mode,
        title: p.title,
        prompt: p.prompt,
        checkKind: p.check.kind,
        pass: check.pass,
        checkDetail: check.detail,
        latencyMs: r.latencyMs,
        error: r.error,
        unreliable: !!p.unreliable,
        note: p.note ?? null,
        outputFile,
      });
      console.log(`[run] ${p.id}: ${check.pass ? "PASS" : "FAIL"} (${check.detail}) ${r.latencyMs}ms`);
      // A timed-out generation may still be running server-side: Bun's fetch does
      // not reliably close the socket when aborted while waiting for response
      // headers (a request queued behind a busy runtime), so the zombie request
      // stays in llama-server's single-slot queue and every later prompt times out
      // behind it. Probing (waitForIdle) only ADDS queued zombies. The reliable
      // reset is a runtime restart: cheap (warm model reload) and cascade-proof.
      if (r.error?.includes("timeout")) {
        if (external) {
          console.log(`[run] ${p.id} timed out; external server (KODO_PROMPT_BASE_URL) - probing until idle ...`);
          const idle = await waitForIdle(server.baseUrl);
          if (!idle) console.log("[run] WARNING: runtime still busy after 300s; subsequent results may be unreliable");
        } else {
          console.log(`[run] ${p.id} timed out; restarting the runtime so the wedged generation cannot cascade ...`);
          await server.stop();
          server = startPromptServer({ model: args.model, libraryRoot: args.library });
          await waitForReady(server.baseUrl);
          console.log("[run] runtime restarted; continuing");
        }
      }
    }
  } catch (e) {
    if (server.logPath) console.log(`[run] serve log tail:\n${server.tailLog(40)}`);
    throw e;
  } finally {
    await server.stop();
  }

  const passed = results.filter((r) => r.pass).length;
  const byCategory: Record<string, { passed: number; total: number }> = {};
  for (const r of results) {
    byCategory[r.category] ??= { passed: 0, total: 0 };
    byCategory[r.category].total++;
    if (r.pass) byCategory[r.category].passed++;
  }
  const summary = {
    model: args.model,
    label: args.label,
    baseUrl: server.baseUrl,
    startedAt,
    finishedAt: new Date().toISOString(),
    total: results.length,
    passed,
    failed: results.length - passed,
    byCategory,
    captures: captures.map((c) => ({
      key: c.key,
      url: c.url,
      title: c.title,
      pageTextLen: c.pageText.length,
      selectionLen: c.selection.length,
      note: c.note ?? null,
    })),
    results,
  };
  writeFileSync(path.join(RESULTS_DIR, args.out), JSON.stringify(summary, null, 2));

  console.log(`\n[run] ${passed}/${results.length} passed`);
  for (const [cat, v] of Object.entries(byCategory)) console.log(`  ${cat}: ${v.passed}/${v.total}`);
  console.log(`[run] wrote ${path.join(RESULTS_DIR, args.out)}`);

  if (!args.only && passed < GATE) {
    console.error(`[run] GATE FAILED: ${passed} verified < ${GATE}`);
    process.exit(1);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

export {};
