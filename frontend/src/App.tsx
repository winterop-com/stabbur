import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, Download, PanelLeft, PanelRight, SquarePen, Sun, Moon } from "lucide-react";
import { Panel, PanelGroup, type ImperativePanelHandle } from "react-resizable-panels";
import { cn } from "@/lib/utils";
import { ResizeHandle } from "@/components/ui/resizable";

import {
  buildContent,
  getDoctor,
  getLibrary,
  getStatus,
  getTools,
  getVoices,
  loadModel,
  streamChat,
  unloadModel,
  type DoctorReport,
  type LibModel,
  type Msg,
  type Status,
  type ToolInfo,
  type Voice,
} from "@/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Composer } from "@/components/Composer";
import { HealthMenu } from "@/components/HealthMenu";
import { MessageItem } from "@/components/MessageItem";
import { ModelSelector } from "@/components/ModelSelector";
import { SettingsPanel } from "@/components/SettingsPanel";
import { Sidebar } from "@/components/Sidebar";
import { ToolsControl } from "@/components/ToolsControl";
import {
  DEFAULT_SETTINGS,
  deriveTitle,
  loadConversations,
  saveConversations,
  uid,
  type Settings,
} from "@/lib/store";
import type { Attachment, ChatMessage, Conversation, ToolMarker } from "@/lib/types";
import { exportConversationMarkdown, exportConversationPdf } from "@/lib/export";
import { useTheme } from "@/lib/useTheme";

/** Parse the active conversation id from the URL hash (#/c/<id>), or null. */
function conversationIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/c\/(.+)$/);
  return m ? decodeURIComponent(m[1]) : null;
}

export function App() {
  const { theme, toggle } = useTheme();

  // Server state.
  const [status, setStatus] = useState<Status | null>(null);
  const [library, setLibrary] = useState<LibModel[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [ttsVoice, setTtsVoice] = useState<string>(() => localStorage.getItem("kodo.tts_voice") || "");
  const [health, setHealth] = useState<DoctorReport | null>(null);
  const [loadingName, setLoadingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // App state.
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations());
  const [activeId, setActiveId] = useState<string | null>(() => {
    const convs = loadConversations();
    const fromUrl = conversationIdFromHash();
    if (fromUrl && convs.some((c) => c.id === fromUrl)) return fromUrl; // deep link survives reload
    return convs.length ? [...convs].sort((a, b) => b.updatedAt - a.updatedAt)[0].id : null;
  });
  // Settings live per-conversation (see activeSettings below). This holds the
  // draft used before a conversation exists (the empty state); it seeds the first
  // conversation on send, then resets — so nothing carries between chats.
  const [draftSettings, setDraftSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // --- resizable layout: imperative handles to collapse/expand the rails. ---
  const leftPanel = useRef<ImperativePanelHandle>(null);
  const rightPanel = useRef<ImperativePanelHandle>(null);
  // Animate programmatic collapse/expand, but never during a manual drag (which
  // must track the cursor 1:1). react-resizable-panels sets flex inline, so a CSS
  // flex transition animates the collapse — suppressed while a handle is dragging.
  const [dragging, setDragging] = useState(false);
  // react-resizable-panels animates the panel via inline flex-grow; transition that.
  const railTransition = dragging ? "" : "transition-[flex-grow] duration-200 ease-out";
  const toggleSidebar = useCallback(() => {
    const p = leftPanel.current;
    if (!p) return;
    if (p.isCollapsed()) p.expand();
    else p.collapse();
  }, []);
  const openSidebar = useCallback(() => leftPanel.current?.expand(), []);
  const toggleSettings = useCallback(() => {
    const p = rightPanel.current;
    if (!p) return;
    if (p.isCollapsed()) p.expand();
    else p.collapse();
  }, []);

  // Chat state.
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]); // pending image/audio attachments
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // --- persistence ---
  useEffect(() => saveConversations(conversations), [conversations]);

  // --- URL routing: reflect the active conversation's id in the hash (#/c/<id>)
  // so a reload / bookmark / back-button lands on the same chat. ---
  useEffect(() => {
    const target = activeId ? `#/c/${activeId}` : "";
    if (window.location.hash !== target) {
      if (target) window.location.hash = target;
      else history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, [activeId]);
  useEffect(() => {
    const onHash = () => {
      const id = conversationIdFromHash();
      if (id) setActiveId(id);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // --- server polling ---
  const refreshStatus = useCallback(() => getStatus().then(setStatus).catch(() => {}), []);
  useEffect(() => {
    refreshStatus();
    // Library + tools + health are cheap-ish filesystem reads: fetch on mount and
    // refresh on a slow interval so a transient failure (e.g. a server restart)
    // self-heals and newly-pulled models appear without a manual reload.
    const refreshSlow = () => {
      getLibrary()
        .then((lib) => {
          setLibrary(lib);
          // Clear a prior library-fetch error on recovery (but not model-load errors).
          setError((e) => (e && e.startsWith("Library: ") ? null : e));
        })
        .catch((e) => setError(`Library: ${e}`));
      getTools().then(setTools).catch(() => {}); // tools are optional; empty if none configured
      getVoices().then(setVoices).catch(() => {}); // voices are optional (no TTS engine)
      getDoctor().then(setHealth).catch(() => {});
    };
    refreshSlow();
    const t = setInterval(refreshStatus, 2000);
    const s = setInterval(refreshSlow, 10000);
    return () => {
      clearInterval(t);
      clearInterval(s);
    };
  }, [refreshStatus]);

  const ready = !!status?.model && status.state === "ready";
  // Which attachment modalities the loaded model accepts (composer gating).
  const loadedModel = library.find((m) => m.name === status?.model);
  const visionModel = !!loadedModel?.vision;
  const audioModel = !!loadedModel?.audio;

  const activeConv = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId],
  );
  const messages = activeConv?.messages ?? [];

  // Effective settings = the active conversation's own settings, or the draft
  // when no conversation is active yet. Editing writes back to whichever applies.
  const settings = activeConv?.settings ?? draftSettings;
  const updateSettings = useCallback(
    (next: Settings) => {
      if (activeId) setConversations((prev) => prev.map((c) => (c.id === activeId ? { ...c, settings: next } : c)));
      else setDraftSettings(next);
    },
    [activeId],
  );

  // --- model load: POST then poll /api/status until ready ---
  const pick = useCallback(
    async (name: string, nCtx?: number | null) => {
      if (status?.locked || loadingName) return;
      setError(null);
      setLoadingName(name);
      try {
        setStatus(await loadModel(name, nCtx === undefined ? settings.contextLength : nCtx));
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
    [status?.locked, loadingName, refreshStatus, settings.contextLength],
  );

  // Eject the loaded model (frees memory); rejected in locked mode.
  const eject = useCallback(async () => {
    if (status?.locked) return;
    try {
      setStatus(await unloadModel());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      refreshStatus();
    }
  }, [status?.locked, refreshStatus]);

  // Reload the current model with a new context window (context is load-time).
  const reloadWithContext = useCallback(
    (nCtx: number | null) => {
      if (status?.model) pick(status.model, nCtx);
    },
    [status?.model, pick],
  );

  // Project auto-load: in a project dir (kodo.toml [project].model), boot straight
  // into the bound model on first open — the manifest is a reproducible assistant
  // (model + system prompt + tools). Fires once, only if nothing's loaded and the
  // model is actually in the library; the user can still switch afterwards.
  const autoLoadedRef = useRef(false);
  useEffect(() => {
    if (autoLoadedRef.current || !status || status.model || status.locked || loadingName) return;
    const wanted = status.project_model;
    if (!wanted) return;
    const inLibrary = library.some((m) => m.name === wanted || m.name.split("/").pop() === wanted);
    if (!inLibrary) return;
    autoLoadedRef.current = true;
    pick(wanted);
  }, [status, library, loadingName, pick]);

  // --- conversation helpers ---
  const upsertConv = useCallback((id: string, fn: (c: Conversation) => Conversation) => {
    setConversations((prev) => prev.map((c) => (c.id === id ? fn(c) : c)));
  }, []);

  // Create a conversation with its own settings snapshot. The explicit "New chat"
  // button starts from DEFAULT_SETTINGS (a truly fresh chat); sending from the
  // empty state passes the configured draft so it carries into that first chat.
  const newConversation = useCallback((initial: Settings = DEFAULT_SETTINGS): string => {
    const now = Date.now();
    const conv: Conversation = {
      id: uid(),
      title: "New chat",
      messages: [],
      settings: initial,
      createdAt: now,
      updatedAt: now,
    };
    setConversations((prev) => [conv, ...prev]);
    setActiveId(conv.id);
    setDraftSettings(DEFAULT_SETTINGS); // next empty state starts clean
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

      // History only — the system prompt is sent authoritatively via the
      // system_prompt field (so an empty field means *no* system prompt, not a
      // silent fallback to the project default).
      const wire: Msg[] = priorMessages.map((m) => ({
        role: m.role,
        content: buildContent(m.content, m.images, m.audios, m.files),
      }));

      try {
        // Allow-list = attached tools minus the user's denylist (sent only when
        // something is off, so the default keeps every tool available).
        const disabled = new Set(settings.disabledTools);
        const someOff = tools.some((t) => disabled.has(t.name));
        const enabledTools = someOff ? tools.filter((t) => !disabled.has(t.name)).map((t) => t.name) : undefined;

        for await (const evt of streamChat(wire, ctrl.signal, {
          maxTokens: settings.maxTokens ?? undefined,
          temperature: settings.temperature ?? undefined,
          topP: settings.topP ?? undefined,
          useTools: settings.useTools,
          enabledTools,
          systemPrompt: settings.systemPrompt,
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
    [settings, tools, upsertConv],
  );

  // --- send a new user turn ---
  const send = useCallback(async () => {
    const text = input.trim();
    const images = attachments.filter((a) => a.kind === "image" && a.url).map((a) => a.url as string);
    const audios = attachments.filter((a) => a.kind === "audio" && a.url).map((a) => a.url as string);
    const files = attachments
      .filter((a) => a.kind === "text")
      .map((a) => ({ name: a.name ?? "file", text: a.text ?? "" }));
    if ((!text && attachments.length === 0) || streaming || !ready) return;

    let convId = activeId;
    if (!convId) convId = newConversation(draftSettings); // carry empty-state config into the first chat

    const userMsg: ChatMessage = {
      id: uid(),
      role: "user",
      content: text,
      ...(images.length ? { images } : {}),
      ...(audios.length ? { audios } : {}),
      ...(files.length ? { files } : {}),
    };
    const assistantMsg: ChatMessage = {
      id: uid(),
      role: "assistant",
      content: "",
      ...(status?.model ? { model: status.model } : {}),
    };

    // Snapshot prior messages (before this turn) for the wire payload.
    const prior = (conversations.find((c) => c.id === convId)?.messages ?? []).concat(userMsg);

    upsertConv(convId, (c) => ({
      ...c,
      title: c.messages.length === 0 ? deriveTitle(text || "Attachment") : c.title,
      updatedAt: Date.now(),
      messages: [...c.messages, userMsg, assistantMsg],
    }));
    setInput("");
    setAttachments([]);

    await runCompletion(convId, prior, assistantMsg.id);
  }, [input, attachments, streaming, ready, status?.model, activeId, conversations, newConversation, upsertConv, runCompletion]);

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
    const assistantMsg: ChatMessage = {
      id: uid(),
      role: "assistant",
      content: "",
      ...(status?.model ? { model: status.model } : {}),
    };
    upsertConv(activeConv.id, (c) => ({
      ...c,
      updatedAt: Date.now(),
      messages: [...kept, assistantMsg],
    }));
    await runCompletion(activeConv.id, kept.length ? kept : prior, assistantMsg.id);
  }, [streaming, ready, status?.model, activeConv, upsertConv, runCompletion]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  // --- autoscroll: stick to bottom unless the user scrolled up ---
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [atBottom, setAtBottom] = useState(true); // drives the scroll-to-bottom button
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stick.current = nearBottom;
    setAtBottom(nearBottom);
  }, []);
  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, []);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  // On conversation switch, jump to bottom.
  useEffect(() => {
    stick.current = true;
    setAtBottom(true);
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeId]);

  const lastAssistantIndex = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "assistant") return i;
    return -1;
  }, [messages]);

  // Contextual nudge: audio-native models (Ultravox/Voxtral/Qwen-Audio) understand
  // speech better than vision-first generalists (gemma) whose audio is a spectrogram
  // bolt-on. When audio is attached to a generalist and a specialist is in the
  // library, suggest the switch.
  const audioSpecialist = (name: string) => /ultravox|voxtral|qwen2?[-_.]?audio|whisper/i.test(name);
  const audioNudge = useMemo(() => {
    const hasAudio = attachments.some((a) => a.kind === "audio");
    if (!hasAudio || !status?.model || audioSpecialist(status.model)) return null;
    const specialist = library.find((m) => m.audio && audioSpecialist(m.name) && m.name !== status.model);
    return specialist ?? null;
  }, [attachments, status?.model, library]);

  // --- tool enable/disable (denylist), on the active conversation's settings ---
  const toggleTool = useCallback(
    (name: string, enabled: boolean) => {
      const set = new Set(settings.disabledTools);
      if (enabled) set.delete(name);
      else set.add(name);
      updateSettings({ ...settings, disabledTools: [...set] });
    },
    [settings, updateSettings],
  );
  const toggleServer = useCallback(
    (names: string[], enabled: boolean) => {
      const set = new Set(settings.disabledTools);
      for (const n of names) (enabled ? set.delete(n) : set.add(n));
      updateSettings({ ...settings, disabledTools: [...set] });
    },
    [settings, updateSettings],
  );
  const setUseTools = useCallback(
    (on: boolean) => updateSettings({ ...settings, useTools: on }),
    [settings, updateSettings],
  );

  // TTS voice (a global preference for the Listen button): "" = default OuteTTS.
  const chooseVoice = useCallback((name: string) => {
    setTtsVoice(name);
    if (name) localStorage.setItem("kodo.tts_voice", name);
    else localStorage.removeItem("kodo.tts_voice");
  }, []);

  // --- attachments (image / audio) ---
  const addAttachments = useCallback((items: Attachment[]) => setAttachments((a) => [...a, ...items]), []);
  const removeAttachment = useCallback((i: number) => setAttachments((a) => a.filter((_, idx) => idx !== i)), []);
  const accept = useMemo(
    () => ({ image: ready && visionModel, audio: ready && audioModel }),
    [ready, visionModel, audioModel],
  );

  // Controls docked in the composer: model picker + tools (the natural spot).
  const disabledSet = useMemo(() => new Set(settings.disabledTools), [settings.disabledTools]);
  const composerControls = (
    <>
      <ModelSelector
        status={status}
        library={library}
        loadingName={loadingName}
        onPick={pick}
        onEject={eject}
      />
      <ToolsControl
        tools={tools}
        useTools={settings.useTools}
        disabled={disabledSet}
        onToggleUse={setUseTools}
        onToggleTool={toggleTool}
        onToggleServer={toggleServer}
      />
    </>
  );

  // Shown above the composer when a better audio model is available for the attachment.
  const nudgeEl = audioNudge && (
    <div className="mx-auto mb-2 flex w-full max-w-4xl items-center justify-between gap-3 rounded-lg border border-border bg-muted/50 px-3 py-1.5 text-[12px]">
      <span className="text-muted-foreground">
        {status!.model!.split("/").pop()} handles audio, but{" "}
        <span className="font-medium text-foreground">{audioNudge.name.split("/").pop()}</span> is built for speech.
      </span>
      <button
        type="button"
        onClick={() => pick(audioNudge.name)}
        className="shrink-0 font-medium text-primary hover:underline"
      >
        Switch
      </button>
    </div>
  );

  return (
    <TooltipProvider delayDuration={300}>
      <PanelGroup
        direction="horizontal"
        autoSaveId="kodo-layout"
        className="h-full overflow-hidden"
      >
        {/* Left rail: collapsible + resizable. Kept in sync with sidebarOpen so
            the top-bar re-open button knows when to show. */}
        <Panel
          ref={leftPanel}
          id="sidebar"
          order={1}
          collapsible
          collapsedSize={0}
          defaultSize={18}
          minSize={12}
          maxSize={32}
          onCollapse={() => setSidebarOpen(false)}
          onExpand={() => setSidebarOpen(true)}
          className={cn("min-w-0", railTransition)}
        >
          <Sidebar
            conversations={conversations}
            activeId={activeId}
            onNew={newConversation}
            onSelect={setActiveId}
            onRename={renameConversation}
            onDelete={deleteConversation}
            onCollapse={toggleSidebar}
          />
        </Panel>

        {/* Always mounted (stable child order); hairline hidden when the left
            rail is collapsed so there's no stray divider at the edge. */}
        <ResizeHandle
          onDragging={setDragging}
          className={cn(!sidebarOpen && "pointer-events-none bg-transparent")}
        />

        <Panel id="main" order={2} minSize={30} className={cn("flex min-w-0 flex-col", railTransition)}>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          {/* top bar */}
          <header className="flex h-12 shrink-0 items-center justify-between gap-2 px-3">
            <div className="flex items-center gap-1">
              {/* When the sidebar is collapsed, keep its actions reachable in the
                  top bar (open + new chat), like chapkit's persistent rail. */}
              {!sidebarOpen && (
                <>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={openSidebar}
                        aria-label="Open sidebar"
                      >
                        <PanelLeft className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Open sidebar</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => newConversation()}
                        aria-label="New chat"
                      >
                        <SquarePen className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>New chat</TooltipContent>
                  </Tooltip>
                </>
              )}
            </div>
            <div className="flex items-center gap-0.5">
              {activeConv && messages.length > 0 && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Export conversation"
                      title="Export conversation"
                    >
                      <Download className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => exportConversationMarkdown(activeConv, status?.model ?? null)}>
                      Markdown (.md)
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => void exportConversationPdf(activeConv, status?.model ?? null)}>
                      PDF / Print
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
              <HealthMenu health={health} />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon-sm" onClick={toggle} aria-label="Toggle theme">
                    {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{theme === "dark" ? "Light mode" : "Dark mode"}</TooltipContent>
              </Tooltip>
              {/* Open-only: when the panel is open, its own header button collapses
                  it (mirrors the sidebar), so there's a single affordance at a time. */}
              {!settingsOpen && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost" size="icon-sm" onClick={toggleSettings} aria-label="Open settings panel">
                      <PanelRight className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Open settings</TooltipContent>
                </Tooltip>
              )}
            </div>
          </header>

          {(error || status?.error) && (
            <div className="mx-auto mt-1 w-full max-w-4xl px-4">
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error && <div>{error}</div>}
                {status?.error && (
                  <details className={error ? "mt-1" : undefined}>
                    <summary className="cursor-pointer">The model runtime stopped unexpectedly.</summary>
                    <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] opacity-80">
                      {status.error}
                    </pre>
                  </details>
                )}
              </div>
            </div>
          )}

          {messages.length === 0 ? (
            // Empty state: centered greeting + composer.
            <div className="flex flex-1 flex-col items-center justify-center px-4">
              <h1 className="mb-8 text-2xl font-semibold tracking-tight">
                {ready ? "What can I help with?" : "Select a model to start"}
              </h1>
              <div className="w-full max-w-4xl">
                {nudgeEl}
                <Composer
                  value={input}
                  onChange={setInput}
                  onSend={send}
                  onStop={stop}
                  streaming={streaming}
                  ready={ready}
                  autoFocus
                  leftSlot={composerControls}
                  attachments={attachments}
                  accept={accept}
                  onAdd={addAttachments}
                  onRemove={removeAttachment}
                />
              </div>
            </div>
          ) : (
            <>
              <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-6">
                  {messages.map((m, i) => (
                    <MessageItem
                      key={m.id}
                      message={m}
                      streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
                      canRegenerate={!streaming && i === lastAssistantIndex}
                      onRegenerate={regenerate}
                      ttsVoice={ttsVoice}
                    />
                  ))}
                </div>
              </div>
              <div className="relative shrink-0 px-4 pb-4">
                {!atBottom && (
                  <button
                    type="button"
                    onClick={scrollToBottom}
                    aria-label="Scroll to latest"
                    className="absolute -top-5 left-1/2 z-10 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-card text-foreground shadow-md transition-colors hover:bg-accent"
                  >
                    <ArrowDown className="h-4 w-4" />
                  </button>
                )}
                <div className="mx-auto w-full max-w-4xl">
                  {nudgeEl}
                  <Composer
                    value={input}
                    onChange={setInput}
                    onSend={send}
                    onStop={stop}
                    streaming={streaming}
                    ready={ready}
                    leftSlot={composerControls}
                    attachments={attachments}
                    accept={accept}
                    onAdd={addAttachments}
                    onRemove={removeAttachment}
                  />
                  <p className="mt-2 text-center text-[11px] text-muted-foreground">
                    kodo runs your model locally. Responses may be inaccurate.
                  </p>
                </div>
              </div>
            </>
          )}
        </main>
        </Panel>

        {/* Always mounted (so the PanelGroup child order stays stable), but the
            hairline is hidden while the right rail is collapsed. */}
        <ResizeHandle
          onDragging={setDragging}
          className={cn(!settingsOpen && "pointer-events-none bg-transparent")}
        />

        {/* Right rail: collapsible + resizable. Content mounts only when open so
            the model-card fetch fires on open + model change. */}
        <Panel
          ref={rightPanel}
          id="settings"
          order={3}
          collapsible
          collapsedSize={0}
          defaultSize={0}
          minSize={16}
          maxSize={40}
          onCollapse={() => setSettingsOpen(false)}
          onExpand={() => setSettingsOpen(true)}
          className={cn("min-w-0", railTransition)}
        >
          {settingsOpen && (
            <SettingsPanel
              status={status}
              library={library}
              settings={settings}
              onChange={updateSettings}
              onCollapse={toggleSettings}
              onReloadContext={reloadWithContext}
              busy={loadingName != null}
              voices={voices}
              ttsVoice={ttsVoice}
              onChooseVoice={chooseVoice}
            />
          )}
        </Panel>
      </PanelGroup>
    </TooltipProvider>
  );
}
