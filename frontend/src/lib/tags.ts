import type { CSSProperties } from "react";

// Shared tag helpers for the Models view and the in-composer model picker.

/** Normalize a free-text tag to a slug (lowercase, dashes, <=32 chars). */
export function normalizeTag(t: string): string {
  return t
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32);
}

// A tag's color is *derived* from its name (stable hash -> fixed palette), so every
// "tested" is the same color everywhere with zero storage. Full class strings (not
// built dynamically) so Tailwind keeps them.
//
// THE ONE DOCUMENTED EXCEPTION TO "EVERY COLOUR IS A VARIABLE" (docs/ui-conventions.md). A tag is
// a name a user invented, so no theme can hold a token for it and the hue has to be computed —
// a token per tag is a token per string anyone might type. The constraint that comes with the
// exception is contrast, and it is what these shade numbers are:
//
//   - LIGHT ink is the ramp's 700 step, not 600. At 600 seven of the eleven hues fell under
//     4.5:1 on the default light ground (lime 3.06, amber 3.19, teal 3.66, cyan 3.60...); at 700
//     the worst of the eleven is 4.96, which is the bar `--good-ink` (4.61) and `--warning-ink`
//     (4.65) meet. Uniform across the ramp on purpose: a per-hue table of steps is a table that
//     drifts, and one rule ("700 light, 400 dark") is checkable by reading it.
//   - DARK ink is the 400 step, the lightest of the eleven landing at 6.71:1 on the dark ground.
//
// The border and background stay at 500 — a fill has nothing to read against it.
const TAG_PALETTE = [
  "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
  "border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-400",
  "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  "border-lime-500/30 bg-lime-500/10 text-lime-700 dark:text-lime-400",
  "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  "border-teal-500/30 bg-teal-500/10 text-teal-700 dark:text-teal-400",
  "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400",
  "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
  "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-400",
  "border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-400",
  "border-pink-500/30 bg-pink-500/10 text-pink-700 dark:text-pink-400",
];

/** Stable border/bg/text classes for a tag, derived from its name. */
export function tagColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return TAG_PALETTE[h % TAG_PALETTE.length];
}

/** A tag's first-class style from the registry (GET /api/tags/registry), keyed by tag name. */
export interface TagMeta {
  color?: string;
  icon?: string;
  description?: string;
}
export type TagRegistry = Record<string, TagMeta>;

/**
 * How to render a tag: a registered hex color becomes inline styles (border/bg/text derived from
 * it); otherwise fall back to the name-derived palette classes. `icon` is the registered glyph, if any.
 */
export function tagStyle(
  name: string,
  registry?: TagRegistry,
): { className: string; style?: CSSProperties; icon?: string } {
  const meta = registry?.[name];
  if (meta?.color) {
    return {
      className: "border",
      style: { borderColor: `${meta.color}55`, backgroundColor: `${meta.color}1a`, color: meta.color },
      icon: meta.icon,
    };
  }
  return { className: tagColor(name), icon: meta?.icon };
}

/** The sorted union of all tags across a set of models. */
export function allTagsOf(models: { tags?: string[] }[]): string[] {
  const s = new Set<string>();
  for (const m of models) for (const t of m.tags ?? []) s.add(t);
  return [...s].sort();
}
