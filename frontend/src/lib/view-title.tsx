import { createContext, useContext, useEffect, useState } from "react";

/**
 * What the top bar says on its left for whichever surface is on screen.
 *
 * ONE STRIP, NOT TWO — and this is what makes that possible. stabbur used to draw a `ViewBand` inside
 * Library and Voice: a titled strip immediately below a top bar whose left half was an empty div.
 * Two stacked bands, the upper one blank, which on a wide display reads as the app having no top bar
 * at all. (The status bar had the identical fault at the other end of the window and was fixed the
 * same way.) The title is chrome, so it belongs in the chrome; but only the view knows what it is
 * called and how much is in it, so the view publishes it upward and the shell renders it.
 *
 * KEYED BY THE VIEW THAT PUBLISHED IT. A parent's effects run after its children's, so a shell that
 * simply cleared this on every navigation would clear the incoming view's title just after it was
 * set. Instead the record says which surface it describes and the shell ignores one that is not the
 * surface on screen, which is correct in every ordering rather than in the lucky one.
 *
 * The chip is a STRING, not a node: it goes into an effect's dependencies, and a fresh element every
 * render would republish forever.
 */
export interface ViewTitle {
  /** The surface this describes, so a stale record from the previous view is simply ignored. */
  view: string;
  title: string;
  /** The one-line summary beside the title — a count, a total — or null when there is nothing to say. */
  chip: string | null;
}

const PublishContext = createContext<(t: ViewTitle) => void>(() => {});

/** Wraps the surfaces that publish a title. The shell owns the state and renders the result. */
export function ViewTitleProvider({
  publish,
  children,
}: {
  publish: (t: ViewTitle) => void;
  children: React.ReactNode;
}) {
  return <PublishContext.Provider value={publish}>{children}</PublishContext.Provider>;
}

/** State + setter for the shell: `title` is what to render, `publish` is what the provider carries. */
export function useViewTitleState(): [ViewTitle | null, (t: ViewTitle) => void] {
  const [title, setTitle] = useState<ViewTitle | null>(null);
  return [title, setTitle];
}

/** Called by a view to say what the top bar should read while it is on screen. */
export function usePublishViewTitle(view: string, title: string, chip: string | null): void {
  const publish = useContext(PublishContext);
  useEffect(() => {
    publish({ view, title, chip });
  }, [publish, view, title, chip]);
}
