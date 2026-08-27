// The panel's two appearance axes, applied to <html>.
//
// The shared stylesheet (frontend/src/index.css) states every palette twice — `html[data-theme=X]`
// for light and `html.dark[data-theme=X]` for dark — so a theme is only half-applied unless BOTH
// marks are written. The panel used to write only the `dark` class from the OS query, which is why
// it always rendered the unnamed default palette however the web UI was set.
//
// Deliberately NOT a hook: the very first paint happens before React mounts (main.tsx reads storage
// and stamps <html> before createRoot), so a themed panel must be expressible as a plain function.

import { DEFAULT_THEME, type Mode, type Theme } from "../../lib/settings";

/** Whether the OS is currently asking for dark. */
function prefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/**
 * Stamp the chosen theme + mode onto <html>. `default` removes the attribute rather than writing
 * "default": the base palette is the bare `:root` block, and `html[data-theme='default']` matches
 * no rule at all. Same convention as the web UI's useTheme, so the two agree about what a mark means.
 */
export function applyAppearance(theme: Theme, mode: Mode): void {
  const root = document.documentElement;
  if (theme === DEFAULT_THEME) root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  root.classList.toggle("dark", mode === "system" ? prefersDark() : mode === "dark");
}

/**
 * Keep the OS light/dark setting mirrored while `mode` is "system", and return an unsubscribe.
 *
 * Re-applies both axes rather than only the class, so one function owns the whole of what <html>
 * says. The listener stays installed under an explicit light/dark too — applyAppearance ignores
 * the query there, so an OS flip is simply a no-op instead of a case to remember to unsubscribe.
 */
export function watchSystemMode(theme: Theme, mode: Mode): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = (): void => applyAppearance(theme, mode);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
