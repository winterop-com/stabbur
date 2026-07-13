// Shared TargetBanner status strings. Both the component (components/TargetBanner.tsx) and the e2e
// specs (e2e/mock/*, e2e/live/*) import these so the asserted copy has ONE home and can't drift
// across the many spec files that used to hard-code it.

/** Collapsed green tab-state chip + the matched-tab meaning: the active tab is under the target's
 *  base_url. Short by design — it sits inline in the compact header. */
export const TAB_MATCHED = "matches this tab";

/** Collapsed muted tab-state chip when the active tab's target can't be determined (no tab URL yet). */
export const TAB_UNKNOWN = "tab unknown";

/** The full amber comparison shown only in the expanded mismatch detail. */
export const TAB_MISMATCH_DETAIL = "This tab does not match the assistant target.";

/** Collapsed neutral one-liner on an unrelated page ("Not a <name> page."). Kept verbatim from the
 *  single-target banner so nothing shouts the assistant's product name at every site. */
export function tabMismatchOneLiner(name: string): string {
  return `Not a ${name} page.`;
}
