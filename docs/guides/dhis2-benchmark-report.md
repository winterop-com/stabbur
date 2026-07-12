# DHIS2 benchmark report

Which local models can actually drive DHIS2? This report answers that with two reproducible
benchmarks against the **DHIS2 CLI bridge**: a **read-only** suite (does the model call the tool
and return the right answer about a live instance?) and a **write** suite (can the model safely
complete a create -> rename/link -> delete lifecycle?). Bottom line up front:

> A **9B** model — `deepreinforce-ai/Ornith-1.0-9B-GGUF` — tops the **read-only** leaderboard at a
> perfect **12/12**, and is the **fastest** (~12s/problem) and **smallest** (5.2 GB) model to do so,
> beating the 27B and 31B models. You do not need a big model to run DHIS2 locally; you need one
> that reliably calls tools.

> **Writes are a different, much harder story.** Under scoring that verifies **real DHIS2 state**
> (not just a self-reported completion token), the strongest writer — `gemma-4-12B` — completes
> **0 of 7** lifecycles: it reliably *creates* objects but does not reliably *delete* them, leaving
> residue on every problem. A live end-to-end test confirms the write *path* works (a create,
> approved through the confirmation gate, really persists and read-back-verifies); the *reliability*
> is the model's limit. This is why writes ship behind a **per-action confirmation gate** — the
> human, not the model, is the safety net. See "[Writes](#writes-create-update-delete)" below.

!!! note "Re-verified 2026-07-12 (compact-JSON tool output)"

    heim now hands tool results to the model as **compact JSON** rather than the older Python
    `repr` text, so the two locally-available models were re-run to confirm the leaderboard still
    holds under the new shape. Both reproduce **12/12**: `Ornith-1.0-9B` on 3/3 clean runs and
    `gemma-4-12B` on 2/2. Two changes came out of it: the `count data sets` ground truth was
    refreshed **27 → 28** (the play demo added a data set since the 2026-07-04 snapshot; every
    other count still matches), and generation should be **bounded** (`max_tokens`) — an uncapped
    run occasionally lets a small model run away on the hardest problem and drop its final answer,
    which reads as a spurious miss. The larger sweep models below were not re-run (not in the local
    library); their scores are the original 2026-07-04 measurements.

## What was measured

The `tools-dhis2` suite (`heim benchmark run tools-dhis2`) attaches the
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
| intermediate | count data sets | 28 |
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

Fixed in `heim_benchmark.core`: the matcher now retries with digit-group separators (comma or
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
heim benchmark run tools-dhis2 --all --save
heim benchmark leaderboard        # regenerates docs/benchmarks.md
```

See [DHIS2 tools & profiles](dhis2.md) for the bridge tiers and prompts, and the
[model catalog](model-catalog.md) for the models used here.

## Writes: create, update, delete

The read-only suite above measures *reading* DHIS2. The **write** suite
(`tools-dhis2-write`, `heim benchmark run tools-dhis2-write`) measures whether a model can *safely
mutate* it — the harder, higher-stakes half. Bottom line: it can't, not yet, not unattended.

### What was measured

Each of the 7 problems is a **self-cleaning lifecycle**: the model creates one or more metadata
objects, optionally renames or links them, then **deletes everything it made**, so a correct run
leaves the instance exactly as it found it. Every test object is prefixed `HEIM_`.

A problem passes only when the suite **verifies real DHIS2 state**: after the run it reads the live
instance back and requires that the object was actually created (a real, non-errored create) **and
is absent at the end** (the delete really happened) — not merely that the model called the tool and
printed a `LIFECYCLE_OK` token. Between models the runner **sweeps** any `HEIM_`-prefixed residue
directly against the instance, so a model that abandons a lifecycle can't leave orphans that skew
the next model.

!!! warning "Scoring correction (2026-07-12)"
    An earlier version of this suite scored a pass on "called the bridge at least once **and** the
    final answer contains `LIFECYCLE_OK`". That overcounted badly: a model that created objects,
    never deleted them, and printed the token still passed. The state-verifying scorer and the real
    residue sweep above replace that proxy — and the "sweep between models" this report previously
    described was, at the time, aspirational (it now exists in code). The figures below are under
    the new, state-verified scoring; the earlier proxy numbers ran higher and are superseded.

The bridge runs **read-write** (no `DHIS2_MCP_READONLY`) against a `local_basic` profile
(`localhost:8080`, admin/district) — never a shared or production instance. Writes cannot target
the public play demos: they are `DHIS2_MCP_PROTECTED_HOSTS` the bridge refuses writes to regardless
of mode, so this suite requires a local writable DHIS2 (the blocker that once held it up is gone).
The 7 problems, by difficulty:

| Difficulty | Problem | Lifecycle |
|---|---|---|
| basics | `de-create-delete` | create a NUMBER data element, delete it |
| basics | `ig-create-delete` | create an indicator group, delete it |
| intermediate | `de-rename-delete` | create a data element, rename it, delete it |
| intermediate | `deg-create-delete` | create a data element group, delete it |
| advanced | `de-create-verify-delete` | create a data element, read it back by name for its UID, delete by UID |
| advanced | `deg-add-member-delete` | create a data element **and** a group, add the element to the group, delete both |
| expert | `indicator-create-delete` | resolve an existing indicator type's UID, create an indicator using it, delete it |

### Write results (state-verified)

So far only `gemma-4-12B` — the strongest writer under the old proxy — has been re-run under the
state-verifying scorer:

| Model | Score (state-verified) | What happened |
|---|---|---|
| `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | **0/7** | creates an object on every problem but never completes the deletes; the sweep removed 7 residual `HEIM_` objects afterward |

The earlier proxy leaderboard ranked six models from 4/7 down to 1/7; those figures counted the
`LIFECYCLE_OK` token rather than real state and are superseded. Re-running the rest under state
verification is pending — but since the proxy already flattered them and gemma (the proxy's best)
drops to 0/7 under real verification, the honest expectation is that none clear the bar unattended.

### What the write results say

**The honest picture is worse than the proxy suggested — and it is the *delete* half that fails.**
Under real state verification, gemma-4-12B — the strongest writer under the old scoring — completes
**0 of 7** lifecycles. It reliably *creates* objects but does not reliably *delete* them, so it
leaves residue on every problem. The old "4/7" counted a completion token the model emitted whether
or not the deletes actually landed; verifying real state removes that illusion.

**The write path works — the model is the bottleneck.** A live end-to-end test drives the Chrome
panel against this same local instance: bind a write-enabled assistant, ask it to create a data
element group, approve each confirmation, and an independent authenticated read-back confirms the
object really persisted. That proves the plumbing (bind -> confirm gate -> approve -> execute ->
persist -> read-back). The same test shows the delete step is unreliable (it is driven best-effort;
a deterministic sweep guarantees cleanup). So reads are excellent (12/12), the write *path* is
proven end-to-end, and write *reliability* is a model limitation the gate **contains** rather than
solves — the human approving each mutation, and noticing an incomplete cleanup, is the safety net.

**Reliable reading and reliable mutating are different skills.** A model's read rank does not
predict its writes: `Ornith-1.0-9B` is a flawless 12/12 reader yet was the weakest writer even under
the flattering proxy. Bigger models did not help under the proxy, and the multi-step
create-verify-delete lifecycle — not raw capacity — is what trips models up.

**The multi-object step is the wall.** Per problem, every model clears the simple shapes and
every model fails the compound ones:

| Problem | difficulty | Models passing (of 6) |
|---|---|---|
| `de-create-delete` | basics | 4 |
| `ig-create-delete` | basics | 4 |
| `deg-create-delete` | intermediate | 3 |
| `de-create-verify-delete` | advanced | 2 |
| `de-rename-delete` | intermediate | 1 |
| `deg-add-member-delete` | advanced | **0** |
| `indicator-create-delete` | expert | **0** |

A lone create+delete lands ~4/6. The moment a lifecycle adds a **second linked object**
(`deg-add-member-delete`: create element + create group + link + delete both) or a **dependency
resolution** (`indicator-create-delete`: look up an indicator type first, then use its UID), **no
model completes it**. Even a mid-lifecycle **rename** (`de-rename-delete`) trips five of six. The
failure is not the individual API call — it is holding a multi-step plan together through to the
final deletes without losing the thread.

### Reproduce it

```bash
# a read-write profile against a LOCAL, non-protected instance (never a shared demo)
DHIS2_PASSWORD=district d2w profile add local_basic \
  --url http://localhost:8080 --auth basic --username admin --verify

# run the write suite across your tool-capable models
heim benchmark run tools-dhis2-write --all --save
heim benchmark leaderboard        # regenerates docs/benchmarks.md
```

The benchmark drives the bridge directly (auto-approving mutations) to measure raw model
capability. In the interactive surfaces — `heim serve --ui`, the Chrome side panel, and the
Textual TUI — the same writes are instead **gated per action**: the assistant prompts the user to
approve or deny each mutation before it runs (a declined call returns `error: user declined this
action` and the model continues). The scripted one-shot `heim chat -p` has no confirm channel, so
it **fail-safe denies** gated writes unless `--allow-writes` is passed. See `CHROME.md` for the
gate's design and the `ROADMAP.md` "DHIS2 write reliability" thread for what improves it next
(stronger write models; the typed `dhis2w-mcp-router` so reads skip the prompt; verification that
asserts real DHIS2 state, not just the `LIFECYCLE_OK` token).
