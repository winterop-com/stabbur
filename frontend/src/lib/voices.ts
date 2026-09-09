import type { Voice } from "@/api";

/** The group a model voice is filed under in a picker (a Kokoro preset goes under its language). */
export const MODEL_VOICES_GROUP = "Model voices";

/**
 * Listen voices grouped for a `<select>`: model voices first, then Kokoro's presets by language.
 *
 * A model voice is a whole TTS model, not one of Kokoro's presets, so it belongs in its own group
 * rather than filed under a language it may not even declare. It goes first because there are a
 * handful of them against 54 presets: appended last they sat below a screenful of scrolling and
 * read as missing entirely.
 */
export function groupVoices(voices: Voice[]): [string, Voice[]][] {
  const groups = voices.reduce<Record<string, Voice[]>>((acc, v) => {
    const group = v.engine === "kokoro" ? v.language || "Other" : MODEL_VOICES_GROUP;
    (acc[group] ??= []).push(v);
    return acc;
  }, {});
  // `.sort` on the fresh array `Object.entries` returns; the project's tsc target has no `toSorted`.
  return Object.entries(groups).sort(
    ([a], [b]) => Number(b === MODEL_VOICES_GROUP) - Number(a === MODEL_VOICES_GROUP),
  );
}

/** The text of one `<option>`: the label, plus a gender mark only where the voice declares one. */
export function voiceOptionLabel(v: Voice): string {
  return v.gender ? `${v.label} · ${v.gender === "female" ? "F" : "M"}` : v.label;
}

/** The voice a picker's value resolves to, or undefined for an id the backend did not list. */
export function findVoice(voices: Voice[], id: string | null | undefined): Voice | undefined {
  return id ? voices.find((v) => v.id === id) : undefined;
}

/** Parse a seed field: an integer, or null for blank/invalid (a seed is a whole number, never a fraction). */
export function parseSeed(raw: string): number | null {
  const s = raw.trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isInteger(n) && n >= 0 ? n : null;
}
