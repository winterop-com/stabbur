// Collects lightweight page context (url, title, selection, optional page text)
// from the active tab via chrome.scripting. Injection failures (chrome:// pages,
// no host access) degrade silently to null.

export interface PageContext {
  url: string;
  title: string;
  selection: string;
  /** document.body.innerText, whitespace-collapsed and truncated; empty when not requested. */
  pageText: string;
}

const SELECTION_LIMIT = 2000;
const PAGE_TEXT_LIMIT = 8000;

/**
 * Read url/title/selection (and, when `includePageText`, the visible page text)
 * from the given tab, or null if the page is off-limits.
 */
export async function collect(tabId: number, includePageText = false): Promise<PageContext | null> {
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: (limit: number, textLimit: number, withText: boolean) => {
        const sel = window.getSelection?.()?.toString() ?? "";
        let pageText = "";
        if (withText) {
          const raw = document.body?.innerText ?? "";
          // Collapse runs of spaces/tabs to one space and runs of blank lines to a
          // single newline, keeping line structure (useful for lists) while
          // spending the character budget on content, not whitespace.
          pageText = raw
            .replace(/[^\S\n]+/g, " ")
            .replace(/\s*\n\s*/g, "\n")
            .trim()
            .slice(0, textLimit);
        }
        return {
          url: location.href,
          title: document.title,
          selection: sel.slice(0, limit),
          pageText,
        };
      },
      args: [SELECTION_LIMIT, PAGE_TEXT_LIMIT, includePageText],
    });
    const value = result?.result as PageContext | undefined;
    return value ?? null;
  } catch {
    return null;
  }
}

/** Render page context as a plain-text block to prepend to a chat turn. Ordering
 *  is url/title/selection/page text, so a selection (the user's explicit focus)
 *  precedes the broader page dump. */
export function formatPageContext(ctx: PageContext): string {
  const lines = [`Page URL: ${ctx.url}`, `Page title: ${ctx.title}`];
  if (ctx.selection.trim()) {
    lines.push(`Selected text:\n${ctx.selection}`);
  }
  if (ctx.pageText.trim()) {
    lines.push(`Page text (truncated):\n${ctx.pageText}`);
  }
  return lines.join("\n");
}
