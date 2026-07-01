import { useEffect, useMemo, useRef, useState } from "react";

import { getLibrary, getStatus, loadModel, streamChat, type LibModel, type Msg, type Status } from "./api";

const STATE_COLOR: Record<Status["state"], string> = {
  ready: "bg-emerald-400",
  loading: "bg-amber-400",
  stopped: "bg-zinc-500",
};

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [library, setLibrary] = useState<LibModel[]>([]);
  const [loadingName, setLoadingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshStatus = () => getStatus().then(setStatus).catch(() => {});

  useEffect(() => {
    refreshStatus();
    getLibrary().then(setLibrary).catch((e) => setError(String(e)));
    const t = setInterval(refreshStatus, 2000);
    return () => clearInterval(t);
  }, []);

  const pick = async (name: string) => {
    if (status?.locked || loadingName) return;
    setError(null);
    setLoadingName(name);
    try {
      setStatus(await loadModel(name));
      await refreshStatus();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoadingName(null);
    }
  };

  const grouped = useMemo(() => {
    const by: Record<string, LibModel[]> = {};
    for (const m of library) (by[m.model_format] ??= []).push(m);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [library]);

  return (
    <div className="flex h-full">
      <aside className="flex w-72 shrink-0 flex-col border-r border-zinc-800 bg-[#0e1116]">
        <div className="flex items-center gap-2 px-4 py-4">
          <span className="text-lg font-semibold tracking-tight">kodo</span>
          {status?.locked && <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">locked</span>}
        </div>
        <div className="flex items-center gap-2 px-4 pb-3 text-sm">
          <span className={`h-2 w-2 rounded-full ${STATE_COLOR[status?.state ?? "stopped"]}`} />
          <span className="truncate text-zinc-300">{status?.model ?? "no model loaded"}</span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          {grouped.map(([fmt, models]) => (
            <div key={fmt} className="mb-3">
              <div className="px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">{fmt}</div>
              {models.map((m) => {
                const active = status?.model === m.name;
                const busy = loadingName === m.name;
                return (
                  <button
                    key={m.name}
                    onClick={() => pick(m.name)}
                    disabled={status?.locked || !!loadingName}
                    className={`group flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm disabled:opacity-60 ${
                      active ? "bg-zinc-800 text-white" : "text-zinc-300 hover:bg-zinc-800/60"
                    }`}
                    title={m.name}
                  >
                    <span className="truncate">{m.name.split("/").pop()}</span>
                    <span className="ml-2 shrink-0 text-[11px] text-zinc-500">{busy ? "…" : m.size_human}</span>
                  </button>
                );
              })}
            </div>
          ))}
          {library.length === 0 && <div className="px-2 py-4 text-sm text-zinc-500">No models in the library.</div>}
        </div>
        {error && <div className="border-t border-zinc-800 px-4 py-2 text-xs text-red-400">{error}</div>}
      </aside>

      <Chat status={status} />
    </div>
  );
}

function Chat({ status }: { status: Status | null }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const ready = status?.model && status.state === "ready";

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy || !ready) return;
    const base = [...messages, { role: "user" as const, content: text }];
    setMessages([...base, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      for await (const tok of streamChat(base, ctrl.signal)) {
        setMessages((m) => {
          const c = [...m];
          c[c.length - 1] = { ...c[c.length - 1], content: c[c.length - 1].content + tok };
          return c;
        });
      }
    } catch (e) {
      if (!ctrl.signal.aborted) {
        setMessages((m) => {
          const c = [...m];
          c[c.length - 1] = { role: "assistant", content: `⚠ ${e instanceof Error ? e.message : e}` };
          return c;
        });
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
          {messages.length === 0 && (
            <div className="mt-24 text-center text-zinc-500">
              {ready ? "Ask anything." : "Pick a model on the left to start."}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed ${
                  m.role === "user" ? "bg-blue-600 text-white" : "bg-zinc-800 text-zinc-100"
                }`}
              >
                {m.content || (busy && i === messages.length - 1 ? "…" : "")}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      </div>

      <div className="border-t border-zinc-800 bg-[#0e1116] px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
            placeholder={ready ? "Message… (Enter to send, Shift+Enter for newline)" : "Load a model to chat"}
            disabled={!ready}
            className="max-h-40 min-h-[2.5rem] flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-zinc-500 disabled:opacity-60"
          />
          {busy ? (
            <button
              onClick={() => abortRef.current?.abort()}
              className="rounded-lg bg-zinc-700 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-600"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!ready || !input.trim()}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </main>
  );
}
