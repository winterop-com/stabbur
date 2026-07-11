// Builds the exact context-prefixed user message the extension sends, REUSING the
// extension's own formatPageContext so the harness can never drift from the panel.
// A page-text prompt attaches the page text (selection empty); a selection prompt
// attaches the selection (page text empty) — the two extension modes.

import { formatPageContext, type PageContext } from "../../lib/pageContext";
import type { Mode } from "./catalog";
import type { Capture } from "./capture";

/** The captured page-context fields for one site. */
export function contextFor(mode: Mode, cap: Capture): PageContext {
  if (mode === "selection") {
    return { url: cap.url, title: cap.title, selection: cap.selection, pageText: "" };
  }
  return { url: cap.url, title: cap.title, selection: "", pageText: cap.pageText };
}

/** The fenced/labeled context block prepended to the prompt (extension format). */
export function buildContextBlock(mode: Mode, cap: Capture): string {
  return formatPageContext(contextFor(mode, cap));
}

/** The full user message content: context block + blank line + prompt. Mirrors
 *  ChatView.toApiMessages (`${context}\n\n${content}`). */
export function buildUserContent(mode: Mode, cap: Capture, prompt: string): string {
  const block = buildContextBlock(mode, cap);
  return block ? `${block}\n\n${prompt}` : prompt;
}
