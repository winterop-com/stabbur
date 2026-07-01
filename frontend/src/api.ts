// Thin client for kodo's server: status/library/load + a hand-rolled OpenAI SSE
// chat loop (kodo proxies raw OpenAI SSE at /v1, so no AI-SDK adapter needed).

export type Role = "user" | "assistant" | "system";
export interface Msg {
  role: Role;
  content: string;
}

export interface Status {
  state: "stopped" | "loading" | "ready";
  model: string | null;
  locked: boolean;
}

export interface LibModel {
  name: string;
  model_format: string;
  size_bytes: number;
  size_human: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const getStatus = () => fetch("/api/status").then(json<Status>);
export const getLibrary = () => fetch("/api/library").then(json<LibModel[]>);

export async function loadModel(name: string): Promise<Status> {
  // /api/load/{name:path} accepts slashes; don't encode them away.
  const res = await fetch(`/api/load/${name}`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return json<Status>(res);
}

/** Stream assistant tokens for a chat completion. Abort via `signal`. */
export async function* streamChat(messages: Msg[], signal: AbortSignal): AsyncGenerator<string> {
  const res = await fetch("/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, stream: true }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const s = line.trim();
      if (!s.startsWith("data:")) continue;
      const payload = s.slice(5).trim();
      if (payload === "[DONE]") return;
      try {
        const delta = JSON.parse(payload)?.choices?.[0]?.delta?.content;
        if (delta) yield delta as string;
      } catch {
        // keepalive or a partial chunk split across reads — ignore
      }
    }
  }
}
