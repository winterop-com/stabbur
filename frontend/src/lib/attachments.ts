// Turning dropped / pasted / picked files into composer attachments. This lives
// outside the component because the work is asynchronous and format-specific
// (PDF text extraction, page rendering, image downscaling), and because pdf.js is
// a megabyte of parser we only want to fetch when someone actually attaches a PDF.
//
// The contract with the composer: every file either becomes an Attachment or
// produces a note explaining why it didn't. Nothing is ever dropped in silence.

import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import type { Attachment } from "@/lib/types";

/** Which attachment kinds the loaded model accepts. `known` is false while the
 *  model's capabilities are still resolving — attachments are then accepted
 *  optimistically rather than dropped as "unsupported". */
export interface Accept {
  image: boolean;
  audio: boolean;
  known: boolean;
}

/**
 * Pixel budget for an image we hand to a model. Vision encoders tile their input
 * down to a few hundred pixels per patch, so a 12 MP phone photo buys nothing —
 * it just costs upload time and, because a data URL is base64, 4/3 of its bytes
 * in localStorage and in the request body. 2 MP is past the point any current
 * encoder resolves.
 */
export const MAX_IMAGE_MEGAPIXELS = 2;

/** Pages we're willing to rasterize from one PDF. Each page is a full image the
 *  model must encode, so a 200-page report as images would blow any context. */
const MAX_PDF_IMAGE_PAGES = 10;

/** Ceiling on inlined file text (~50k tokens). Past this the prompt is the problem,
 *  not the attachment, so truncate visibly rather than let the request fail. */
const MAX_TEXT_CHARS = 200_000;

// Text/doc files we inline into the prompt (many carry an empty MIME type, so we
// also match by extension). These work with any model, not just multimodal ones.
const TEXT_EXT =
  /\.(txt|text|md|markdown|rst|json|jsonl|ndjson|csv|tsv|log|ya?ml|toml|ini|cfg|conf|env|xml|html?|css|scss|py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|java|kt|kts|scala|c|h|cpp|cc|cxx|hpp|cs|php|swift|sql|sh|bash|zsh|fish|ps1|r|lua|pl|pm|dart|ex|exs|clj|hs|ml|vue|svelte|tex|dockerfile|makefile|gitignore|proto|graphql|gql)$/i;
// File-picker accept hint: text/* plus the common code extensions above.
const TEXT_ACCEPT =
  "text/*,.md,.markdown,.rst,.json,.jsonl,.csv,.tsv,.log,.yaml,.yml,.toml,.ini,.cfg,.env,.xml,.html,.css,.scss,.py,.js,.jsx,.mjs,.cjs,.ts,.tsx,.go,.rs,.rb,.java,.kt,.c,.h,.cpp,.hpp,.cs,.php,.swift,.sql,.sh,.lua,.pl,.dart,.vue,.svelte,.tex";
const PDF_ACCEPT = "application/pdf,.pdf";

/** What we can do with a file. "pdf" is not a MediaKind: it becomes text or images. */
type FileKind = "image" | "audio" | "text" | "pdf" | null;

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || /\.pdf$/i.test(file.name);
}

function isTextFile(file: File): boolean {
  return (
    file.type.startsWith("text/") ||
    file.type === "application/json" ||
    file.type === "application/xml" ||
    TEXT_EXT.test(file.name)
  );
}

/** Classify a File into what we'll do with it, or null (nothing sensible). Text and
 *  PDF are always accepted (they end up in the prompt); image/audio only for models
 *  that can read them. */
function kindOf(file: File, accept: Accept): FileKind {
  // While caps are unknown (library/status still loading), accept media
  // optimistically instead of dropping it — a genuine mismatch surfaces at send.
  if (file.type.startsWith("image/") && (accept.image || !accept.known)) return "image";
  if (file.type.startsWith("audio/") && (accept.audio || !accept.known)) return "audio";
  if (isPdf(file)) return "pdf";
  if (isTextFile(file)) return "text";
  return null;
}

/** A kind the composer's attach menu can open a picker for. Not `FileKind`: this is
 *  the vocabulary a *reader* picks from, so "text" is text and code together, and
 *  "pdf" is its own entry because what heim does with one (extract, or rasterize)
 *  is worth saying before the dialog opens. */
export type PickKind = "image" | "audio" | "text" | "pdf";

/** The `accept` attribute for one kind's picker. The menu has already said which
 *  kinds the loaded model takes, so filtering happens per choice rather than on one
 *  catch-all input — the OS dialog opens already narrowed to what was asked for. */
export function acceptAttributeFor(kind: PickKind): string {
  switch (kind) {
    case "image":
      return "image/*";
    case "audio":
      return "audio/*";
    case "pdf":
      return PDF_ACCEPT;
    case "text":
      return TEXT_ACCEPT;
  }
}

/** Why a file couldn't be attached — always names the file, so a multi-file drop
 *  says which one was the problem. */
function rejectionNote(file: File): string {
  const name = file.name || "That file";
  if (file.type.startsWith("image/")) return `${name} needs a vision model — the loaded one can't see images.`;
  if (file.type.startsWith("audio/")) return `${name} needs an audio model — the loaded one can't hear audio.`;
  if (file.type.startsWith("video/")) return `${name} is a video — heim can't attach video.`;
  return `${name} isn't a supported attachment — text, code, PDF, image, and audio files work.`;
}

function readDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error("read failed"));
    r.onload = () => resolve(r.result as string);
    r.readAsDataURL(file);
  });
}

/** Draw a source onto a canvas of the given size over white, and encode as JPEG.
 *  The white fill matters: PDFs and PNGs render on transparency, which JPEG has no
 *  channel for and would otherwise flatten to black. */
function encodeCanvas(w: number, h: number, draw: (ctx: CanvasRenderingContext2D) => void): string {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(w));
  canvas.height = Math.max(1, Math.round(h));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  draw(ctx);
  return canvas.toDataURL("image/jpeg", 0.85);
}

/** Read an image, downscaling to MAX_IMAGE_MEGAPIXELS first. Images already under
 *  the cap are passed through byte-for-byte — re-encoding them would only lose
 *  detail (and alpha) for no size win. */
async function readImageUrl(file: File): Promise<string> {
  const cap = MAX_IMAGE_MEGAPIXELS * 1_000_000;
  // createImageBitmap decodes off the main thread and handles every format the
  // browser does; when it can't (exotic/broken file), fall back to the raw bytes.
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) return readDataUrl(file);
  try {
    const pixels = bitmap.width * bitmap.height;
    if (pixels <= cap) return await readDataUrl(file);
    const scale = Math.sqrt(cap / pixels);
    const w = bitmap.width * scale;
    const h = bitmap.height * scale;
    return encodeCanvas(w, h, (ctx) => ctx.drawImage(bitmap, 0, 0, Math.round(w), Math.round(h)));
  } finally {
    bitmap.close();
  }
}

/** Load pdf.js on first use. The worker is bundled through Vite and served from our
 *  own origin — heim is self-hosted and must work with no network at all, so the
 *  CDN workerSrc every pdf.js snippet shows is not an option. */
let pdfjs: Promise<typeof import("pdfjs-dist")> | null = null;
function loadPdfjs(): Promise<typeof import("pdfjs-dist")> {
  pdfjs ??= import("pdfjs-dist").then((mod) => {
    mod.GlobalWorkerOptions.workerSrc = workerUrl;
    return mod;
  });
  return pdfjs;
}

type PdfDocument = Awaited<ReturnType<Awaited<ReturnType<typeof loadPdfjs>>["getDocument"]>["promise"]>;
type PdfPage = Awaited<ReturnType<PdfDocument["getPage"]>>;

/** Concatenate a page's text, honouring pdf.js's end-of-line markers — without them
 *  every line of a document runs together into one unreadable paragraph. */
async function pageText(page: PdfPage): Promise<string> {
  const content = await page.getTextContent();
  let out = "";
  for (const item of content.items) {
    if (!("str" in item)) continue; // marked-content markers, not text
    out += item.str + (item.hasEOL ? "\n" : "");
  }
  return out;
}

/** Rasterize one page, sized to the same pixel budget as an attached image. */
async function pageImage(page: PdfPage): Promise<string> {
  const cap = MAX_IMAGE_MEGAPIXELS * 1_000_000;
  const unit = page.getViewport({ scale: 1 });
  // Never upscale past 2x: a small page rendered huge is blur, not detail.
  const scale = Math.min(2, Math.sqrt(cap / (unit.width * unit.height)));
  const viewport = page.getViewport({ scale });
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  // A PDF page paints onto transparency; JPEG has no alpha channel and would flatten
  // that to black, so the page gets an explicit white backdrop.
  await page.render({ canvas, viewport, background: "#ffffff" }).promise;
  return canvas.toDataURL("image/jpeg", 0.85);
}

async function renderPages(doc: PdfDocument, file: File, notes: string[]): Promise<Attachment[]> {
  const count = Math.min(doc.numPages, MAX_PDF_IMAGE_PAGES);
  if (doc.numPages > count) {
    notes.push(`${file.name} is ${doc.numPages} pages — only the first ${count} were attached as images.`);
  }
  const out: Attachment[] = [];
  for (let n = 1; n <= count; n++) {
    const page = await doc.getPage(n);
    out.push({ kind: "image", url: await pageImage(page), name: `${file.name} · page ${n}` });
    page.cleanup();
  }
  return out;
}

/**
 * A PDF becomes either inlined text (default — works with every model and costs a
 * fraction of the tokens) or rendered page images (vision models, when the layout
 * *is* the content: tables, charts, forms). Two automatic fallbacks keep the user
 * out of the loop: asking for images without a vision model falls back to text, and
 * a PDF with no text layer at all (a scan) falls forward to images.
 */
async function readPdf(file: File, accept: Accept, asImage: boolean, notes: string[]): Promise<Attachment[]> {
  const lib = await loadPdfjs();
  const task = lib.getDocument({ data: new Uint8Array(await file.arrayBuffer()) });
  const doc = await task.promise;
  try {
    const canSee = accept.image || !accept.known;
    if (asImage && canSee) return await renderPages(doc, file, notes);
    if (asImage) notes.push(`${file.name}: the loaded model can't see images, so its text was attached instead.`);

    let text = "";
    for (let n = 1; n <= doc.numPages; n++) {
      const page = await doc.getPage(n);
      text += `${n > 1 ? "\n\n" : ""}${await pageText(page)}`;
      page.cleanup();
      if (text.length > MAX_TEXT_CHARS) break;
    }
    if (!text.trim()) {
      // No text layer: a scan or an export of pure vector art. Rasterizing is the
      // only way to read it, so do that when the model can see — otherwise say so
      // rather than attaching an empty file the model will hallucinate around.
      if (canSee) {
        notes.push(`${file.name} has no text layer (a scan?) — its pages were attached as images.`);
        return await renderPages(doc, file, notes);
      }
      notes.push(`${file.name} has no text layer (a scan?) — a vision model could read it as images.`);
      return [];
    }
    if (text.length > MAX_TEXT_CHARS) {
      text = text.slice(0, MAX_TEXT_CHARS);
      notes.push(`${file.name} was truncated — only the first ${MAX_TEXT_CHARS.toLocaleString()} characters fit.`);
    }
    return [{ kind: "text", name: file.name, text, pages: doc.numPages }];
  } finally {
    // Tears down the document *and* the worker's copy of it; without this a big PDF
    // stays resident for the life of the tab.
    void task.destroy();
  }
}

/** An attachment batch: what we made, and a line per file we couldn't. */
export interface Prepared {
  items: Attachment[];
  notes: string[];
}

/**
 * Turn a batch of files into attachments. Files are processed in order and one
 * failure never takes the batch down — a bad file contributes a note and the rest
 * still attach.
 */
export async function prepareAttachments(
  files: File[],
  accept: Accept,
  opts: { pdfAsImage?: boolean } = {},
): Promise<Prepared> {
  const items: Attachment[] = [];
  const notes: string[] = [];
  for (const file of files) {
    try {
      switch (kindOf(file, accept)) {
        case "pdf":
          items.push(...(await readPdf(file, accept, !!opts.pdfAsImage, notes)));
          break;
        case "image":
          items.push({ kind: "image", url: await readImageUrl(file), name: file.name });
          break;
        case "audio":
          items.push({ kind: "audio", url: await readDataUrl(file), name: file.name });
          break;
        case "text": {
          const text = await file.text();
          if (text.length > MAX_TEXT_CHARS) {
            notes.push(`${file.name} was truncated — only the first ${MAX_TEXT_CHARS.toLocaleString()} characters fit.`);
          }
          items.push({ kind: "text", name: file.name, text: text.slice(0, MAX_TEXT_CHARS) });
          break;
        }
        default:
          notes.push(rejectionNote(file));
      }
    } catch {
      // Encrypted PDF, unreadable bytes, a dropped folder — say which file, not "something failed".
      notes.push(`${file.name || "That file"} couldn't be read.`);
    }
  }
  return { items, notes };
}
