# Chrome side-panel prompt catalog

A catalog of prompts that work reliably with the kodo Chrome side panel, verified
against a local `gemma-4-12B` model. Each prompt is written to be model-friendly for
a ~12B model: explicit output format and "Output ONLY ..." guards.

The status table near the bottom is **auto-generated from a real verification run**
(see [How verification works](#how-verification-works)) so the docs never drift from
reality.

## How to use

1. Install and open the side panel (see the [extension README](https://github.com/winterop-com/kodo/tree/main/extension)).
2. Point it at a running `kodo serve` (Settings -> kodo base URL).
3. Turn on **Page context** (the pill in the composer, or the Settings checkbox). This
   attaches the page URL, title, and your current text selection to the next message.
4. For whole-page tasks, also turn on **Page text** (the second pill / the "Include full
   page text" checkbox). This attaches the page's visible text, collapsed and truncated to
   8000 characters. It is a sub-option of Page context.
5. For selection-grounded tasks (Q&A, rewrite, translate), **select the paragraph** on the
   page first and leave Page text off - only your selection is sent.
6. Paste a prompt from below and send.

The context is prepended to your message as a labeled block:

```
Page URL: https://news.ycombinator.com/
Page title: Hacker News
Page text (truncated):
<the page's visible text>
```

or, for a selection:

```
Page URL: https://en.wikipedia.org/wiki/SQLite
Page title: SQLite - Wikipedia
Selected text:
<your selection>
```

## Prompts by site

Copy a prompt, make sure the right toggles are on (noted per group), and send.

### Hacker News front page (`news.ycombinator.com`)

Page text on, no selection.

Top 10 stories as strict JSON:

```
The page text above is the Hacker News front page. Extract the first 10 stories. Output a strict JSON array of exactly 10 objects, each with keys: rank (number), title (string), points (number), comments (number). If points or comments are missing use 0. Output ONLY the JSON array with no prose and no code fence.
```

Top 10 stories as a Markdown table:

```
The page text above is the Hacker News front page. Extract the first 10 stories as a Markdown table with the columns: Rank | Title | Points | Comments. Include the header row and the separator row. Output ONLY the table.
```

Top 10 stories as CSV:

```
The page text above is the Hacker News front page. Extract the first 10 stories as CSV. First line must be the header: rank,title,points,comments. Then one line per story. Wrap any title containing a comma in double quotes. Output ONLY the CSV.
```

Front-page themes (3 bullets):

```
The page text above is the Hacker News front page. Summarize the main themes across the stories in exactly 3 bullet points. Each bullet must be a single sentence. Output ONLY the 3 bullets.
```

Reasoning over the context - which top-5 story has the most points:

```
The page text above is the Hacker News front page. Of the first 5 stories, which has the most points? Answer with just the story title and its points, in the form: <title> - <points> points.
```

Rank the top-5 by points:

```
The page text above is the Hacker News front page. Rank the first 5 stories by points, highest first, as a numbered list where each line is: <rank>. <title> (<points> points). Output ONLY the list.
```

Points delta between the top two:

```
The page text above is the Hacker News front page. Take the first two stories. State the points of story 1, the points of story 2, and the difference (story 1 minus story 2). Format: Story 1: X points; Story 2: Y points; Difference: Z.
```

### Wikipedia: SQLite (`en.wikipedia.org/wiki/SQLite`)

Article summary - page text on:

```
The page text above is a Wikipedia article about SQLite. Summarize it in exactly 5 bullet points. Each bullet is one sentence. Output ONLY the 5 bullets.
```

Extract years mentioned - page text on:

```
From the page text above, list every distinct year (a four-digit number like 2004) that is mentioned, as a plain list with one year per line, sorted ascending. Output ONLY the list.
```

Selection-grounded (select the lead paragraph first; Page text off):

```
Explain the selected text simply, as if to someone new to databases, in 2 to 3 sentences. Output ONLY the explanation.
```

```
Using ONLY the selected text, answer in one sentence: what kind of database engine is SQLite? If the answer is not in the selected text, reply with exactly: not in the selection
```

Honesty case (the answer is absent from the selection - the model should refuse):

```
Using ONLY the selected text, answer in one sentence: who is the current CEO of Google? If the answer is not in the selected text, reply with exactly: not in the selection
```

Rewrite for a child:

```
Rewrite the selected text so a 10-year-old can understand it. Keep it under 80 words. Output ONLY the rewritten text.
```

Translate to Norwegian:

```
Translate the selected text into Norwegian (bokmål). Output ONLY the Norwegian translation.
```

### GitHub: fastapi/fastapi (`github.com/fastapi/fastapi`)

Page text on.

Repo metadata as JSON:

```
The page text above is a GitHub repository page. Output a strict JSON object with keys: name (string, owner/repo), description (string), primary_language (string, your best guess). If a value is unknown use an empty string. Output ONLY the JSON object.
```

Two-bullet summary:

```
The page text above is a GitHub repository page. In exactly 2 bullet points, state (1) what the project is and (2) its primary programming language. Output ONLY the 2 bullets.
```

### MDN: Array.prototype.map() (`developer.mozilla.org`)

Page text on.

Signature extraction:

```
The page text above is the MDN reference for Array.prototype.map(). Extract the call signatures shown in the Syntax section, one per line, exactly as written. Output ONLY the signature lines.
```

Parameters as JSON:

```
The page text above is the MDN reference for Array.prototype.map(). List its parameters as a strict JSON array of objects with keys: name (string), description (string, one short sentence). Output ONLY the JSON array.
```

### arXiv abstract (`arxiv.org/abs/1706.03762`)

One-sentence summary - page text on:

```
The page text above is an arXiv abstract page. Summarize the paper's abstract in ONE sentence of at most 40 words. Output ONLY that sentence.
```

Selection-grounded (select the abstract block first; Page text off):

```
Explain the selected text in plain language for a non-expert, in 3 sentences. Avoid jargon. Output ONLY the explanation.
```

```
Using ONLY the selected text, answer in one sentence: what network architecture does the paper propose? If the answer is not in the selected text, reply with exactly: not in the selection
```

Extract numbers and their meaning:

```
From the selected text, extract every number and what it refers to, as a list with one item per line in the form: number - meaning. If there are no numbers, reply with exactly: none. Output ONLY the list.
```

### Stack Overflow (a canonical question)

Page text on.

```
The page text above is a Stack Overflow page. In 2 to 4 bullet points, summarize the question and the key point of the top answer. Output ONLY the bullets.
```

### Python docs: json module (`docs.python.org/3/library/json.html`)

Page text on.

Module summary:

```
The page text above is the Python documentation for the json module. In exactly 3 bullet points, summarize what this module is for. Each bullet is one sentence. Output ONLY the 3 bullets.
```

Functions as JSON:

```
The page text above is the Python documentation for the json module. List four top-level functions it documents as a strict JSON array of strings (e.g. "json.dumps"). Output ONLY the JSON array.
```

Selection-grounded honesty case (select the intro paragraph; Page text off):

```
Using ONLY the selected text, answer in one sentence: what is the airspeed velocity of an unladen swallow? If the answer is not in the selected text, reply with exactly: not in the selection
```

### DHIS2 play demo (`play.im.dhis2.org/dev-2-42`)

Context-only (no login). Page context on; the URL and title in the context block are
enough.

Extract the instance URL:

```
Using the page context above, output ONLY the base URL of this DHIS2 instance and nothing else.
```

Describe the page:

```
Using the page context above, in 1 to 2 sentences describe what this page is (name the system and whether it looks like a login page or a signed-in app). Output ONLY the description.
```

## Verified results

<!-- RESULTS:START -->

_Auto-generated from a real harness run. Model: `lmstudio-community/gemma-4-12B-it-QAT-GGUF`. 27/28 verified. Last run: 2026-07-11T00:19:10.543Z._

**By category**

| Category | Verified |
| --- | --- |
| extraction | 7/8 |
| summarization | 7/7 |
| explanation | 2/2 |
| selection-qa | 4/4 |
| transformation | 4/4 |
| reasoning | 3/3 |

**Per prompt**

| Prompt | Site | Category | Mode | Status | Latency | Check |
| --- | --- | --- | --- | --- | --- | --- |
| `hn-json-top10` | hn | extraction | page-text | verified | 54.4s | json-array: array of 10 items |
| `hn-markdown-table` | hn | extraction | page-text | unreliable (12B) | 84.6s | markdown-table: only 7 data rows (need >= 8) |
| `hn-csv` | hn | extraction | page-text | verified | 118.5s | csv: 11 csv rows |
| `github-metadata-json` | github | extraction | page-text | verified | 21.2s | json-object: object with keys name,description,primary_language |
| `mdn-callback-args` | mdn | extraction | page-text | verified | 18.3s | contains: contains all of: element, index, array |
| `mdn-params-json` | mdn | extraction | page-text | verified | 15.1s | json-array: array of 2 items |
| `pydocs-funcs-json` | pydocs | extraction | page-text | verified | 60.2s | json-string-array: array of 4 strings |
| `dhis2-url-extract` | dhis2 | extraction | page-text | verified | 26.8s | regex: matched /play\.im\.dhis2\.org/i |
| `hn-themes` | hn | summarization | page-text | verified | 53.7s | bullets: 3 bullets |
| `wiki-summary-bullets` | wikipedia | summarization | page-text | verified | 27.9s | bullets: 5 bullets |
| `arxiv-onesentence` | arxiv | summarization | page-text | verified | 24.3s | length: 32 words |
| `pydocs-summary` | pydocs | summarization | page-text | verified | 21.6s | bullets: 3 bullets |
| `so-summary` | stackoverflow | summarization | page-text | verified | 24.9s | bullets: 3 bullets |
| `github-summary` | github | summarization | page-text | verified | 16.1s | bullets: 2 bullets |
| `wiki-explain-selection` | wikipedia | explanation | selection | verified | 10.0s | length: 50 words |
| `arxiv-explain-selection` | arxiv | explanation | selection | verified | 17.1s | length: 65 words |
| `wiki-selection-qa` | wikipedia | selection-qa | selection | verified | 14.8s | regex: matched /serverless\|self-contained\|embedded\|library\|C language\|written in C |
| `wiki-selection-honesty` | wikipedia | selection-qa | selection | verified | 6.3s | honesty: correctly refused (not in the selection) |
| `arxiv-selection-qa` | arxiv | selection-qa | selection | verified | 8.4s | contains: contains all of: transformer |
| `pydocs-selection-honesty` | pydocs | selection-qa | selection | verified | 8.6s | honesty: correctly refused (not in the selection) |
| `wiki-child-rewrite` | wikipedia | transformation | selection | verified | 16.6s | length: 59 words |
| `wiki-translate-no` | wikipedia | transformation | selection | verified | 34.5s | regex: matched /\b(og\|er\|en\|som\|det\|til\|av\|for\|med\|ikke)\b/i |
| `arxiv-extract-numbers` | arxiv | transformation | selection | verified | 71.2s | regex: matched /\d.*-\|-\s*\d\|none/i |
| `wiki-extract-dates` | wikipedia | transformation | page-text | verified | 33.1s | regex: matched /(19\|20)\d\d/ |
| `hn-most-points` | hn | reasoning | page-text | verified | 14.0s | regex: matched /\d+\s*points/i |
| `hn-rank-by-points` | hn | reasoning | page-text | verified | 22.7s | regex: matched /1\.[\s\S]*\d+\s*points/i |
| `hn-points-delta` | hn | reasoning | page-text | verified | 8.0s | regex: matched /difference:?\s*-?\d+/i |
| `dhis2-context-describe` | dhis2 | summarization | page-text | verified | 7.1s | contains: contains all of: dhis2 |

**Reliability notes**

- `hn-markdown-table`: Borderline at 12B: usually emits the full 10-row table, but sometimes stops after 7 rows even at temperature 0.1 (observed 10/10 and 7/10 across identical runs). The JSON variant (hn-json-top10) is the reliable form; use this one when you want copy-pasteable Markdown and do not mind an occasional short table.
- `mdn-callback-args`: Replaces a signature-extraction prompt: code blocks (the literal map(callbackFn) syntax lines) do not survive page-text capture on MDN, so signature extraction is unanswerable from the attached context - the model deliberates endlessly over missing data. Prose-rendered facts extract reliably.

<!-- RESULTS:END -->

## How verification works

The prompts above are not aspirational - they are checked mechanically against a real
model by the harness in `extension/e2e/prompts/`:

- **Capture** (`capture.ts`): plain Playwright visits each site once and records
  `{url, title, page text (<= 8000 chars, whitespace-collapsed), selection}`, mirroring
  exactly what the extension's `lib/pageContext.ts` collects. Captures are cached to
  `results/captures.json` so reruns can use `--no-capture`.
- **Replay** (`replay.ts` + `context.ts`): the harness reuses the extension's own
  `formatPageContext` to build the context block (a `format-parity.spec.ts` unit test
  guards that they stay identical), prepends it to the prompt, and POSTs to `/api/chat`
  with `use_tools=false` and a low temperature against a locked-model `kodo serve`.
- **Assert** (`checks.ts`): each prompt has a mechanical check - JSON parse + shape +
  item count, Markdown table header/row counts, CSV column counts, keyword/regex
  presence, the honesty refusal, or summary length/bullet bounds.
- **Doc regen** (`regen-doc.ts`): the results table above is rewritten from
  `results/results.json`, so it always reflects the last real run.

### Rerun

```
make extension-prompts
```

This captures the sites, starts a `kodo serve` locked to `gemma-4-12B`, replays every
prompt, writes `extension/e2e/prompts/results/` (git-ignored: `results.json` + per-prompt
raw outputs under `outputs/`), and regenerates the table above. Use
`KODO_PROMPT_BASE_URL=http://127.0.0.1:PORT bun run prompts --no-capture` (from
`extension/`) to replay against an already-running server without re-capturing.

The end-to-end last mile - a verified prompt driven through the real side panel against a
real `kodo` - is covered by `e2e/prompts/ui.spec.ts` (`bun run prompts:ui`).
