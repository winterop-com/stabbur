import { useCallback, useEffect, useState } from "react";

import { loadMode, loadTheme, saveMode, saveTheme, type Mode, type Theme } from "@/lib/store";

/**
 * The two independent appearance axes, both persisted and both applied to <html>:
 * the **mode** (light/dark) as the `dark` class, and the **theme** (the named colour
 * set) as `data-theme`. Neither knows about the other — picking Paper does not decide
 * light or dark, and switching to dark does not decide Paper. These are the names the
 * screen uses, so the code and the copy say the same thing.
 * Geometry (`--radius`) is deliberately not an axis — a theme that changed it
 * would be a second design rather than a second set of colours.
 */
export function useTheme(): {
  mode: Mode;
  toggleMode: () => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
} {
  const [mode, setMode] = useState<Mode>(() => loadMode());
  const [theme, setThemeState] = useState<Theme>(() => loadTheme());

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", mode === "dark");
    saveMode(mode);
  }, [mode]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "default") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    saveTheme(theme);
  }, [theme]);

  const toggleMode = useCallback(() => {
    setMode((m) => (m === "dark" ? "light" : "dark"));
  }, []);

  return { mode, toggleMode, theme, setTheme: setThemeState };
}
