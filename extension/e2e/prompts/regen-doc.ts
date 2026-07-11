// Regenerate the verified-results section of docs/guides/extension-prompts.md from
// results/results.json, so the doc's status table can never drift from a real run.
//
//   bun run e2e/prompts/regen-doc.ts
//
// Rewrites only the block between the RESULTS markers; the prose is hand-written.

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RESULTS = path.join(HERE, "results", "results.json");
const DOC = path.resolve(HERE, "../../../docs/guides/extension-prompts.md");
const START = "<!-- RESULTS:START -->";
const END = "<!-- RESULTS:END -->";

interface Result {
  id: string;
  site: string;
  category: string;
  mode: string;
  title: string;
  pass: boolean;
  checkKind: string;
  checkDetail: string;
  latencyMs: number;
  unreliable: boolean;
  note: string | null;
}
interface Summary {
  model: string;
  label: string;
  finishedAt: string;
  total: number;
  passed: number;
  failed: number;
  byCategory: Record<string, { passed: number; total: number }>;
  results: Result[];
}

function statusCell(r: Result): string {
  if (r.pass) return "verified";
  if (r.unreliable) return "unreliable (12B)";
  return "failing";
}

function render(s: Summary): string {
  const lines: string[] = [];
  lines.push(START);
  lines.push("");
  lines.push(
    `_Auto-generated from a real harness run. Model: \`${s.model}\`. ` +
      `${s.passed}/${s.total} verified. Last run: ${s.finishedAt}._`,
  );
  lines.push("");
  lines.push("**By category**");
  lines.push("");
  lines.push("| Category | Verified |");
  lines.push("| --- | --- |");
  for (const [cat, v] of Object.entries(s.byCategory)) lines.push(`| ${cat} | ${v.passed}/${v.total} |`);
  lines.push("");
  lines.push("**Per prompt**");
  lines.push("");
  lines.push("| Prompt | Site | Category | Mode | Status | Latency | Check |");
  lines.push("| --- | --- | --- | --- | --- | --- | --- |");
  for (const r of s.results) {
    const detail = r.checkDetail.replace(/\|/g, "\\|").slice(0, 80);
    lines.push(
      `| \`${r.id}\` | ${r.site} | ${r.category} | ${r.mode} | ${statusCell(r)} | ${(r.latencyMs / 1000).toFixed(
        1,
      )}s | ${r.checkKind}: ${detail} |`,
    );
  }
  const notes = s.results.filter((r) => r.note);
  if (notes.length) {
    lines.push("");
    lines.push("**Reliability notes**");
    lines.push("");
    for (const r of notes) lines.push(`- \`${r.id}\`: ${r.note}`);
  }
  lines.push("");
  lines.push(END);
  return lines.join("\n");
}

function main(): void {
  const s = JSON.parse(readFileSync(RESULTS, "utf8")) as Summary;
  const doc = readFileSync(DOC, "utf8");
  const startIdx = doc.indexOf(START);
  const endIdx = doc.indexOf(END);
  if (startIdx < 0 || endIdx < 0) throw new Error(`markers ${START} / ${END} not found in ${DOC}`);
  const next = doc.slice(0, startIdx) + render(s) + doc.slice(endIdx + END.length);
  writeFileSync(DOC, next);
  console.log(`[regen-doc] wrote ${s.passed}/${s.total} verified into ${DOC}`);
}

main();
export {};
