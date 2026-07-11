// Guards that the harness sends the SAME page-context block the extension produces:
// buildContextBlock must equal the extension's formatPageContext for both modes.
// If someone changes one without the other, the verified prompts stop reflecting
// reality and this fails.

import { test, expect } from "@playwright/test";
import { formatPageContext } from "../../lib/pageContext";
import { buildContextBlock, buildUserContent } from "./context";
import type { Capture } from "./capture";

const cap: Capture = {
  key: "sample",
  url: "https://example.com/article",
  title: "Example Article",
  pageText: "First line of visible page text.\nSecond line.",
  selection: "A selected sentence.",
};

test("page-text mode block equals formatPageContext (page text, no selection)", () => {
  const expected = formatPageContext({ url: cap.url, title: cap.title, selection: "", pageText: cap.pageText });
  const block = buildContextBlock("page-text", cap);
  expect(block).toBe(expected);
  expect(block).toContain("Page URL: https://example.com/article");
  expect(block).toContain("Page text (truncated):");
  expect(block).not.toContain("Selected text:");
});

test("selection mode block equals formatPageContext (selection, no page text)", () => {
  const expected = formatPageContext({ url: cap.url, title: cap.title, selection: cap.selection, pageText: "" });
  const block = buildContextBlock("selection", cap);
  expect(block).toBe(expected);
  expect(block).toContain("Selected text:");
  expect(block).toContain("A selected sentence.");
  expect(block).not.toContain("Page text (truncated):");
});

test("buildUserContent prepends the context block then the prompt", () => {
  const content = buildUserContent("page-text", cap, "Summarize this.");
  expect(content.endsWith("\n\nSummarize this.")).toBe(true);
  expect(content.startsWith("Page URL:")).toBe(true);
});
