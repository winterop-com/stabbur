# UI conventions

The rules stabbur's browser UI is built to, written down so the next change doesn't
have to re-derive them. They are deliberately about **form, not subject** — a type
scale and a colour vocabulary say nothing about models or chat, so this page ports
to a sibling app unchanged. stabbur's visual family is
[`dhis2w-fhir-serve`](https://github.com/winterop-com/dhis2w-utils); where the two
disagree, this page is stabbur's answer and the reasoning is stated, because the
sibling is a codebase, not a specification.

Scope: `frontend/` (the SPA served by `stabbur serve --ui`). See
[Not yet swept](#not-yet-swept) for what this does not cover.

---

## The type scale

**Three sizes. Nothing else, and nothing hand-written.**

| Class | Size | Role |
| --- | --- | --- |
| `text-base` | 16px / 24px | **Chat body.** The message you read and the box you type it in — assistant markdown, the user bubble, the composer. The one surface that is a document rather than an interface. |
| `text-sm` | 14px / 20px | **The default.** Explanatory prose (slider descriptions, hints, notes, the sentence under a section heading), control and field labels, row titles, loading and empty states. When in doubt it is this. |
| `text-xs` | 12px / 16px | **Annotation.** Chips, badges, counts, uppercase eyebrows, timings, sizes, paths, machine identifiers — text *about* something else on screen rather than something you read. |

Two things follow from the table and are worth saying outright:

- **Nothing is smaller than `text-xs`.** 10px and 11px were never a decision; they
  were 102 separate ones, each locally plausible and collectively a UI that reads
  as though the window had been zoomed out.
- **`text-sm` is the floor for a sentence.** If it wraps, it is prose. Prose gets
  14px whatever it is sitting inside — a 320px rail is a reason to change the rail,
  not a reason to shrink the sentence. (The sibling's 221 `text-xs` to 171
  `text-sm` split is the same call made independently; its footer prose measures
  14px/20px/400, which is `text-sm` exactly.)

**Weight and case carry the rest of the hierarchy, not size.** A section heading is
`text-xs font-semibold uppercase tracking-wide`; a panel title is `text-sm
font-semibold tracking-tight`. Both are small — what makes them headings is that
everything around them is neither bold nor uppercase. Reaching for a bigger size is
almost always reaching for the wrong axis: stabbur's surfaces are dense, and a 20px
heading inside a rail buys emphasis by spending room.

### No hand-written pixel sizes

`text-[11px]`, `text-[0.8rem]`, `text-[13px]` — all banned, and this is
[enforced](#what-is-enforced). Not because any one of them is wrong, but because an
arbitrary value is invisible to review: it looks like a considered choice at the
call site and is indistinguishable from a typo three files away. A scale only
works if it is the only thing available.

**The `text-[0.8rem]` exception was removed rather than blessed.** `StatusBar.tsx`
carried it to match the sibling's 12.8px nav label to the pixel. It is now
`text-xs`. Two reasons, in order: the segment is a nav label, and the table above
already has an answer for a nav label — no exception was needed, only a lookup. And
the difference is 0.8px, against the standing cost of an allowlist that every
future site can argue its way onto. A rule with one justified exception has two,
then five. (The sibling's own 12.8px is not a considered size either — it falls out
of shadcn's `size="sm"` button variant.)

**The one relative form that is allowed** is `text-[0.85em]` on fenced code
(`CodeBlock.tsx`, `MermaidDiagram.tsx`), mirroring `.prose-chat :not(pre) > code`
in `index.css`. `em` is not an opt-out from the scale — it is a fixed ratio *to*
it, so a code block stays 85% of whatever the prose around it is. That is a
mechanism, not a magic number, which is why the check flags `px`/`rem`/`pt` and
leaves `em` alone.

---

## Colour

Every colour is a variable. The palette lives in `frontend/src/index.css`, stated
once per theme, and every theme restates every token in the same order — a token
added to `:root` is a token added to all ten blocks, or some theme inherits another
theme's green.

### The semantic set — what a state *means*

`--good` · `--critical` · `--warning` · `--info`. Four words, chosen before any
component picks a colour. Every theme moves their lightness and chroma to suit its
ground but never their hue family: green means healthy and amber means
look-at-this on every screen stabbur has.

`--critical` is **not** `--destructive`, and the split is by *who acts*:

- `--destructive` is an affordance the reader can press to destroy something (and
  the error text shadcn already paints with it).
- `--critical` is a state stabbur **observed** and is reporting. A failed doctor check
  has nothing for the reader to press.

### `-ink`: the fill/text split

`--good` and `--warning` are tuned as **fills** — a dot, a tinted background, a
border — where saturation reads and contrast is against the fill itself. As small
**text** on the page ground they are too light: in the light themes green sits at
L≈0.6 and amber at L≈0.65, which fails against a near-white background at 12px.

So each has a darker text-only twin. Use them like this:

```tsx
// fill: the token itself
<span className="border-good/30 bg-good/10 text-good-ink">tts</span>
<span className="h-2 w-2 rounded-full bg-good" />

// text: always the -ink variant
<p className="text-warning-ink">Off, but still running.</p>
```

In the dark themes the fill is already light enough to read as text, so every dark
block declares `--good-ink: var(--good)` and the split costs nothing there. That
aliasing is the point: the component says `text-good-ink` once and never learns
which mode it is in.

`--critical` and `--info` need no twin — red and blue are dark enough at their
light-theme lightness to be read directly.

### The rail is a surface of its own

`--sidebar-*` is a complete family (ground, foreground, active fill, border, ring)
rather than the page's tokens at an alpha, because a rail washed over a page ground
lands muddy — the active row and the hover row differ by a few percent of the same
tint, which on some themes is invisible. Two tones are *derived* from the rail's own
pair (`--sidebar-muted-foreground`, `--sidebar-wash`) so they follow whichever rail
is in force. **Nothing on the rail may borrow the page's `--muted-foreground`**: a
dark rail under a light page would render it unreadable.

`--code-*` are spent by the `.hljs-*` rules alone and deliberately get **no**
Tailwind utilities, so no component can paint prose with a syntax colour.

### Literals

A hex or a Tailwind palette colour is allowed in exactly one situation: it mirrors
an identity that already exists outside the theme and must not move with it.

- `LibraryView.tsx`'s cyan (`gguf`) and fuchsia (`mlx`) mirror the CLI's per-format
  colours. The same model is the same colour in the terminal and in the browser,
  which is worth more than theme-following. `safetensors` takes `--warning`,
  because there its amber *does* mean something semantic — not ready to run, and
  2-4x the size of the quant.
- `bg-black/*` scrims (`ui/sheet.tsx`, `ui/dialog.tsx`, image and rail overlays)
  darken *arbitrary content* underneath, not a themed surface. There is no token
  for "less light through", and a themed scrim inverts in dark mode.

Everything else is a token.

---

## Chrome

### Chrome is a width, never a share of the display

Every frame around the content — the sidebar, the settings rail, the bars — is
sized in **pixels**. `react-resizable-panels` speaks in percentages of the panel
group, so `App.tsx` measures the group and converts: the pixel figure is the
intent, the percentage is only how it is expressed to the library
(`SIDEBAR_DEFAULT_PX`, `CHAT_SETTINGS_MIN_PX`, `pctOfGroup`).

This is not tidiness. An 18% sidebar is 230px at 1280 and 461px at 2560, so on a
large monitor every frame grows while the type does not, and the app reads as
though it had been zoomed out — which is exactly the complaint that started this
document. The type was a red herring; the chrome was the cause. stabbur's sidebar
was 345px against the sibling's fixed 239px on the same display.

The trap, if you add another one: a percentage-sized panel re-lays-out on a
window resize *without* changing its percentage, so reading its size back at that
moment records the scaled width and bakes the zoom straight back in. Learn a new
width only from a change the reader caused (`onSidebarResize` guards on the group
width the panel was last expressed against, and ignores both a collapse and the
library's expand-restore).

### Say it once

Four separate reports of the same fault, all fixed the same way:

- The **Settings row** was pinned inside the sidebar, so the moment a status bar
  existed there were two stacked bands across the foot of the window. It is now
  the status bar's left segment.
- The **title** was drawn by a `ViewBand` *inside* Library and Voice, immediately
  under a top bar whose left half was an empty `<div>` — two strips, the upper one
  blank, and no title at all on Chat. The title now lives in the one top bar
  (`lib/view-title` is how a view publishes it upward), and Chat carries the
  conversation's name.
- The **loaded model** was a pill in the top bar *and* the composer's picker. The
  picker is a strict superset — it also chooses a model and filters by capability
  — and the model governs the next message, so it belongs beside the box that
  message is typed into. The pill is gone.
- The **status sentence** (backend, model count, tool count) was a line across the
  status bar, and every fact in it was already a row in `stabbur doctor`, which the
  health menu renders. A hand-written copy of a list that maintains itself can only
  drift. The bar states none of them now.

The rule: **one fact, one place, chosen by which surface the fact acts on.** When
a summary moves into the chrome, delete the copy underneath it (the Library's
"Chat" heading dropped its count when the top bar's chip took it — and note the
Voice heading kept its own, because the bar says nothing about voice models).

### The bars

One top bar and one bottom bar, on every surface, looking the same on every
surface. The `ViewBand` that preceded the merged header was *tinted*, which was
right for a strip only two views wore — it marked them as dense data views
against the transcript. As the app's one top bar it is chrome, and chrome that
changes colour when you navigate reads as an inconsistency rather than as a
distinction, so the tint is gone and a hairline separates the bar from the
content. Title everywhere, one ground everywhere.

Bar geometry is matched to the sibling's, measured rather than guessed, because
the two get compared side by side: a 46px status bar, its Settings row a
*contained* 28px element with ~9px of air over and under it rather than a
full-height slab, the gear 20px off the window edge, and anything to the right of
the divider inset 48px from it rather than hugging it.

**A bar is allowed to be empty.** The status bar's right half normally says
nothing at all, and that is the finished state rather than a gap to fill: the
frame closes the window, the Settings segment continues the rail's column past its
foot, and the facts live where they are maintained. The single exception is the
one thing no other surface can report — "Not connected to a stabbur server", which is
an alarm, not a readout, and which the health menu cannot show because a server
that is down does not answer `/api/doctor` either.

### A count is not a list

`Tools (MCP)` said "3 (datetime, network, files)" — how many, with which-one
folded into a comma-separated sentence. Rows that are *details of* another row
nest under it and expand, so the breakdown is a structure rather than prose. The
nesting comes from an explicit `group` field on the check, never from a naming
convention or from parsing a `detail` string; a payload without the field renders
flat, because a health report that silently drops a row it cannot place is worse
than an untidy one.

---

## The command palette

cmdk's default filter is a fuzzy **subsequence** match over one string per row.
Over a list that mixes sentences with machine spellings that is not a ranking, it
is a coin toss: `swit` matched inside *"the wide**s**t separa**t**ion this app has
between text and the surface under it"* and put three theme rows above "Switch to
dark mode", which did not appear at all.

`lib/palette.ts` replaces it, and the rule is:

- **Words are matched as words.** A token has to be present in full. Nothing is
  found by scattering a query's letters through a sentence, which is what makes
  "Nothing matches that" a state a reader can actually reach.
- **An identifier is matched by the start of the string and nothing else.** A
  model id is not prose. A run of letters and digits contains most short
  sequences somewhere inside it, so an id in a row's *words* makes that row a
  wildcard that answers almost anything typed.
- **A row's own keywords outrank the sentence beside it.** `dark` is what the
  mode toggle is *for* and a word that merely occurs in a theme's description.
- **A shared word costs more than a wrong one.** cmdk keeps groups in the order
  they are built, so a word on an earlier shelf beats a better match on a later
  one whatever it scores. Every model carrying the keyword `switch` (from the
  shelf heading "Switch model") was enough to bury the mode toggle. A row's
  vocabulary has to be *exclusive*, not merely accurate.

Labels are the vocabulary a person would say out loud: **"Switch to dark mode"**,
not "Dark mode" — a label that never contains the word cannot be reached by any
prefix of it. Same wording as the sibling.

---

## Recipes

The shapes already in the tree. Reuse them; a new one should be a deliberate
addition, not a near-miss of one of these.

**Section** — a titled block in a settings surface. Eyebrow, optional sentence, then
the controls.

```tsx
<section className="border-t border-border px-4 py-4 first:border-t-0">
  <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
  {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
  <div className="mt-3">{children}</div>
</section>
```

**Chip** — a rounded, bordered fact. Tinted from the semantic set when it carries a
state, `border-border bg-muted/60` when it is just a label.

```tsx
<span className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-xs">{tag}</span>
<span className="rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-xs text-warning-ink">not runnable yet</span>
```

A **badge** is the same thing squared off, for a format or a kind:
`rounded border px-1.5 py-0.5 text-xs font-medium uppercase tracking-wide`.

**Labelled row** — a name and its sentence on the left, one control on the right.
The label is `text-sm font-medium` because it names something you operate; the line
under it is prose.

```tsx
<div className="flex items-center justify-between gap-3">
  <div className="min-w-0">
    <div className="text-sm font-medium">Parse PDF as image</div>
    <div className="text-sm text-muted-foreground">Render pages instead of extracting text.</div>
  </div>
  <Switch … />
</div>
```

**Card** — `rounded-xl border border-border p-3` for a grid card (library, voice),
`rounded-md border border-border bg-background/40` for a card nested inside a
panel. Hover states go on the border (`hover:border-primary/40`), never on the
size.

**Note** — one line explaining why a control is inert or why something did not do
what it looked like it did. `text-sm text-muted-foreground`, or `text-warning-ink`
when it is a state worth looking at. Never a chip: a sentence is not a fact.

### Density

The scale sets type; these set the room around it. A dense surface earns its
density from consistent padding, not from small type.

- Panel/section padding `px-4 py-4`; a card nested inside it `px-2.5 py-2`.
- Gap between a label and its explanatory line: `mt-1`. Between a control and the
  sentence under it: `mt-1.5`.
- Interactive rows are at least 28px tall (`py-2` at `text-sm`), so a pointer has
  something to hit.

---

## What is enforced

Three checks, all in `make check` (the CI gate). Be clear about which does what:

**`oxlint`** (`frontend/.oxlintrc.json`, run by `bun run lint`) lints **JS and
TypeScript** — hook rules, unused code, `no-explicit-any`, `eqeqeq`, correctness.
It is the same config as the sibling. It **cannot** see anything on this page: to
oxlint a `className` is an opaque string literal, so it will never catch
`text-[11px]`, a wrong token, or a missing `-ink`. Adopting it closes a real gap
(the SPA had no linter at all), but it is not what enforces the UI standard.

**`scripts/check_ui_classes.py`** is what enforces the type rule. It scans
`frontend/src` for hand-written absolute type sizes — `text-[<n>px]`,
`text-[<n>rem]`, `text-[<n>pt]`, including inside variant forms like
`[&_[cmdk-group-heading]]:text-[11px]` — and fails with the file, the line, the
class it found, and the class to use instead. It is deliberately narrow: one rule,
zero false positives, and no allowlist. Run it alone with
`uv run python scripts/check_ui_classes.py`.

**Neither check enforces the rest of this page.** Whether a sentence got `text-sm`
or `text-xs`, whether a fill token was used as text, whether a new chip matched the
recipe, whether a fact ended up stated twice — those are review, and this document
is what review reads.

**`vitest`** (`frontend/vitest.config.ts`, run by `bun run test`) is the third, and
it is deliberately narrow. It covers one module: `lib/history`, the chat history's
IndexedDB store. That is the only place in the SPA holding something a reader
cannot get back if it goes wrong — a transcript with its attachments — and it was
added when history moved off localStorage, because a migration between two storage
engines is exactly the change a browser click-through cannot prove. It renders
nothing (`environment: "node"`, `fake-indexeddb` for the store), so no DOM testing
library is in the tree.

The obvious second candidate is **the palette's ranking** (`lib/palette.ts`), the
one part of this page that is a pure function with an assertable answer: it is
verified by hand in a browser today, and the runner it needs is now here. The cases
worth pinning are `swit`/`switch`/`dark`/`light` surfacing the mode toggle first,
`phosphor` still finding Terminal, and a model id neither answering an unrelated
query nor failing to answer its own prefix.

### Not yet swept

Honest gaps, not exemptions:

- **`extension/`** (the Chrome MV3 side panel) is a second SPA with 12 arbitrary
  type sizes of its own and is outside the check's roots. It should be swept and
  added; it was left out to keep one change one subject.
- **`frontend/src/components/ToolsControl.tsx`** is skipped by name in the check.
  It is unreferenced — no import anywhere in the tree — and whether it is revived
  or deleted is a separate decision from what size its text should be. Deleting it
  deletes the skip.
