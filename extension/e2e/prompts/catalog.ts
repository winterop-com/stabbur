// The verified prompt catalog. Each prompt names a site, a task category, the
// context mode (whole page text vs a selection), the exact prompt text a user
// pastes, and a mechanical check. Prompts are written model-friendly for a 12B:
// explicit output format + "Output ONLY ..." guards.

import type { Check } from "./checks";

export type Category =
  | "extraction"
  | "summarization"
  | "explanation"
  | "selection-qa"
  | "transformation"
  | "reasoning";

export type Mode = "page-text" | "selection";

export interface Prompt {
  id: string;
  site: string;
  category: Category;
  mode: Mode;
  /** Short human title for the docs. */
  title: string;
  /** The exact prompt text the user pastes into the panel. */
  prompt: string;
  check: Check;
  /** Set true for prompts that stay unreliable on 12B after rewording. */
  unreliable?: boolean;
  /** Note about failure mode / reliability, shown in the docs. */
  note?: string;
  /** Per-prompt generation budget override (default 2500). Reasoning models can spend the
   *  whole default budget thinking on strict-extraction prompts and emit nothing. */
  maxTokens?: number;
}

export const CATALOG: Prompt[] = [
  // ---- extraction (whole page) ----
  {
    id: "hn-json-top10",
    site: "hn",
    category: "extraction",
    mode: "page-text",
    title: "Top 10 HN stories as JSON",
    prompt:
      "The page text above is the Hacker News front page. Extract the first 10 stories. Output a strict JSON array of exactly 10 objects, each with keys: rank (number), title (string), points (number), comments (number). If points or comments are missing use 0. Output ONLY the JSON array with no prose and no code fence.",
    check: { kind: "json-array", minItems: 8, itemKeys: ["rank", "title", "points"] },
  },
  {
    id: "hn-markdown-table",
    site: "hn",
    category: "extraction",
    mode: "page-text",
    title: "Top 10 HN stories as a Markdown table",
    prompt:
      "The page text above is the Hacker News front page. Extract the first 10 stories as a Markdown table with the columns: Rank | Title | Points | Comments. Include the header row and the separator row. Output ONLY the table.",
    check: { kind: "markdown-table", minRows: 8, minCols: 4 },
    unreliable: true,
    note:
      "Borderline at 12B: usually emits the full 10-row table, but sometimes stops after 7 rows even at temperature 0.1 (observed 10/10 and 7/10 across identical runs). The JSON variant (hn-json-top10) is the reliable form; use this one when you want copy-pasteable Markdown and do not mind an occasional short table.",
  },
  {
    id: "hn-csv",
    site: "hn",
    category: "extraction",
    mode: "page-text",
    title: "Top 10 HN stories as CSV",
    prompt:
      "The page text above is the Hacker News front page. Extract the first 10 stories as CSV. First line must be the header: rank,title,points,comments. Then one line per story. Wrap any title containing a comma in double quotes. Do not deliberate - start writing the CSV immediately. Output ONLY the CSV.",
    check: { kind: "csv", minRows: 8, minCols: 4 },
    maxTokens: 6000,
  },
  {
    id: "github-metadata-json",
    site: "github",
    category: "extraction",
    mode: "page-text",
    title: "GitHub repo metadata as JSON",
    prompt:
      "The page text above is a GitHub repository page. Output a strict JSON object with keys: name (string, owner/repo), description (string), primary_language (string, your best guess). If a value is unknown use an empty string. Output ONLY the JSON object.",
    check: { kind: "json-object", keys: ["name", "description", "primary_language"] },
  },
  {
    id: "mdn-callback-args",
    site: "mdn",
    category: "extraction",
    mode: "page-text",
    title: "MDN callback arguments extraction",
    prompt:
      "The page text above is the MDN reference for Array.prototype.map(). List the arguments the callback function is called with, one per line, each followed by a dash and a one-phrase description from the page. Do not deliberate - answer immediately. Output ONLY the list.",
    check: { kind: "contains", all: ["element", "index", "array"] },
    note:
      "Replaces a signature-extraction prompt: code blocks (the literal map(callbackFn) syntax lines) do not survive page-text capture on MDN, so signature extraction is unanswerable from the attached context - the model deliberates endlessly over missing data. Prose-rendered facts extract reliably.",
  },
  {
    id: "mdn-params-json",
    site: "mdn",
    category: "extraction",
    mode: "page-text",
    title: "MDN parameters as JSON",
    prompt:
      "The page text above is the MDN reference for Array.prototype.map(). List its parameters as a strict JSON array of objects with keys: name (string), description (string, one short sentence). Output ONLY the JSON array.",
    check: { kind: "json-array", minItems: 1, itemKeys: ["name", "description"] },
  },
  {
    id: "pydocs-funcs-json",
    site: "pydocs",
    category: "extraction",
    mode: "page-text",
    title: "Python json module functions as JSON",
    prompt:
      "The page text above is the Python documentation for the json module. List four top-level functions it documents as a strict JSON array of strings (e.g. \"json.dumps\"). Output ONLY the JSON array.",
    check: { kind: "json-string-array", minItems: 4 },
  },
  {
    id: "dhis2-url-extract",
    site: "dhis2",
    category: "extraction",
    mode: "page-text",
    title: "DHIS2 instance URL extraction",
    prompt:
      "Using the page context above, output ONLY the base URL of this DHIS2 instance and nothing else.",
    check: { kind: "regex", pattern: "play\\.im\\.dhis2\\.org", flags: "i" },
  },

  // ---- summarization ----
  {
    id: "hn-themes",
    site: "hn",
    category: "summarization",
    mode: "page-text",
    title: "HN front-page themes",
    prompt:
      "The page text above is the Hacker News front page. Summarize the main themes across the stories in exactly 3 bullet points. Each bullet must be a single sentence. Output ONLY the 3 bullets.",
    check: { kind: "bullets", min: 3, max: 5 },
  },
  {
    id: "wiki-summary-bullets",
    site: "wikipedia",
    category: "summarization",
    mode: "page-text",
    title: "SQLite article summary in 5 bullets",
    prompt:
      "The page text above is a Wikipedia article about SQLite. Summarize it in exactly 5 bullet points. Each bullet is one sentence. Output ONLY the 5 bullets.",
    check: { kind: "bullets", min: 4, max: 7 },
  },
  {
    id: "arxiv-onesentence",
    site: "arxiv",
    category: "summarization",
    mode: "page-text",
    title: "arXiv abstract in one sentence",
    prompt:
      "The page text above is an arXiv abstract page. Summarize the paper's abstract in ONE sentence of at most 40 words. Output ONLY that sentence.",
    check: { kind: "length", maxWords: 60, maxSentences: 2 },
  },
  {
    id: "pydocs-summary",
    site: "pydocs",
    category: "summarization",
    mode: "page-text",
    title: "Python json module summary",
    prompt:
      "The page text above is the Python documentation for the json module. In exactly 3 bullet points, summarize what this module is for. Each bullet is one sentence. Output ONLY the 3 bullets.",
    check: { kind: "bullets", min: 3, max: 5 },
  },
  {
    id: "so-summary",
    site: "stackoverflow",
    category: "summarization",
    mode: "page-text",
    title: "Stack Overflow question + answer summary",
    prompt:
      "The page text above is a Stack Overflow page. In 2 to 4 bullet points, summarize the question and the key point of the top answer. Output ONLY the bullets.",
    check: { kind: "bullets", min: 2, max: 6 },
  },
  {
    id: "github-summary",
    site: "github",
    category: "summarization",
    mode: "page-text",
    title: "GitHub repo two-bullet summary",
    prompt:
      "The page text above is a GitHub repository page. In exactly 2 bullet points, state (1) what the project is and (2) its primary programming language. Output ONLY the 2 bullets.",
    check: { kind: "bullets", min: 2, max: 3 },
  },

  // ---- explanation (selection) ----
  {
    id: "wiki-explain-selection",
    site: "wikipedia",
    category: "explanation",
    mode: "selection",
    title: "Explain a selected SQLite paragraph simply",
    prompt:
      "Explain the selected text simply, as if to someone new to databases, in 2 to 3 sentences. Output ONLY the explanation.",
    check: { kind: "length", minWords: 20, maxWords: 130 },
  },
  {
    id: "arxiv-explain-selection",
    site: "arxiv",
    category: "explanation",
    mode: "selection",
    title: "Explain the selected abstract for a non-expert",
    prompt:
      "Explain the selected text in plain language for a non-expert, in 3 sentences. Avoid jargon. Output ONLY the explanation.",
    check: { kind: "length", minWords: 25, maxWords: 150 },
  },

  // ---- selection-grounded Q&A (incl. the honesty case) ----
  {
    id: "wiki-selection-qa",
    site: "wikipedia",
    category: "selection-qa",
    mode: "selection",
    title: "Answer strictly from the SQLite selection",
    prompt:
      "Using ONLY the selected text, answer in one sentence: what kind of database engine is SQLite? If the answer is not in the selected text, reply with exactly: not in the selection",
    check: { kind: "regex", pattern: "serverless|self-contained|embedded|library|C language|written in C|SQL", flags: "i" },
  },
  {
    id: "wiki-selection-honesty",
    site: "wikipedia",
    category: "selection-qa",
    mode: "selection",
    title: "Honesty: absent fact in the SQLite selection",
    prompt:
      "Using ONLY the selected text, answer in one sentence: who is the current CEO of Google? If the answer is not in the selected text, reply with exactly: not in the selection",
    check: { kind: "honesty" },
  },
  {
    id: "arxiv-selection-qa",
    site: "arxiv",
    category: "selection-qa",
    mode: "selection",
    title: "Answer strictly from the arXiv abstract",
    prompt:
      "Using ONLY the selected text, answer in one sentence: what network architecture does the paper propose? If the answer is not in the selected text, reply with exactly: not in the selection",
    check: { kind: "contains", all: ["transformer"] },
  },
  {
    id: "pydocs-selection-honesty",
    site: "pydocs",
    category: "selection-qa",
    mode: "selection",
    title: "Honesty: absent fact in the json-module selection",
    prompt:
      "Using ONLY the selected text, answer in one sentence: what is the airspeed velocity of an unladen swallow? If the answer is not in the selected text, reply with exactly: not in the selection",
    check: { kind: "honesty" },
  },

  // ---- transformation (selection) ----
  {
    id: "wiki-child-rewrite",
    site: "wikipedia",
    category: "transformation",
    mode: "selection",
    title: "Rewrite the selection for a 10-year-old",
    prompt:
      "Rewrite the selected text so a 10-year-old can understand it. Keep it under 80 words. Output ONLY the rewritten text.",
    check: { kind: "length", minWords: 10, maxWords: 110 },
  },
  {
    id: "wiki-translate-no",
    site: "wikipedia",
    category: "transformation",
    mode: "selection",
    title: "Translate the selection to Norwegian",
    prompt:
      "Translate the selected text into Norwegian (bokmål). Output ONLY the Norwegian translation.",
    check: { kind: "regex", pattern: "\\b(og|er|en|som|det|til|av|for|med|ikke)\\b", flags: "i" },
  },
  {
    id: "arxiv-extract-numbers",
    site: "arxiv",
    category: "transformation",
    mode: "selection",
    title: "Extract numbers + meaning from the abstract",
    prompt:
      "From the selected text, extract every number and what it refers to, as a list with one item per line in the form: number - meaning. If there are no numbers, reply with exactly: none. Do not deliberate - answer immediately. Output ONLY the list.",
    check: { kind: "regex", pattern: "\\d.*-|-\\s*\\d|none", flags: "i" },
    maxTokens: 6000,
  },
  {
    id: "wiki-extract-dates",
    site: "wikipedia",
    category: "transformation",
    mode: "page-text",
    title: "Extract years mentioned on the SQLite page",
    prompt:
      "From the page text above, list every distinct year (a four-digit number like 2004) that is mentioned, as a plain list with one year per line, sorted ascending. Output ONLY the list.",
    check: { kind: "regex", pattern: "(19|20)\\d\\d", flags: "" },
  },

  // ---- tool-free reasoning on the context ----
  {
    id: "hn-most-points",
    site: "hn",
    category: "reasoning",
    mode: "page-text",
    title: "Which top-5 HN story has the most points",
    prompt:
      "The page text above is the Hacker News front page. Of the first 5 stories, which has the most points? Answer with just the story title and its points, in the form: <title> - <points> points.",
    check: { kind: "regex", pattern: "\\d+\\s*points", flags: "i" },
  },
  {
    id: "hn-rank-by-points",
    site: "hn",
    category: "reasoning",
    mode: "page-text",
    title: "Rank top-5 HN stories by points",
    prompt:
      "The page text above is the Hacker News front page. Rank the first 5 stories by points, highest first, as a numbered list where each line is: <rank>. <title> (<points> points). Output ONLY the list.",
    check: { kind: "regex", pattern: "1\\.[\\s\\S]*\\d+\\s*points", flags: "i" },
  },
  {
    id: "hn-points-delta",
    site: "hn",
    category: "reasoning",
    mode: "page-text",
    title: "Points delta between the top two HN stories",
    prompt:
      "The page text above is the Hacker News front page. Take the first two stories. State the points of story 1, the points of story 2, and the difference (story 1 minus story 2). Format: Story 1: X points; Story 2: Y points; Difference: Z.",
    check: { kind: "regex", pattern: "difference:?\\s*-?\\d+", flags: "i" },
  },

  // ---- context-only (DHIS2 landing) ----
  {
    id: "dhis2-context-describe",
    site: "dhis2",
    category: "summarization",
    mode: "page-text",
    title: "Describe the DHIS2 landing page",
    prompt:
      "Using the page context above, in 1 to 2 sentences describe what this page is (name the system and whether it looks like a login page or a signed-in app). Output ONLY the description.",
    check: { kind: "contains", all: ["dhis2"] },
  },
];
