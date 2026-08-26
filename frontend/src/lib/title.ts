// Naming a conversation with the model, once the first exchange is over.
//
// `deriveTitle` (lib/store) takes the first 40 characters of what you typed, which is a placeholder
// dressed as a name: a 240px rail truncates it again, so "analyze these images…" arrives as "anal…",
// and a first message carrying only a picture had no text to slice at all — every one of those
// conversations was called "Attachment", permanently. The model that just answered the message
// already knows what it was about, so it is asked.
//
// THE ONE HARD CONSTRAINT IS WHICH MODEL. The request names the model that is *already loaded* and
// no other. An upstream llama-server in router mode runs `max_instances: 1` and heim's own
// ServerManager is single-runtime, so naming a chat with a second model would evict the one being
// chatted with, and the next message would evict it back — tens of gigabytes of weights swapped to
// produce five words. The caller passes the name from /api/status; there is no fallback and no
// "pick a small one" path, because there is no such thing as a free second model here.
//
// WHY /v1 AND NOT /api/chat. No tools are wanted (naming a chat is not a task the assistant should
// be able to run a shell for), and /api/chat would drag the project's system prompt and MCP
// toolset into a request that wants neither. /v1 is the transparent proxy — a plain
// request/response against the loaded runtime, nothing injected. See docs/guides/api.md.
//
// FAILURE IS SILENT, ALWAYS. Every path out of here is a title or `null`: no model, an unreachable
// server, a 409 because the runtime was unloaded between the reply and this call, a timeout, a
// refusal, an empty string, junk. The conversation keeps its derived title and the user is told
// nothing, because a chat that is called "analyze these ima…" instead of "Image analysis" is not a
// fault worth a toast.

import { apiFetch } from "@/lib/http";
import type { Conversation } from "@/lib/types";

/**
 * The instruction, held as one constant and never interpolated into.
 *
 * llama.cpp caches the longest common token prefix between requests, and this text is the prefix of
 * every title request the app makes — the conversation excerpt comes after it. Byte-identical means
 * it is processed once per runtime and reused for every chat named afterwards; a version that
 * mentioned the model, the date, or the conversation would be a cache miss every time.
 */
const INSTRUCTION =
  "Name this conversation. Reply with a title of at most six words. " +
  "No quotes, no trailing punctuation, no preamble.";

/** Longest title kept. The sidebar is a fixed 240px, so anything past this truncates on screen
 *  anyway — better to cut it here, at a word boundary, than to let the rail cut mid-word. */
const MAX_CHARS = 48;
/** Completion budget. Six words is ~10 tokens; the slack is for a model that adds a word or two.
 *  Overrunning it is treated as a failure rather than trimmed — see `finish_reason` below. */
const MAX_TOKENS = 24;
/** How much of each turn is quoted. The point is what the conversation is *about*, which is in the
 *  opening of both turns; sending the whole of a long answer would cost more prompt processing than
 *  the reply it names. */
const PROMPT_CHARS = 600;
const REPLY_CHARS = 400;
/** The model just answered, so it is warm and this is a short prompt — a call still running after
 *  this has hit something worse than slowness (a swapped runtime, a wedged upstream). */
const TIMEOUT_MS = 30_000;

/** What a title request needs: the loaded model, and the first exchange to name. */
export interface TitleRequest {
  /** The name from /api/status. THE loaded model — see the header. */
  model: string;
  /** The user's first message (may be empty: an image-only turn is the case that motivated this). */
  prompt: string;
  /** The assistant's answer to it (may be empty when the turn errored or was stopped). */
  reply: string;
  /** One image from the first turn, or null. Pass null unless the loaded model has vision. */
  image?: string | null;
}

/** A single answer from the /v1 proxy, read defensively — nothing here is trusted to exist. */
interface CompletionResponse {
  choices?: { finish_reason?: string; message?: { content?: unknown } }[];
}

function clamp(text: string, limit: number): string {
  const clean = text.trim().replace(/\s+/g, " ");
  return clean.length > limit ? clean.slice(0, limit) : clean;
}

/**
 * The conversation as the model is shown it back.
 *
 * INCLUDING THE REPLY IS DELIBERATE, and it is the difference between this working and not on the
 * case that started it: an image-only first message has no user text whatsoever, so the answer is
 * the only description of the picture that exists without paying for a second vision pass. It is
 * clamped to a few hundred characters, which is where the subject of an answer lives — a reply's
 * later paragraphs are elaboration, and elaboration is what makes a title drift off the point.
 */
function excerpt(req: TitleRequest): string {
  const prompt = clamp(req.prompt, PROMPT_CHARS);
  const reply = clamp(req.reply, REPLY_CHARS);
  const lines = [`User: ${prompt || (req.image ? "(an image, no text)" : "(no text)")}`];
  if (reply) lines.push(`Assistant: ${reply}`);
  return lines.join("\n");
}

/** A single-word answer that names the *act* of titling rather than the conversation. Every one of
 *  these is a model restating the question, and every one is worse than the derived title. */
const JUNK = new Set([
  "title",
  "chat",
  "conversation",
  "untitled",
  "none",
  "n/a",
  "unknown",
  "attachment",
  "image",
  "message",
  "assistant",
  "user",
  "sure",
  "ok",
  "okay",
  "hello",
  "hi",
]);

/** How a model declines, or hedges before answering. Either way there is no title in the string. */
const REFUSAL =
  /^(i can'?t|i cannot|i(?:'m| am) (?:unable|sorry|not)|i don'?t|as an ai|sorry[,.!\s]|unfortunately[,\s])/i;

/**
 * Turn whatever the model said into a title, or null if there isn't one in there.
 *
 * Models answer the same question in a dozen shapes — `"Heim Sidebar Screenshots"`, `Title: heim
 * sidebar`, `**Heim sidebar**`, a title with a full stop on the end, a title followed by two
 * paragraphs explaining the choice. Every one of those contains the answer; stripping the wrapping
 * is cheaper and far more often right than re-asking. What is NOT recoverable — a refusal, a
 * restatement of the question, an empty string — returns null, and the caller keeps the derived
 * title.
 */
export function sanitizeTitle(raw: string): string | null {
  // The first non-empty line only: a model that explains itself does so *after* answering, and
  // everything past the first newline is that explanation.
  let t = (raw.split("\n").find((line) => line.trim()) ?? "").trim();
  // Bold/italic wrapping, then a label. Order matters: `**Title: x**` is both.
  t = t.replace(/^\*+\s*/, "").replace(/\s*\*+$/, "");
  t = t.replace(/^(?:chat\s+|conversation\s+)?title\s*[:\-–]\s*/i, "");
  // Matched quotes only — an apostrophe inside a title is not a quote, and a single leading quote
  // is likelier to be part of the text than a wrapper the model forgot to close.
  const quoted = /^(["'“‘`])([\s\S]*)(["'”’`])$/.exec(t);
  if (quoted) t = quoted[2];
  t = t.replace(/\s+/g, " ").trim();
  // Trailing sentence punctuation. `?` and `!` survive: a title can honestly be a question.
  t = t.replace(/[.,;:\s]+$/, "");
  if (!t || t.length < 2) return null;
  if (REFUSAL.test(t)) return null;
  // `!`/`?` survive the trim above — a title may honestly be a question — but they are noise when
  // deciding whether the model answered with a word for "yes" instead of with a name.
  if (JUNK.has(t.toLowerCase().replace(/[!?]+$/, ""))) return null;
  if (t.length > MAX_CHARS) {
    // Cut at a word boundary, which is the whole complaint about the derived title: "anal…" is a
    // truncation, "Analysing the images…" is a name that happens to be long.
    const cut = t.slice(0, MAX_CHARS);
    const space = cut.lastIndexOf(" ");
    t = `${(space > MAX_CHARS / 2 ? cut.slice(0, space) : cut).replace(/[.,;:]+$/, "")}…`;
  }
  return t;
}

/**
 * Ask the loaded model to name this conversation. Resolves to a title, or null on any failure.
 *
 * `enable_thinking: false` is not optional. A reasoning model spends its completion budget thinking
 * before it answers, and with a 24-token cap the budget is gone before a single character of
 * content is produced — a 200 response carrying an empty string. Measured on this machine: 24
 * completion tokens and no content with thinking on, 2 tokens and an answer with it off. An empty
 * string is therefore read as failure, never as "the model had no title".
 */
export async function requestConversationTitle(req: TitleRequest): Promise<string | null> {
  const body = excerpt(req);
  if (!body && !req.image) return null;
  const text = `${INSTRUCTION}\n\n${body}`;
  // A plain string when there is no image: the multimodal parts array is understood by every
  // backend heim proxies, but a bare string is what a text-only runtime is happiest with, and this
  // path runs against whatever happens to be loaded.
  const content = req.image ? [{ type: "text", text }, { type: "image_url", image_url: { url: req.image } }] : text;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await apiFetch("/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
      body: JSON.stringify({
        model: req.model,
        max_tokens: MAX_TOKENS,
        temperature: 0, // naming a chat is not a creative act; the same chat should get the same name
        stream: false,
        chat_template_kwargs: { enable_thinking: false },
        messages: [{ role: "user", content }],
      }),
    });
    if (!res.ok) return null; // 409 (unloaded between the reply and this call), 502, anything
    const data = (await res.json()) as CompletionResponse;
    const choice = data.choices?.[0];
    // Stopped because it ran out of budget, not because it was done: what came back is the opening
    // of something longer, so it is a sentence being written, not a title. Not worth salvaging.
    if (choice?.finish_reason === "length") return null;
    return typeof choice?.message?.content === "string" ? sanitizeTitle(choice.message.content) : null;
  } catch {
    return null; // aborted by the timeout, offline, unparseable body — all the same outcome
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Apply a model-generated title, unless the user has named this conversation themselves.
 *
 * THE RULE THIS FILE EXISTS TO NOT BREAK. A name someone typed is the one title in the app that
 * carries intent, and a background request that lands two seconds later must never win against it.
 * The guard is `titledBy`, recorded when each title was set — not a comparison of the current title
 * against what `deriveTitle` would have produced, which would silently treat a hand-typed name that
 * happens to match as fair game, and would break the moment either function changed.
 */
export function applyModelTitle(conv: Conversation, title: string): Conversation {
  if (conv.titledBy === "user") return conv;
  return { ...conv, title, titledBy: "model" };
}
