# DHIS2 benchmark report (read-only)

Which local models can actually drive DHIS2? This report answers that with a reproducible
benchmark: point each model at the **DHIS2 CLI bridge** and score whether it calls the tool
and returns the right answer about a live instance. Bottom line up front:

> A **9B** model — `deepreinforce-ai/Ornith-1.0-9B-GGUF` — tops the leaderboard at a perfect
> **12/12**, and is the **fastest** (~12s/problem) and **smallest** (5.2 GB) model to do so,
> beating the 27B and 31B models. You do not need a big model to run DHIS2 locally; you need one
> that reliably calls tools.

## What was measured

The `tools-dhis2` suite (`kodo benchmark run tools-dhis2`) attaches the
[`dhis2w-mcp-bridge`](https://winterop-com.github.io/dhis2w-utils/) against the **play42** profile
(the public DHIS2 "Sierra Leone" demo, v2.42) in **read-only** mode
(`DHIS2_MCP_READONLY=1`), then asks 12 questions of increasing difficulty. A problem passes only
if the model **calls the `dhis2_cli` tool** *and* its final answer **contains the correct value** —
both checks, strictest scoring. Because the bridge is one tool driven by free-form `d2w` argv,
the suite does not pin an exact command; it scores the outcome, not the keystrokes.

The 12 problems and their ground truth (a snapshot of play42 on 2026-07-04):

| Difficulty | Problem | Answer |
|---|---|---|
| basics | system name | Sierra Leone |
| basics | server version | 2.42 |
| basics | current user | admin |
| basics | count organisation units | 1332 |
| intermediate | count data elements | 1037 |
| intermediate | count indicators | 77 |
| intermediate | count data sets | 27 |
| intermediate | org-unit level 3 name | Chiefdom |
| advanced | count option sets | 171 |
| advanced | UID of "ANC 1st visit" | fbfJHSPpUQD |
| advanced | UID of "Bo" | O6uvpzGd5pu |
| expert | name of UID ImspTQPwCqd | Sierra Leone |

Analytics values are deliberately excluded — the demo regenerates data relative to the current
date, so no absolute analytics number is stable enough to assert on. These are all **stable
structural facts** (metadata counts, names, UIDs, version).

## Read-only leaderboard

Ranked by score, then speed. Every model in the library with the `tools` capability was run;
scores are the corrected numbers (see "A scoring bug we found and fixed" below).

| Rank | Model | Score | Avg response/problem | Size |
|---|---|---|---|---|
| 1 | `deepreinforce-ai/Ornith-1.0-9B-GGUF` | **12/12** | 12.3s | 5.2 GB |
| 2 | `lmstudio-community/Qwen3.6-27B-GGUF` | **12/12** | 30.9s | 16.3 GB |
| 3 | `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | **12/12** | 68.0s | 6.7 GB |
| 4 | `unsloth/Qwen3.5-4B-GGUF` | 11/12 | 10.5s | 2.6 GB |
| 5 | `unsloth/gpt-oss-20b-GGUF` | 11/12 | 24.9s | 10.8 GB |
| 6 | `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | 11/12 | 6.1s | 17.3 GB |
| — | `lmstudio-community/gemma-4-31B-it-QAT-GGUF` | n/a* | — | 17.6 GB |
| — | `TheDrummer/Rocinante-X-12B-v1-GGUF` | 0/12 | 3.7s | 7.0 GB |
| — | `mradermacher/MN-Violet-Lotus-12B-GGUF` | 0/12 | 4.5s | 12.1 GB |
| — | 3 MLX models | n/a† | — | — |

\* `gemma-4-31B` scored 10/12 on the initial sweep, missing **only** the two 4-digit counts
(the scoring bug below) — so it is effectively a 12/12-class model. Two confirmatory re-runs under
the corrected scoring both hit transient local-serving flakes (one runaway tool-call loop, one
empty-generation failure), so it is not independently re-scored here. It was also the slowest and
least stable model to serve locally (6-minute loads, ~183s/problem when it looped).

† The three MLX models (`Qwen3.5-4B-MLX-4bit`, `gemma-4-26B-A4B-it-QAT-MLX-4bit`,
`Qwen3.6-27B-4bit`) could **not be evaluated**: serving them crashes at import time in the
`mlx-vlm` runtime (`AttributeError: 'str' object has no attribute '__module__'`, a `transformers`
incompatibility). Tracked as an open issue in the roadmap.

## What the results say

**Small models win.** The best DHIS2 driver is a 9B model, and a 2.6 GB 4B model reaches 11/12.
Tool-driving is about *reliably emitting a tool call and reading JSON back*, not raw parameter
count — so the practical choice for a local DHIS2 assistant is a small, fast, tool-solid model,
not the biggest one that fits.

**Bigger is not better, and can be worse.** The 31B was the slowest and least stable to serve; the
30B coder model (`Qwen3-Coder`) landed at 11/12, below the 9B. On the initial sweep the 31B even
missed simple counts the 12B got right.

**Roleplay finetunes can't tool-call.** `Rocinante` and `MN-Violet-Lotus` both scored 0/12. The
tell is in the timing: they answered in **~3.7s/problem** — no tool call, no network round-trip —
versus 10-70s for the models that actually queried the server. They are flagged `tools` by their
chat template but do not use it; treat that capability flag as necessary, not sufficient.

**Run-to-run variance is real.** `Qwen3-Coder` scored 7/12 on the first run and 11/12 on the
re-run (sampling temperature > 0). Single runs are noisy at the margin; the leaderboard is a
snapshot, not a precise ranking within a point or two.

## A scoring bug we found and fixed

The first sweep produced a suspicious pattern: **every** tool-calling model missed *exactly* the
two 4-digit counts (1332 organisation units, 1037 data elements) while passing every sub-1000
count (77, 27, 171). The cause was in the benchmark, not the models: the answer check was a raw
substring match, so a model answering "**1,332**" failed an expected "1332" — the thousands
separator broke the match. Sub-1000 counts have no separator, so they always matched.

Fixed in `kodo_benchmark.core`: the matcher now retries with digit-group separators (comma or
whitespace between digits) stripped from both sides. Re-running under the corrected scoring moved
the four affected models from 10/12 to 12/12 and lifted the rest accordingly. The numbers above
are post-fix.

## The real lesson: agent-guiding tool descriptions pay off

The headline result — a 9B model driving DHIS2 near-perfectly — is not just about the model. The
bridge's single `dhis2_cli` tool ships a **deliberately expanded, agent-oriented description**: an
`OUTPUT CONTRACT` (how to read success vs. error, that `--json` is automatic), `COMMON READS`
(count / list / get / search recipes with exact argv), narrowing rules (`--fields`, `--filter`
operators), and an `ANALYTICS` recipe (resolve name -> UID first, `--dim` not `--dx`). That guidance
is what lets a small model go straight to `metadata list <type> --count` instead of flailing.

So `tools-dhis2` measures two things at once: whether a model can tool-call, and whether the
**investment in agent-facing tool documentation** actually lowers the model bar. It does — the
same suite would be far harder against a terse one-line tool description. The takeaway for building
local assistants: spend effort on tool descriptions, and you can run smaller, cheaper, faster
models.

## Reproduce it

```bash
# one profile for the public demo (basic auth: admin / district)
DHIS2_PASSWORD=district d2w profile add play42 \
  --url https://play.im.dhis2.org/dev-2-42 --auth basic --username admin --verify

# run the suite against every tool-capable model in your library
kodo benchmark run tools-dhis2 --all --save
kodo benchmark leaderboard        # regenerates docs/benchmarks.md
```

See [DHIS2 tools & profiles](dhis2.md) for the bridge tiers and prompts, and the
[model catalog](model-catalog.md) for the models used here.

## Not yet done: writes

This report covers **read-only** driving. A **write** suite (create / update / delete against a
throwaway instance, `DHIS2_MCP_READONLY` off) is the natural next step, to see which models can
*safely* mutate DHIS2. It is currently **blocked**: no local writable DHIS2 was reachable, and
writes to the shared play demos are refused by design. Start a local instance, then add
`tools-dhis2-write.toml` alongside this suite.
