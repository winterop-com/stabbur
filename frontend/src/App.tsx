import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PanelLeft, Settings as SettingsIcon, Sun, Moon } from "lucide-react";
import { cn } from "@/lib/utils";

import {
  getLibrary,
  getStatus,
  loadModel,
  streamChat,
  type LibModel,
  type Msg,
  type Status,
} from "@/api";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Composer } from "@/components/Composer";
import { MessageItem } from "@/components/MessageItem";
import { ModelSelector } from "@/components/ModelSelector";
import { SettingsPanel } from "@/components/SettingsPanel";
import { Sidebar } from "@/components/Sidebar";
import {
  deriveTitle,
  loadConversations,
  loadSettings,
  saveConversations,
  saveSettings,
  uid,
  type Settings,
} from "@/lib/store";
import type { ChatMessage, Conversation, ToolMarker } from "@/lib/types";
import { useTheme } from "@/lib/useTheme";

export function App() {
  const { theme, toggle } = useTheme();

  // Server state.
  const [status, setStatus] = useState<Status | null>(null);
  const [library, setLibrary] = useState<LibModel[]>([]);
  const [loadingName, setLoadingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // App state.
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations());
  const [activeId, setActiveId] = useState<string | null>(() => {
    const convs = loadConversations();
    return convs.length ? [...convs].sort((a, b) => b.updatedAt - a.updatedAt)[0].id : null;
  });
  const [settings, setSettings] = useState<Settings>(() => loadSettings());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Chat state.
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // --- persistence ---
  useEffect(() => saveConversations(conversations), [conversations]);
  useEffect(() => saveSettings(settings), [settings]);

  // --- server polling ---
  const refreshStatus = useCallback(() => getStatus().then(setStatus).catch(() => {}), []);
  useEffect(() => {
    refreshStatus();
    getLibrary().then(setLibrary).catch((e) => setError(String(e)));
    const t = setInterval(refreshStatus, 2000);
    return () => clearInterval(t);
  }, [refreshStatus]);

  const ready = !!status?.model && status.state === "ready";

  const activeConv = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId],
  );
  const messages = activeConv?.messages ?? [];

  // --- model load: POST then poll /api/status until ready ---
  const pick = useCallback(
    async (name: string) => {
      if (status?.locked || loadingName) return;
      setError(null);
      setLoadingName(name);
      try {
        setStatus(await loadModel(name));
        // Poll until the server reports ready (or leaves loading).
        const deadline = Date.now() + 120_000;
        // eslint-disable-next-line no-constant-condition
        while (Date.now() < deadline) {
          const s = await getStatus();
          setStatus(s);
          if (s.state !== "loading") break;
          await new Promise((r) => setTimeout(r, 800));
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoadingName(null);
        refreshStatus();
      }
    },
    [status?.locked, loadingName, refreshStatus],
  );

  // --- conversation helpers ---
  const upsertConv = useCallback((id: string, fn: (c: Conversation) => Conversation) => {
    setConversations((prev) => prev.map((c) => (c.id === id ? fn(c) : c)));
  }, []);

  const newConversation = useCallback((): string => {
    const now = Date.now();
    const conv: Conversation = { id: uid(), title: "New chat", messages: [], createdAt: now, updatedAt: now };
    setConversations((prev) => [conv, ...prev]);
    setActiveId(conv.id);
    return conv.id;
  }, []);

  const deleteConversation = useCallback(
    (id: string) => {
      setConversations((prev) => {
        const next = prev.filter((c) => c.id !== id);
        if (id === activeId) {
          const sorted = [...next].sort((a, b) => b.updatedAt - a.updatedAt);
          setActiveId(sorted.length ? sorted[0].id : null);
        }
        return next;
      });
    },
    [activeId],
  );

  const renameConversation = useCallback(
    (id: string, title: string) => upsertConv(id, (c) => ({ ...c, title })),
    [upsertConv],
  );

  // --- core: run a chat completion into an assistant turn ---
  const runCompletion = useCallback(
    async (convId: string, priorMessages: ChatMessage[], assistantId: string) => {
      setStreaming(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      // Build the wire payload: optional system prompt + history.
      const wire: Msg[] = [];
      if (settings.systemPrompt.trim()) wire.push({ role: "system", content: settings.systemPrompt.trim() });
      for (const m of priorMessages) wire.push({ role: m.role, content: m.content });

      try {
        for await (const evt of streamChat(wire, ctrl.signal, {
          maxTokens: settings.maxTokens ?? undefined,
          temperature: settings.temperature ?? undefined,
          topP: settings.topP ?? undefined,
          useTools: settings.useTools,
        })) {
          if (evt.type === "token") {
            upsertConv(convId, (c) => ({
              ...c,
              updatedAt: Date.now(),
              messages: c.messages.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + evt.text } : m,
              ),
            }));
          } else if (evt.type === "reasoning") {
            upsertConv(convId, (c) => ({
              ...c,
              updatedAt: Date.now(),
              messages: c.messages.map((m) =>
                m.id === assistantId ? { ...m, reasoning: (m.reasoning ?? "") + evt.text } : m,
              ),
            }));
          } else if (evt.type === "tool") {
            const marker: ToolMarker = { kind: evt.kind, detail: evt.detail };
            upsertConv(convId, (c) => ({
              ...c,
              updatedAt: Date.now(),
              messages: c.messages.map((m) =>
                m.id === assistantId ? { ...m, tools: [...(m.tools ?? []), marker] } : m,
              ),
            }));
          } else if (evt.type === "error") {
            upsertConv(convId, (c) => ({
              ...c,
              messages: c.messages.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content ? `${m.content}\n\n${evt.detail}` : evt.detail, error: true }
                  : m,
              ),
            }));
          } else if (evt.type === "done") {
            break;
          }
        }
      } catch (e) {
        if (!ctrl.signal.aborted) {
          const detail = e instanceof Error ? e.message : String(e);
          upsertConv(convId, (c) => ({
            ...c,
            messages: c.messages.map((m) =>
              m.id === assistantId ? { ...m, content: m.content || `Error: ${detail}`, error: true } : m,
            ),
          }));
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [settings, upsertConv],
  );

  // --- send a new user turn ---
  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming || !ready) return;

    let convId = activeId;
    if (!convId) convId = newConversation();

    const userMsg: ChatMessage = { id: uid(), role: "user", content: text };
    const assistantMsg: ChatMessage = { id: uid(), role: "assistant", content: "" };

    // Snapshot prior messages (before this turn) for the wire payload.
    const prior = (conversations.find((c) => c.id === convId)?.messages ?? []).concat(userMsg);

    upsertConv(convId, (c) => ({
      ...c,
      title: c.messages.length === 0 ? deriveTitle(text) : c.title,
      updatedAt: Date.now(),
      messages: [...c.messages, userMsg, assistantMsg],
    }));
    setInput("");

    await runCompletion(convId, prior, assistantMsg.id);
  }, [input, streaming, ready, activeId, conversations, newConversation, upsertConv, runCompletion]);

  // --- regenerate: drop last assistant turn, re-run the last user turn ---
  const regenerate = useCallback(async () => {
    if (streaming || !ready || !activeConv) return;
    const msgs = activeConv.messages;
    // Find last assistant message and the user prefix that precedes it.
    let lastAssistant = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "assistant") {
        lastAssistant = i;
        break;
      }
    }
    if (lastAssistant < 0) return;
    const prior = msgs.slice(0, lastAssistant).filter((m) => m.role !== "assistant" || m.content);
    // Everything up to (not including) the old assistant turn, plus a fresh one.
    const kept = msgs.slice(0, lastAssistant);
    const assistantMsg: ChatMessage = { id: uid(), role: "assistant", content: "" };
    upsertConv(activeConv.id, (c) => ({
      ...c,
      updatedAt: Date.now(),
      messages: [...kept, assistantMsg],
    }));
    await runCompletion(activeConv.id, kept.length ? kept : prior, assistantMsg.id);
  }, [streaming, ready, activeConv, upsertConv, runCompletion]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  // --- autoscroll: stick to bottom unless the user scrolled up ---
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }, []);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  // On conversation switch, jump to bottom.
  useEffect(() => {
    stick.current = true;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeId]);

  const lastAssistantIndex = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "assistant") return i;
    return -1;
  }, [messages]);

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-full overflow-hidden">
        {/* Always mounted; width animates so collapse slides instead of snapping. */}
        <div
          className={`shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out ${
            sidebarOpen ? "w-[260px]" : "w-0"
          }`}
        >
          <Sidebar
            conversations={conversations}
            activeId={activeId}
            onNew={newConversation}
            onSelect={setActiveId}
            onRename={renameConversation}
            onDelete={deleteConversation}
            onCollapse={() => setSidebarOpen(false)}
          />
        </div>

        <main className="flex min-w-0 flex-1 flex-col">
          {/* top bar */}
          <header className="flex h-12 shrink-0 items-center justify-between gap-2 px-3">
            <div className="flex items-center gap-1">
              {!sidebarOpen && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setSidebarOpen(true)}
                      aria-label="Open sidebar"
                    >
                      <PanelLeft className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Open sidebar</TooltipContent>
                </Tooltip>
              )}
              <ModelSelector status={status} library={library} loadingName={loadingName} onPick={pick} />
            </div>
            <div className="flex items-center gap-0.5">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon-sm" onClick={toggle} aria-label="Toggle theme">
                    {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{theme === "dark" ? "Light mode" : "Dark mode"}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => setSettingsOpen((v) => !v)}
                    aria-label="Settings"
                    aria-pressed={settingsOpen}
                    className={cn(settingsOpen && "bg-accent text-accent-foreground")}
                  >
                    <SettingsIcon className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Settings</TooltipContent>
              </Tooltip>
            </div>
          </header>

          {error && (
            <div className="mx-auto mt-1 w-full max-w-3xl px-4">
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            </div>
          )}

          {messages.length === 0 ? (
            // Empty state: centered greeting + composer.
            <div className="flex flex-1 flex-col items-center justify-center px-4">
              <h1 className="mb-8 text-2xl font-semibold tracking-tight">
                {ready ? "What can I help with?" : "Select a model to start"}
              </h1>
              <div className="w-full max-w-3xl">
                <Composer
                  value={input}
                  onChange={setInput}
                  onSend={send}
                  onStop={stop}
                  streaming={streaming}
                  ready={ready}
                  autoFocus
                />
              </div>
            </div>
          ) : (
            <>
              <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6">
                  {messages.map((m, i) => (
                    <MessageItem
                      key={m.id}
                      message={m}
                      streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
                      canRegenerate={!streaming && i === lastAssistantIndex}
                      onRegenerate={regenerate}
                    />
                  ))}
                </div>
              </div>
              <div className="shrink-0 px-4 pb-4">
                <div className="mx-auto w-full max-w-3xl">
                  <Composer
                    value={input}
                    onChange={setInput}
                    onSend={send}
                    onStop={stop}
                    streaming={streaming}
                    ready={ready}
                  />
                  <p className="mt-2 text-center text-[11px] text-muted-foreground">
                    kodo runs your model locally. Responses may be inaccurate.
                  </p>
                </div>
              </div>
            </>
          )}
        </main>

        {/* Right rail: width animates like the left sidebar; panel mounts when
            open so the model card fetch fires on open + model change. */}
        <div
          className={`shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out ${
            settingsOpen ? "w-[320px]" : "w-0"
          }`}
        >
          {settingsOpen && (
            <SettingsPanel
              status={status}
              library={library}
              settings={settings}
              onChange={setSettings}
              onCollapse={() => setSettingsOpen(false)}
            />
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}
