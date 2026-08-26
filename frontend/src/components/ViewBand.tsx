/**
 * The title band the data views wear: a tinted strip carrying the view's name and one chip
 * summarising what is in it.
 *
 * SCOPED TO LIBRARY AND VOICE ON PURPOSE. It is chrome for a dense view — a grid of cards, a
 * rack of panels — where a fixed header telling you where you are and how much is here earns
 * its row. Across a chat the same band would sit above the transcript competing with it for
 * attention on every scroll, so the chat's top bar stays as it is.
 *
 * NOT A SECOND TITLE. Both views already rendered their own heading and summary; this replaces
 * those rather than sitting above them, which is the whole point — one title, in the same place,
 * on both surfaces.
 *
 * Rendered *outside* each view's scroll container (a sibling of it, not a child), so it stays put
 * while the content moves under it. The tint is `--primary` at low alpha, so the band picks up
 * whichever theme is on rather than naming a colour of its own.
 */
export function ViewBand({ title, chip }: { title: string; chip?: React.ReactNode }) {
  return (
    <div className="shrink-0 border-b border-primary/15 bg-primary/5">
      <div className="mx-auto flex w-full max-w-5xl items-center gap-3 px-6 py-2.5">
        <h1 className="text-sm font-semibold tracking-tight">{title}</h1>
        {chip != null && (
          <span className="min-w-0 truncate rounded-full border border-border bg-background/60 px-2 py-0.5 text-[11px] tabular-nums text-muted-foreground">
            {chip}
          </span>
        )}
      </div>
    </div>
  );
}
