import { useCallback, useEffect, useState } from "react";

import { loadPalette, loadTheme, savePalette, saveTheme, type Theme, type ThemePalette } from "@/lib/store";

/**
 * The two independent theme axes, both persisted and both applied to <html>:
 * light/dark as the `dark` class, and the named palette as `data-theme`.
 * Geometry (`--radius`) is deliberately not an axis — a theme that changed it
 * would be a second design rather than a second palette.
 */
export function useTheme(): {
  theme: Theme;
  toggle: () => void;
  palette: ThemePalette;
  setPalette: (p: ThemePalette) => void;
} {
  const [theme, setTheme] = useState<Theme>(() => loadTheme());
  const [palette, setPaletteState] = useState<ThemePalette>(() => loadPalette());

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    saveTheme(theme);
  }, [theme]);

  useEffect(() => {
    const root = document.documentElement;
    if (palette === "default") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", palette);
    savePalette(palette);
  }, [palette]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle, palette, setPalette: setPaletteState };
}
