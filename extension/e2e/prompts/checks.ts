// Mechanical per-prompt checks. Each returns {pass, detail}; `detail` explains a
// failure (or notes what matched) so the results file is self-describing.

export type Check =
  | { kind: "json-array"; minItems: number; itemKeys?: string[] }
  | { kind: "json-object"; keys: string[] }
  | { kind: "json-string-array"; minItems: number }
  | { kind: "markdown-table"; minRows: number; minCols: number }
  | { kind: "csv"; minRows: number; minCols: number }
  | { kind: "contains"; all: string[] } // case-insensitive substrings, all required
  | { kind: "regex"; pattern: string; flags?: string }
  | { kind: "honesty" } // must contain "not in the selection"
  | { kind: "bullets"; min: number; max: number }
  | { kind: "length"; minWords?: number; maxWords?: number; maxSentences?: number };

export interface CheckResult {
  pass: boolean;
  detail: string;
}

/** Strip a leading/trailing ``` or ```json fence if the model wrapped its output. */
function stripFence(text: string): string {
  const t = text.trim();
  const fence = /^```[a-zA-Z]*\s*\n([\s\S]*?)\n```$/m.exec(t);
  return fence ? fence[1].trim() : t;
}

/** Find the first balanced JSON array or object substring in the text. */
function extractJson(text: string): string | null {
  const t = stripFence(text);
  const start = t.search(/[[{]/);
  if (start < 0) return null;
  const open = t[start];
  const close = open === "[" ? "]" : "}";
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = start; i < t.length; i++) {
    const c = t[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === open) depth++;
    else if (c === close) {
      depth--;
      if (depth === 0) return t.slice(start, i + 1);
    }
  }
  return null;
}

function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

function sentenceCount(text: string): number {
  return text.split(/[.!?]+(?:\s|$)/).filter((s) => s.trim().length > 0).length;
}

function countBullets(text: string): number {
  const lines = text.split("\n").map((l) => l.trim());
  return lines.filter((l) => /^([-*•]|\d+[.)])\s+\S/.test(l)).length;
}

export function runCheck(check: Check, output: string): CheckResult {
  const lc = output.toLowerCase();
  switch (check.kind) {
    case "json-array": {
      const js = extractJson(output);
      if (!js) return { pass: false, detail: "no JSON array found" };
      let val: unknown;
      try {
        val = JSON.parse(js);
      } catch (e) {
        return { pass: false, detail: `JSON.parse failed: ${String(e)}` };
      }
      if (!Array.isArray(val)) return { pass: false, detail: "parsed value is not an array" };
      if (val.length < check.minItems)
        return { pass: false, detail: `only ${val.length} items (need >= ${check.minItems})` };
      if (check.itemKeys) {
        for (const key of check.itemKeys) {
          const ok = val.every((it) => it && typeof it === "object" && key in (it as object));
          if (!ok) return { pass: false, detail: `not all items have key "${key}"` };
        }
      }
      return { pass: true, detail: `array of ${val.length} items` };
    }
    case "json-object": {
      const js = extractJson(output);
      if (!js) return { pass: false, detail: "no JSON object found" };
      let val: unknown;
      try {
        val = JSON.parse(js);
      } catch (e) {
        return { pass: false, detail: `JSON.parse failed: ${String(e)}` };
      }
      if (typeof val !== "object" || val === null || Array.isArray(val))
        return { pass: false, detail: "parsed value is not an object" };
      for (const key of check.keys) {
        if (!(key in (val as object))) return { pass: false, detail: `missing key "${key}"` };
      }
      return { pass: true, detail: `object with keys ${check.keys.join(",")}` };
    }
    case "json-string-array": {
      const js = extractJson(output);
      if (!js) return { pass: false, detail: "no JSON array found" };
      let val: unknown;
      try {
        val = JSON.parse(js);
      } catch (e) {
        return { pass: false, detail: `JSON.parse failed: ${String(e)}` };
      }
      if (!Array.isArray(val)) return { pass: false, detail: "not an array" };
      if (val.length < check.minItems)
        return { pass: false, detail: `only ${val.length} items (need >= ${check.minItems})` };
      if (!val.every((x) => typeof x === "string"))
        return { pass: false, detail: "array contains non-strings" };
      return { pass: true, detail: `array of ${val.length} strings` };
    }
    case "markdown-table": {
      const lines = stripFence(output)
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l.startsWith("|"));
      const sepIdx = lines.findIndex((l) => /^\|?[\s:|-]*-[\s:|-]*\|?$/.test(l) && l.includes("-"));
      if (sepIdx < 1) return { pass: false, detail: "no header + separator row found" };
      const cols = lines[sepIdx].split("|").filter((c) => c.trim().length > 0).length;
      if (cols < check.minCols) return { pass: false, detail: `only ${cols} columns (need >= ${check.minCols})` };
      const dataRows = lines.length - (sepIdx + 1);
      if (dataRows < check.minRows) return { pass: false, detail: `only ${dataRows} data rows (need >= ${check.minRows})` };
      return { pass: true, detail: `${dataRows} rows x ${cols} cols` };
    }
    case "csv": {
      const lines = stripFence(output)
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l.length > 0 && l.includes(","));
      if (lines.length < check.minRows)
        return { pass: false, detail: `only ${lines.length} csv lines (need >= ${check.minRows})` };
      const minCols = check.minCols - 1; // commas = cols - 1
      const bad = lines.find((l) => l.split(",").length - 1 < minCols);
      if (bad) return { pass: false, detail: `a row has < ${check.minCols} columns: ${bad.slice(0, 60)}` };
      return { pass: true, detail: `${lines.length} csv rows` };
    }
    case "contains": {
      const missing = check.all.filter((s) => !lc.includes(s.toLowerCase()));
      if (missing.length) return { pass: false, detail: `missing: ${missing.join(", ")}` };
      return { pass: true, detail: `contains all of: ${check.all.join(", ")}` };
    }
    case "regex": {
      const re = new RegExp(check.pattern, check.flags ?? "");
      return re.test(output)
        ? { pass: true, detail: `matched /${check.pattern}/${check.flags ?? ""}` }
        : { pass: false, detail: `no match for /${check.pattern}/${check.flags ?? ""}` };
    }
    case "honesty": {
      return lc.includes("not in the selection")
        ? { pass: true, detail: "correctly refused (not in the selection)" }
        : { pass: false, detail: "did not refuse with 'not in the selection'" };
    }
    case "bullets": {
      const n = countBullets(output);
      if (n < check.min || n > check.max)
        return { pass: false, detail: `${n} bullets (want ${check.min}-${check.max})` };
      return { pass: true, detail: `${n} bullets` };
    }
    case "length": {
      const w = wordCount(output);
      if (check.minWords != null && w < check.minWords)
        return { pass: false, detail: `${w} words (need >= ${check.minWords})` };
      if (check.maxWords != null && w > check.maxWords)
        return { pass: false, detail: `${w} words (need <= ${check.maxWords})` };
      if (check.maxSentences != null) {
        const s = sentenceCount(output);
        if (s > check.maxSentences) return { pass: false, detail: `${s} sentences (need <= ${check.maxSentences})` };
      }
      return { pass: true, detail: `${w} words` };
    }
  }
}
