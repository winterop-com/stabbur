// Client-side conversation export. Everything needed is already in the browser
// (the conversation lives in React state / localStorage), so both formats are
// produced without a round-trip:
//
//   * Markdown  — serialized from conversation state (source-form; shareable).
//   * PDF       — a self-contained HTML document opened in a new window, printed
//                 via the browser's native "Save as PDF". Decoupled from the app
//                 layout so the resizable panels / theme never leak into the file.
//
// react-dom/server (for rendering message Markdown to static HTML) is imported
// lazily inside the PDF path so it stays out of the main bundle.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { common } from "lowlight";
// The github (light) highlight theme, inlined as a string for the print window.
import hljsCss from "highlight.js/styles/github.css?inline";

import { renderMermaid } from "@/lib/mermaid";
import type { Settings } from "@/lib/store";
import type { ChatMessage, Conversation } from "@/lib/types";

/** A filesystem-safe slug from a conversation title. */
function slug(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "chat"
  );
}

/** yyyy-mm-dd for filenames / headers (local time). */
function isoDate(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Human display name for a role. */
function roleLabel(role: ChatMessage["role"]): string {
  if (role === "user") return "You";
  if (role === "assistant") return "Assistant";
  return role.charAt(0).toUpperCase() + role.slice(1);
}

/**
 * The model(s) that produced this conversation, from the per-message stamps —
 * distinct, in order of first use, joined for the header. Falls back to the
 * currently-loaded model for conversations saved before stamping existed.
 */
function conversationModel(conv: Conversation, fallback: string | null): string | null {
  const used = [...new Set(conv.messages.filter((m) => m.role === "assistant" && m.model).map((m) => m.model))];
  return used.length ? used.join(", ") : fallback;
}

/** Non-default sampling settings, as short "key value" fragments (empty if none). */
function samplingSummary(s: Settings): string[] {
  const out: string[] = [];
  if (s.temperature != null) out.push(`temperature ${s.temperature}`);
  if (s.topP != null) out.push(`top_p ${s.topP}`);
  if (s.maxTokens != null) out.push(`max_tokens ${s.maxTokens}`);
  if (s.contextLength != null) out.push(`context ${s.contextLength}`);
  return out;
}

/** Trigger a client-side file download of some in-memory data. */
function downloadFile(filename: string, data: BlobPart, mime: string): void {
  const url = URL.createObjectURL(new Blob([data], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Serialize a conversation to shareable Markdown: a title + metadata header
 * (model, date, system prompt, non-default sampling) followed by each turn.
 * Assistant content is already Markdown and is emitted verbatim (code fences
 * intact); attachments become inline notes (data URLs would bloat the file).
 */
export function conversationToMarkdown(conv: Conversation, model: string | null): string {
  const out: string[] = [`# ${conv.title || "Conversation"}`, ""];

  const modelLabel = conversationModel(conv, model);
  const meta: string[] = [];
  if (modelLabel) meta.push(`- **Model:** ${modelLabel}`);
  meta.push(`- **Exported:** ${isoDate(Date.now())}`);
  meta.push(`- **Messages:** ${conv.messages.filter((m) => m.role !== "system").length}`);
  const sampling = samplingSummary(conv.settings);
  if (sampling.length) meta.push(`- **Sampling:** ${sampling.join(", ")}`);
  out.push(...meta, "");

  const sys = (conv.settings.systemPrompt ?? "").trim();
  if (sys) out.push("**System prompt:**", "", "```text", sys, "```", "");

  out.push("---", "");

  for (const m of conv.messages) {
    if (m.role === "system") continue; // captured in the header
    out.push(`### ${roleLabel(m.role)}`, "");

    const atts: string[] = [];
    for (const _ of m.images ?? []) atts.push("_[image attached]_");
    for (const _ of m.audios ?? []) atts.push("_[audio attached]_");
    if (atts.length) out.push(atts.join(" "), "");

    if (m.reasoning?.trim()) {
      out.push("<details><summary>Thinking</summary>", "", m.reasoning.trim(), "", "</details>", "");
    }

    for (const t of m.tools ?? []) {
      out.push(`> **${t.kind === "call" ? "Tool call" : "Tool result"}:** ${t.detail}`);
    }
    if (m.tools?.length) out.push("");

    if (m.content.trim()) out.push(m.content.trim(), "");
    if (m.error) out.push("_(the model runtime returned an error for this turn)_", "");

    out.push("---", "");
  }

  return out.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd() + "\n";
}

/** Export the conversation as a downloaded `.md` file. */
export function exportConversationMarkdown(conv: Conversation, model: string | null): void {
  const md = conversationToMarkdown(conv, model);
  downloadFile(`stabbur-${slug(conv.title)}-${isoDate(Date.now())}.md`, md, "text/markdown;charset=utf-8");
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Render one message's Markdown to a static HTML string (highlighted code, and
 * ```mermaid fences rendered to inline SVG). Mermaid blocks are pulled out to
 * placeholders, the Markdown is rendered, then each placeholder is replaced with
 * its rendered (light-themed) diagram — falling back to the source on error.
 */
async function markdownToHtml(content: string): Promise<string> {
  const { renderToStaticMarkup } = await import("react-dom/server");
  const diagrams: string[] = [];
  const token = (i: number) => `heimmermaidplaceholder${i}stabbur`;
  const src = content.replace(/```mermaid[^\n]*\n([\s\S]*?)```/g, (_m, code: string) => {
    const i = diagrams.length;
    diagrams.push(code);
    return `\n\n${token(i)}\n\n`;
  });

  let html = renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight, { languages: common }]]}>
      {src}
    </ReactMarkdown>,
  );

  for (let i = 0; i < diagrams.length; i++) {
    let block: string;
    try {
      block = `<div class="mermaid">${await renderMermaid(diagrams[i], false)}</div>`; // PDF is light-themed
    } catch {
      block = `<pre><code>${escapeHtml(diagrams[i])}</code></pre>`;
    }
    html = html.replace(`<p>${token(i)}</p>`, block).replace(token(i), block);
  }
  return html;
}

// Print-document stylesheet: a clean, light, paper-friendly layout independent
// of the app's theme. Paired with the inlined github (light) highlight theme.
const PRINT_CSS = `
  *{ box-sizing: border-box; }
  /* Zero page margin removes the browser's default header/footer (date, URL,
     page numbers); real margins come from the body padding below. */
  @page{ margin: 0; }
  html,body{ margin:0; }
  body{ font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color:#1a1a1a; padding:16mm 18mm; }
  /* Drop empty code boxes left by unterminated fences in model output. */
  .content pre:empty, .content pre:has(> code:empty){ display:none; }
  .content .mermaid{ text-align:center; margin:.75rem 0; break-inside:avoid; }
  .content .mermaid svg{ max-width:100%; height:auto; }
  header.doc{ border-bottom:1px solid #e2e2e2; padding-bottom:1rem; margin-bottom:1.5rem; }
  header.doc h1{ font-size:1.5rem; margin:0 0 .5rem; }
  header.doc .meta{ color:#666; font-size:.8rem; }
  header.doc .meta span{ margin-right:1rem; white-space:nowrap; }
  header.doc pre.sys{ background:#f6f8fa; border:1px solid #e2e2e2; border-radius:6px;
        padding:.6rem .8rem; font-size:.8rem; white-space:pre-wrap; margin:.75rem 0 0; }
  /* No blanket break-inside:avoid on a turn — a turn taller than the remaining
     page would jump wholesale to the next page and leave a big white gap. Let
     turns flow; only keep the role label with its content and images unsplit. */
  section.turn{ margin:1.25rem 0; }
  .role{ font-size:.7rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em;
        color:#888; margin-bottom:.35rem; break-after:avoid; }
  section.user .content{ background:#f3f4f6; border-radius:10px; padding:.6rem .9rem; display:inline-block; }
  .content p:first-child{ margin-top:0; } .content p:last-child{ margin-bottom:0; }
  .content pre{ background:#f6f8fa; border:1px solid #e6e6e6; border-radius:6px;
        padding:.7rem .9rem; overflow-x:auto; font-size:.85em; }
  .content code{ font-family:"SF Mono", ui-monospace, Menlo, monospace; }
  .content :not(pre) > code{ background:#f0f1f2; border-radius:4px; padding:.1em .35em; font-size:.85em; }
  .content table{ border-collapse:collapse; } .content th,.content td{ border:1px solid #ddd; padding:.3rem .6rem; }
  .content blockquote{ border-left:3px solid #ddd; margin:.5rem 0; padding-left:.8rem; color:#555; }
  img.att{ max-width:16rem; max-height:16rem; border-radius:8px; border:1px solid #e2e2e2; margin:.25rem .25rem 0 0; break-inside:avoid; }
  .note{ color:#888; font-size:.8rem; font-style:italic; }
  .tool{ color:#555; font-size:.8rem; border-left:3px solid #cbd5e1; padding-left:.6rem; margin:.25rem 0; }
  details.think{ color:#666; font-size:.85rem; margin:.35rem 0; }
  details.think .think-body{ white-space:pre-wrap; border-left:3px solid #eee; padding-left:.8rem; margin-top:.4rem; }
  @page{ margin:1.5cm; }
`;

/**
 * Export the conversation as a PDF: build a self-contained HTML document and
 * open it in a new window, then invoke the browser's print dialog ("Save as
 * PDF"). If a popup blocker prevents the window, fall back to downloading the
 * HTML so the transcript is never lost.
 */
export async function exportConversationPdf(conv: Conversation, model: string | null): Promise<void> {
  const sections: string[] = [];
  for (const m of conv.messages) {
    if (m.role === "system") continue;
    const imgs = (m.images ?? []).map((src) => `<img class="att" src="${src}" alt="attachment" />`).join("");
    const audioNote = (m.audios ?? []).length ? `<p class="note">[audio attached]</p>` : "";
    // Expanded (open) so the reasoning is actually visible on paper — a collapsed
    // disclosure is useless in a static PDF.
    const reasoning = m.reasoning?.trim()
      ? `<details class="think" open><summary>Thinking</summary><div class="think-body">${escapeHtml(m.reasoning.trim())}</div></details>`
      : "";
    const tools = (m.tools ?? [])
      .map((t) => `<p class="tool"><strong>${t.kind === "call" ? "Tool call" : "Tool result"}:</strong> ${escapeHtml(t.detail)}</p>`)
      .join("");
    const body = m.content.trim() ? `<div class="content">${await markdownToHtml(m.content)}</div>` : "";
    const err = m.error ? `<p class="note">(the model runtime returned an error for this turn)</p>` : "";
    sections.push(
      `<section class="turn ${m.role}"><div class="role">${roleLabel(m.role)}</div>${imgs}${audioNote}${reasoning}${tools}${body}${err}</section>`,
    );
  }

  const modelLabel = conversationModel(conv, model);
  const metaBits: string[] = [];
  if (modelLabel) metaBits.push(`<span><strong>Model:</strong> ${escapeHtml(modelLabel)}</span>`);
  metaBits.push(`<span><strong>Exported:</strong> ${isoDate(Date.now())}</span>`);
  const sys = (conv.settings.systemPrompt ?? "").trim();
  const sysBlock = sys ? `<pre class="sys">${escapeHtml(sys)}</pre>` : "";

  const html =
    `<!doctype html><html><head><meta charset="utf-8" />` +
    `<title>${escapeHtml(conv.title || "Conversation")}</title>` +
    `<style>${hljsCss}\n${PRINT_CSS}</style></head><body>` +
    `<header class="doc"><h1>${escapeHtml(conv.title || "Conversation")}</h1>` +
    `<div class="meta">${metaBits.join("")}</div>${sysBlock}</header>` +
    sections.join("") +
    `</body></html>`;

  const w = window.open("", "_blank");
  if (!w) {
    // Popup blocked: download the HTML instead (user can open + print it).
    downloadFile(`stabbur-${slug(conv.title)}-${isoDate(Date.now())}.html`, html, "text/html;charset=utf-8");
    return;
  }
  w.document.open();
  w.document.write(html);
  w.document.close();
  const print = () => {
    w.focus();
    w.print();
  };
  // Wait for images/layout before printing.
  if (w.document.readyState === "complete") setTimeout(print, 300);
  else w.addEventListener("load", () => setTimeout(print, 200));
}
