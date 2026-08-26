import { useEffect, useState } from "react";

/**
 * True below `breakpoint`, tracked live via `matchMedia`. Used to swap a resizable rail for an
 * overlay where the rail would squeeze the content instead of sharing it — each caller passes the
 * width *its* rail stops being usable at, since a list of chat titles and a column of labelled
 * sliders need very different room. Defaults to Tailwind's `md` breakpoint (768px).
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
