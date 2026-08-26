/**
 * What the command palette offers, as data — and how a typed query is scored against it.
 *
 * WHY THIS IS NOT INSIDE THE COMPONENT. cmdk's default filter is a fuzzy *subsequence* match over
 * one string per row, and stabbur hands it rows carrying prose and rows carrying machine spellings.
 * Scattering four letters through a sentence finds almost anything: typing `swit` matched inside
 * "the wide**s**t separa**t**ion this app has between text and the surface under it" and put three
 * theme rows above "Switch to dark mode", which did not appear at all. Model ids are the same
 * failure waiting to happen — a run of letters and digits contains most short sequences somewhere
 * inside it, so every model on the list acts as a wildcard.
 *
 * SO THE TWO KINDS OF STRING ARE MATCHED BY TWO DIFFERENT RULES. Words are matched **as words** (a
 * token has to be there in full, at a word boundary or at least contiguously); an `identifier` is
 * matched by the **start of the string and nothing else**. `paletteScore` is where they meet.
 * Ported from the dhis2w-fhir-serve palette, which hit this first and left the reasoning behind.
 *
 * The whole surface is pure data with no React, no router and no theme store in scope, which is what
 * makes the ranking assertable without a browser. (stabbur's frontend has no test runner yet — see
 * docs/ui-conventions.md; this module is shaped so that adding one is all it would take.)
 */

/** One row the palette offers: what it is called, what it says, and what it can be found by. */
export interface PaletteRow {
  /** Stable and unique. It is the row's cmdk `value`, its React key, and what `onSelect` hands back. */
  id: string;
  /** The heading it is shelved under. Rows keep the order they are built in. */
  group: string;
  /** The visible name. Matched as words. */
  label: string;
  /** The muted line beside the label, or null. Matched, but ranked under the label and the keywords. */
  hint: string | null;
  /** The right-aligned marker: "loaded", a size, "current". Never matched — it is a state, not a name. */
  trailing: string | null;
  /** Extra words the filter matches and nothing renders — how a reader's word reaches stabbur's word. */
  keywords: string[];
  /**
   * The machine spelling this row is served under, or null.
   *
   * A model id is not prose and must never be searched as if it were. It is matched by the start of
   * the string and by nothing else; the words on the row are matched as words. See the module note.
   */
  identifier: string | null;
}

/**
 * What the control between the two grounds is called — here and anywhere else it is offered.
 *
 * "Switch to …", not "Dark mode", and the wording is the sibling app's. A palette row is found by
 * typing what you would say out loud, and what a person types when they want this is "switch".
 * A label that never contains the word cannot be reached by any prefix of it.
 *
 * "Mode" and not "theme": stabbur has five themes and every one of them has both grounds, so a row
 * offering to "switch to the dark theme" would be naming the wrong axis.
 */
export const SWITCH_TO_DARK_LABEL = "Switch to dark mode";
export const SWITCH_TO_LIGHT_LABEL = "Switch to light mode";

/** The shelves, in the order the palette lays them out. */
export const GO_TO_GROUP = "Go to";
export const CHAT_GROUP = "Chat";
export const MODEL_GROUP = "Switch model";
export const RECENTS_GROUP = "Recent chats";
export const VIEW_GROUP = "View";
export const THEME_GROUP = "Theme";

/** What the row list is built from: this run's holdings and the state the toggles report. */
export interface PaletteInput {
  /** Library models, in the order the palette should offer them. */
  models: { name: string; size_human: string }[];
  /** The model stabbur currently has loaded, so its row says so instead of repeating its size. */
  loaded: string | null;
  /** The recent conversations to offer, newest first and already capped. */
  recents: { id: string; title: string }[];
  /** Whether the dark ground is in force, so the mode row offers the other one. */
  dark: boolean;
  /** The theme in force, so its row is marked rather than offered as a change. */
  theme: string;
  /** Every theme, as `store.THEMES` states them. */
  themes: readonly { name: string; label: string; hint: string }[];
  /** Whether this run serves voice at all. */
  voiceEnabled: boolean;
  /** Whether there is an open conversation — gates the delete and export rows. */
  hasConversation: boolean;
}

/**
 * Every row this run offers, shelved in reading order.
 *
 * Where you can go, what you can do with this chat, what you can switch to, then how the app looks.
 * Navigating is what a palette is for; everything else is a second reason to open it.
 */
export function paletteRows(input: PaletteInput): PaletteRow[] {
  const row = (r: Partial<PaletteRow> & Pick<PaletteRow, "id" | "group" | "label">): PaletteRow => ({
    hint: null,
    trailing: null,
    keywords: [],
    identifier: null,
    ...r,
  });
  return [
    row({ id: "go:chat", group: GO_TO_GROUP, label: "Chat", keywords: ["go to", "open", "view", "conversation"] }),
    row({ id: "go:library", group: GO_TO_GROUP, label: "Library", keywords: ["go to", "open", "view", "models"] }),
    ...(input.voiceEnabled
      ? [
          row({
            id: "go:voice",
            group: GO_TO_GROUP,
            label: "Voice",
            keywords: ["go to", "open", "view", "speak", "transcribe", "tts", "stt"],
          }),
        ]
      : []),

    row({ id: "chat:new", group: CHAT_GROUP, label: "New chat", keywords: ["start", "conversation", "blank"] }),
    ...(input.hasConversation
      ? [
          row({
            id: "chat:delete",
            group: CHAT_GROUP,
            label: "Delete this conversation",
            keywords: ["remove", "clear", "discard"],
          }),
          row({
            id: "chat:export-markdown",
            group: CHAT_GROUP,
            label: "Export as Markdown",
            keywords: ["save", "download", "md", "file"],
          }),
          row({
            id: "chat:export-pdf",
            group: CHAT_GROUP,
            label: "Export as PDF",
            keywords: ["save", "download", "print", "file"],
          }),
        ]
      : []),

    // The label is the short name because that is what a reader calls it; the FULL name is the
    // identifier, which is the only thing a prefix is matched against. Both halves matter: without
    // the identifier `mlx-community/…` finds nothing, and with the identifier in the words instead,
    // every model on the shelf would answer half the alphabet.
    ...input.models.map((m) =>
      row({
        id: `model:${m.name}`,
        group: MODEL_GROUP,
        label: m.name.split("/").pop() ?? m.name,
        trailing: m.name === input.loaded ? "loaded" : m.size_human,
        // NOT "switch", though the shelf is called "Switch model": the word belongs to the mode
        // toggle, and every model on the list carrying it is what put four models above
        // "Switch to dark mode" for the query `swit`. cmdk keeps groups in the order they are
        // built, so a word shared with an earlier shelf outranks a better match on a later one
        // whatever it scores — the vocabulary has to be exclusive, not just correct.
        keywords: ["model", "load", "run"],
        identifier: m.name,
      }),
    ),

    ...input.recents.map((c) =>
      row({ id: `recent:${c.id}`, group: RECENTS_GROUP, label: c.title, keywords: ["chat", "conversation", "recent"] }),
    ),

    row({
      id: "view:sidebar",
      group: VIEW_GROUP,
      label: "Toggle sidebar",
      keywords: ["panel", "rail", "navigation", "collapse", "expand", "hide", "show"],
    }),
    row({
      id: "view:chat-settings",
      group: VIEW_GROUP,
      label: "Toggle chat settings",
      keywords: ["panel", "rail", "parameters", "sampling", "tools", "collapse", "expand"],
    }),
    row({
      id: "view:mode",
      group: VIEW_GROUP,
      label: input.dark ? SWITCH_TO_LIGHT_LABEL : SWITCH_TO_DARK_LABEL,
      hint: "Every theme is designed for both",
      // Both grounds, on the one row, because the row offers the ground you are NOT in and a reader
      // types the one they are thinking about — which is as often the one they are already looking at.
      keywords: ["light", "dark", "mode", "ground", "appearance", "switch", "toggle"],
    }),
    // Not under "Go to": settings is a dialog over the current surface, not a destination — running
    // this leaves you exactly where you were.
    row({
      id: "view:settings",
      group: VIEW_GROUP,
      label: "Open settings",
      keywords: ["preferences", "options", "configuration"],
    }),

    ...input.themes.map((t) =>
      row({
        id: `theme:${t.name}`,
        group: THEME_GROUP,
        label: t.label,
        // The hint is matched as well as shown, so "phosphor" finds Terminal — a theme is picked by
        // what it looks like far more often than by its name. It ranks BELOW the keywords, which is
        // what stops a long description from answering a question about something else entirely.
        hint: t.hint,
        trailing: t.name === input.theme ? "current" : null,
        keywords: ["theme", "colour", "color", "palette", "appearance"],
      }),
    ),
  ];
}

/** One shelf as the renderer lays it out: the heading, and what sits under it. */
export interface PaletteShelf {
  group: string;
  rows: PaletteRow[];
}

/** The rows shelved, keeping the order `paletteRows` built them in — never re-sorted here. */
export function paletteShelves(rows: PaletteRow[]): PaletteShelf[] {
  const shelves = new Map<string, PaletteRow[]>();
  for (const r of rows) {
    const known = shelves.get(r.group);
    if (known === undefined) shelves.set(r.group, [r]);
    else known.push(r);
  }
  return [...shelves].map(([group, grouped]) => ({ group, rows: grouped }));
}

/**
 * How well one row answers what has been typed: 1 for the row being named, 0 for one that is not.
 *
 * THE TIERS ARE "HOW DIRECTLY IS THIS ROW BEING NAMED" — its id, then the start of its name, then a
 * word inside its name, then anywhere in its name, then its own vocabulary, then the line beside it.
 * cmdk sorts on this number, so it is the whole of what decides which row Return would take.
 *
 * KEYWORDS OUTRANK THE HINT, and that ordering is load-bearing. `dark` is a word the mode row exists
 * for and a word that merely occurs in the Default theme's description; ranking a row's own
 * vocabulary above a sentence that happens to contain the token is what puts the toggle first.
 *
 * WORDS ARE MATCHED PER TOKEN, so "dark mode" and "mode dark" reach the same row and neither needs
 * the order the label happens to use. Nothing here matches by scattering a query's letters through a
 * sentence, which is what makes "Nothing matches that" a state a reader can actually reach.
 */
export function paletteScore(row: PaletteRow, query: string): number {
  const search = query.trim().toLowerCase();
  if (search === "") return 1;
  const identifier = row.identifier?.toLowerCase() ?? null;
  if (identifier !== null && identifier.startsWith(search)) return 1;
  const tokens = search.split(/\s+/).filter((t) => t !== "");
  // The id is read out of the words wherever it appears among them, so a machine spelling is matched
  // by the rule above and by nothing else. Words that happen to spell part of one are not a match.
  const label = prose(row.label, row.identifier);
  if (label.startsWith(search)) return 0.9;
  if (label !== "" && tokens.every((t) => wordsOf(label).some((w) => w.startsWith(t)))) return 0.8;
  if (label !== "" && tokens.every((t) => label.includes(t))) return 0.7;
  const vocabulary = row.keywords.join(" ").toLowerCase();
  if (vocabulary !== "" && tokens.every((t) => vocabulary.includes(t))) return 0.6;
  const hint = prose(row.hint ?? "", row.identifier);
  if (hint !== "" && tokens.every((t) => hint.includes(t))) return 0.5;
  return 0;
}

/** One of a row's strings as words, or nothing at all when the string IS the row's id. */
function prose(text: string, identifier: string | null): string {
  return text === identifier ? "" : text.toLowerCase();
}

/** One label's words, as a reader would point at them — punctuation is a boundary, not a letter. */
function wordsOf(text: string): string[] {
  return text.split(/[^\p{Letter}\p{Number}]+/u).filter((w) => w !== "");
}

/**
 * The scorer cmdk filters the list with, bound to the rows this run offers.
 *
 * cmdk hands its filter the item's `value` and the search string and nothing else, so the row being
 * asked about is looked up by that value. Rows use their `id` as their value for exactly this
 * reason: it is unique, stable, and carries none of the prose the default filter would have chewed
 * through. cmdk normalises a value it stores, so the lookup does too.
 */
export function paletteFilter(rows: PaletteRow[]): (value: string, search: string) => number {
  const byValue = new Map(rows.map((r) => [r.id.trim().toLowerCase(), r]));
  return (value, search) => {
    const row = byValue.get(value.trim().toLowerCase());
    return row === undefined ? 0 : paletteScore(row, search);
  };
}
