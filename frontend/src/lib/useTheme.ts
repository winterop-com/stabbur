import { useCallback, useEffect, useState } from "react";

import { loadTheme, saveTheme, type Theme } from "@/lib/store";

/** Toggle `class="dark"` on <html>, persisted in localStorage (default dark). */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(() => loadTheme());

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    saveTheme(theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}
