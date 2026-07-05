import { useEffect, useState } from "react";

/**
 * True on narrow (phone) widths, tracked live via `matchMedia`. Used to switch the
 * resizable sidebar for an overlay drawer, where a resizable rail would squeeze the
 * content. Defaults to Tailwind's `md` breakpoint (768px).
 */
export function useIsMobile(breakpoint = 768): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth < breakpoint,
  );

  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, [breakpoint]);

  return isMobile;
}
