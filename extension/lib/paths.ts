// Single source of truth for the same-origin path-safety rule used before a same-origin
// fetch is built in the extension's own context: rooted ("/..."), no protocol-relative
// "//", no backslash, no "..", and no scheme before the query string. bindRecipe and the
// session probe both gate injected fetches on this. The functions injected into the page
// (bindRecipe.mintInPage, sessionReads.runProbe) MUST keep an inline copy of this rule --
// executeScript serializes them, so they cannot close over this import -- and those inline
// copies MUST mirror this implementation exactly.

/** Whether `p` is a safe rooted same-origin path (see module doc). */
export function validSameOriginPath(p: string): boolean {
  if (typeof p !== "string" || !p.startsWith("/") || p.startsWith("//")) return false;
  if (p.includes("\\") || p.includes("..")) return false;
  const q = p.indexOf("?");
  const beforeQuery = q === -1 ? p : p.slice(0, q);
  return !beforeQuery.includes(":");
}
