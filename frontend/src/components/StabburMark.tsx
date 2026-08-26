/**
 * The stabbur mark: wide overhanging roof, raised body, two pillars with air beneath.
 *
 * Same geometry as `public/favicon.svg`, minus its dark rounded-square plate — inline in the
 * chrome the mark sits on the sidebar's own ground, so a plate would read as a sticker stuck
 * to the rail. Fills with `currentColor` so it takes the colour of whatever it sits next to
 * and needs no per-theme variant.
 *
 * Drawn rather than raster: the source logo is a 1254px PNG with the cream background baked in,
 * which at wordmark size mushes and cannot sit on a dark rail.
 */
export function StabburMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true" className={className}>
      <path d="M16 4 L30 13.5 H2 Z" />
      <rect x="6.5" y="14.5" width="19" height="7" />
      <rect x="9" y="21.5" width="3.5" height="6.5" />
      <rect x="19.5" y="21.5" width="3.5" height="6.5" />
    </svg>
  );
}
