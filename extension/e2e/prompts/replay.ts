// Replay step: POST a context-prefixed prompt to a running heim /api/chat with
// use_tools=false and a low temperature, parse the typed SSE stream, and return
// the assembled model output (token frames only) plus latency.

export interface ReplayResult {
  output: string;
  reasoning: string;
  latencyMs: number;
  error: string | null;
}

/** Send one turn to /api/chat and collect the streamed answer.
 *
 *  Runaway protection, layered:
 *  - `max_tokens` caps generation so a repetition/reasoning loop can't run forever;
 *  - a wall-clock deadline races every stream read; on timeout the fetch is aborted
 *    so heim cancels the in-flight generation;
 *  - an OUTER race guarantees the caller gets control back at deadline+5s even if
 *    the header wait ignores the abort (observed with Bun's fetch when the server
 *    was wedged behind a previous generation - wall times far above the timeout). */
export async function replay(
  baseUrl: string,
  userContent: string,
  timeoutMs = 180_000,
  maxTokens = 2500,
): Promise<ReplayResult> {
  const started = Date.now();
  return Promise.race([
    replayInner(baseUrl, userContent, timeoutMs, maxTokens),
    new Promise<ReplayResult>((resolve) =>
      setTimeout(
        () =>
          resolve({
            output: "",
            reasoning: "",
            latencyMs: Date.now() - started,
            error: `hard timeout after ${timeoutMs + 5000}ms (no response headers - server busy/wedged)`,
          }),
        timeoutMs + 5000,
      ),
    ),
  ]);
}

async function replayInner(
  baseUrl: string,
  userContent: string,
  timeoutMs: number,
  maxTokens: number,
): Promise<ReplayResult> {
  const started = Date.now();
  const deadline = started + timeoutMs;
  const ctrl = new AbortController();
  const connectTimer = setTimeout(() => ctrl.abort(), timeoutMs);
  let output = "";
  let reasoning = "";
  let error: string | null = null;
  try {
    const res = await fetch(`${baseUrl}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [{ role: "user", content: userContent }],
        use_tools: false,
        temperature: 0.1,
        max_tokens: maxTokens,
      }),
      signal: ctrl.signal,
    });
    clearTimeout(connectTimer);
    if (!res.ok || !res.body) {
      return { output: "", reasoning: "", latencyMs: Date.now() - started, error: `HTTP ${res.status}` };
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const handle = (line: string): void => {
      const s = line.trim();
      if (!s.startsWith("data:")) return;
      const payload = s.slice(5).trim();
      if (!payload || payload === "[DONE]") return;
      let evt: { type?: string; text?: string; detail?: string };
      try {
        evt = JSON.parse(payload);
      } catch {
        return;
      }
      if (evt.type === "token") output += evt.text ?? "";
      else if (evt.type === "reasoning") reasoning += evt.text ?? "";
      else if (evt.type === "error") error = evt.detail ?? "unknown error";
    };
    let timedOut = false;
    for (;;) {
      const remaining = deadline - Date.now();
      if (remaining <= 0) {
        timedOut = true;
        break;
      }
      // Race the read against the wall-clock deadline. On timeout we DON'T await
      // reader.cancel() (it blocks until the server finishes) — we abort the fetch,
      // which closes the socket so heim cancels the in-flight generation, and return.
      const readPromise = reader.read();
      readPromise.catch(() => {}); // swallow the rejection if the deadline wins
      const raced = await Promise.race([
        readPromise,
        new Promise<null>((resolve) => setTimeout(() => resolve(null), remaining)),
      ]);
      if (raced === null) {
        timedOut = true;
        break;
      }
      if (raced.done) break;
      buffer += decoder.decode(raced.value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) handle(line);
    }
    if (timedOut) {
      error = `timeout after ${timeoutMs}ms`;
      ctrl.abort(); // tear down the connection so the server stops generating
    } else {
      buffer += decoder.decode();
      if (buffer.trim()) handle(buffer);
    }
  } catch (e) {
    error = e instanceof Error && e.name === "AbortError" ? `timeout after ${timeoutMs}ms` : String(e);
  } finally {
    clearTimeout(connectTimer);
  }
  return { output: output.trim(), reasoning: reasoning.trim(), latencyMs: Date.now() - started, error };
}

/** Wait until the server answers a trivial completion quickly (i.e. the runtime is
 *  idle again). Used after a timed-out prompt so a wedged/busy generation can't
 *  cascade timeouts into every subsequent prompt. Returns true when healthy. */
export async function waitForIdle(baseUrl: string, timeoutMs = 300_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const probe = await replay(baseUrl, "Reply with exactly the word OK and nothing else.", 30_000, 50);
    if (!probe.error && probe.output.length > 0) return true;
    await new Promise((r) => setTimeout(r, 5000));
  }
  return false;
}
