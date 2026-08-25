import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, Download, Sun, Moon, X } from "lucide-react";
import { Panel, PanelGroup, type ImperativePanelHandle } from "react-resizable-panels";
import { cn } from "@/lib/utils";
import { ResizeHandle } from "@/components/ui/resizable";

import {
  AssistantsUnavailableError,
  buildContent,
  confirmAction,
  getAssistants,
  getDoctor,
  getLibrary,
  getStatus,
  getTagRegistry,
  getTools,
  getVoiceModels,
  getVoices,
  loadModel,
  setModelTags,
  streamChat,
  unloadModel,
  type AssistantTarget,
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
import { IconRail } from "@/components/IconRail";
import { useIsMobile } from "@/lib/use-mobile";
import type { TagRegistry } from "@/lib/tags";
import { LoadedModelBadge } from "@/components/LoadedModelBadge";
import { MessageItem } from "@/components/MessageItem";
import { ModelSelector } from "@/components/ModelSelector";
import { TargetSelector } from "@/components/TargetSelector";
import { LibraryView } from "@/components/LibraryView";
import { VoiceView } from "@/components/VoiceView";
import { SettingsView } from "@/components/SettingsView";
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
import type { Attachment, ChatMessage, Conversation, PendingConfirm, ToolMarker } from "@/lib/types";
import { exportConversationMarkdown, exportConversationPdf } from "@/lib/export";
import { useTheme } from "@/lib/useTheme";

// The selected assistant target persists per backend (a heim project is served on one origin), so
// two projects served on different ports keep independent picks; a same-origin restart restores it.
const TARGET_KEY = `heim.target:${window.location.host}`;

/** Parse the active conversation id from the URL hash (#/c/<id>), or null. */
function conversationIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/c\/(.+)$/);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Which primary surface to show: the chat, the model library grid, the voice studio, or settings. */
type View = "chat" | "library" | "voice" | "settings";

/** Map a raw runtime error / log tail to a friendly one-liner for known failures. */
function friendlyRuntimeError(raw: string): string | null {
  const low = raw.toLowerCase();
  // mlx-vlm / mlx_lm weight-or-architecture mismatch: a broken or unsupported MLX
  // conversion whose tensors don't line up with the model class (a raw key dump).
  if (
    /received parameters not in|missing parameter|shape mismatch|size mismatch|does not match|weight.*mismatch/.test(
      low,
    ) ||
    (low.includes("mlx") && low.includes("parameter"))
  ) {
    return "This MLX build couldn't be loaded — a weights/architecture mismatch (often a broken or unsupported MLX conversion). Try the model's GGUF build instead.";
  }
  return null;
}

export function App() {
  const { theme, toggle } = useTheme();

  // Server state.
  const [status, setStatus] = useState<Status | null>(null);
  const [library, setLibrary] = useState<LibModel[]>([]);
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [tagRegistry, setTagRegistry] = useState<TagRegistry>({}); // first-class tag colors/icons
  const [tools, setTools] = useState<ToolInfo[]>([]);
  // Multi-target project registry ([[assistants]]): a picker shows only with >= 2 targets, and the
  // chosen id rides every chat turn as `target` (the server routes per turn, spawning a target's
  // bridge lazily on first use). Empty for generic/single-target servers -> no picker, no `target`.
  const [targets, setTargets] = useState<AssistantTarget[]>([]);
  const [targetId, setTargetId] = useState<string | null>(() => localStorage.getItem(TARGET_KEY));
  // An older backend has no /api/assistants route (404). Once we've seen that, stop polling it every slow
  // tick — it will never appear on this backend. Reset on mount (below) so a different backend re-probes.
  const assistantsUnavailableRef = useRef(false);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [sttAvailable, setSttAvailable] = useState(false); // a Whisper STT model is in the library (enables dictation)
  const [ttsVoice, setTtsVoice] = useState<string>(() => localStorage.getItem("heim.tts_voice") || "");
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
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [view, setView] = useState<View>(() =>
    window.location.hash === "#/library"
      ? "library"
      : window.location.hash === "#/voice"
        ? "voice"
        : window.location.hash === "#/settings"
          ? "settings"
          : "chat",
  );

  // --- resizable layout: imperative handle to collapse/expand the left rail. ---
  const leftPanel = useRef<ImperativePanelHandle>(null);
  // Animate programmatic collapse/expand, but never during a manual drag (which
  // must track the cursor 1:1). react-resizable-panels sets flex inline, so a CSS
  // flex transition animates the collapse — suppressed while a handle is dragging.
  const [dragging, setDragging] = useState(false);
  // react-resizable-panels animates the panel via inline flex-grow; transition that.
  const railTransition = dragging ? "" : "transition-[flex-grow] duration-200 ease-out";
  // On phones a resizable rail squeezes the content, so the sidebar becomes an overlay drawer
  // (see the `isMobile` branches below); on desktop it stays the resizable panel.
  const isMobile = useIsMobile();
  const toggleSidebar = useCallback(() => {
    const p = leftPanel.current;
    if (!p) return;
    if (p.isCollapsed()) p.expand();
    else p.collapse();
  }, []);
  const openSidebar = useCallback(() => {
    if (isMobile) setSidebarOpen(true); // open the overlay drawer, don't expand the rail
    else leftPanel.current?.expand();
  }, [isMobile]);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  // Entering mobile collapses the rail to 0 (the drawer replaces it); leaving mobile with the
  // sidebar "open" restores the rail so it doesn't vanish on resize/rotate.
  useEffect(() => {
    const p = leftPanel.current;
    if (!p) return;
    if (isMobile) p.collapse();
    else if (sidebarOpen) p.expand();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMobile]);

  // Chat state.
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]); // pending image/audio attachments
  // Which conversation is currently streaming (null = none). Tracked by id rather than a
  // global boolean so streaming UI (cursor, Stop) only shows on the conversation actually
  // streaming — switching away no longer makes another chat look like it's streaming, nor
  // lets its Stop button abort the real one (F-7).
  const [streamingConvId, setStreamingConvId] = useState<string | null>(null);
  const isStreaming = streamingConvId !== null; // any stream in flight (one at a time)
  const abortRef = useRef<AbortController | null>(null);

  // --- persistence ---
  // Surface a save failure instead of silently losing chats: a large pasted image/audio can
  // blow the ~5 MB localStorage quota. "degraded" means the transcript persisted but inline
  // media won't survive a reload; "failed" means nothing saved.
  const [storageWarning, setStorageWarning] = useState<string | null>(null);
  useEffect(() => {
    const result = saveConversations(conversations);
    setStorageWarning(
      result === "degraded"
        ? "Storage is full — your chats are saved, but attached images/audio won't survive a reload. Delete old conversations to free space."
        : result === "failed"
          ? "Storage is full — new messages can't be saved and will be lost on reload. Delete old conversations to free space."
          : null,
    );
  }, [conversations]);

  // --- URL routing: reflect the active conversation's id in the hash (#/c/<id>)
  // so a reload / bookmark / back-button lands on the same chat. ---
  useEffect(() => {
    const target =
      view === "library"
        ? "#/library"
        : view === "voice"
          ? "#/voice"
          : view === "settings"
            ? "#/settings"
            : activeId
              ? `#/c/${activeId}`
              : "";
    if (window.location.hash !== target) {
      if (target) window.location.hash = target;
      else history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, [view, activeId]);
  // The hashchange handler is mount-only, so read the latest conversations via a ref
  // rather than a stale closure.
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;
  useEffect(() => {
    const onHash = () => {
      if (window.location.hash === "#/library") {
        setView("library");
        return;
      }
      if (window.location.hash === "#/voice") {
        setView("voice");
        return;
      }
      if (window.location.hash === "#/settings") {
        setView("settings");
        return;
      }
      const id = conversationIdFromHash();
      // Validate the id against live conversations (as the initial-load path does): a hash
      // pointing at a deleted chat (e.g. delete then browser Back) must not become the active
      // id, or the next send streams into a conversation that no longer renders.
      if (id && conversationsRef.current.some((c) => c.id === id)) {
        setActiveId(id);
        setView("chat");
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // --- server polling ---
  const refreshStatus = useCallback(() => getStatus().then(setStatus).catch(() => {}), []);
  // Reconcile the assistant picker against a fresh registry: show it only for a multi-target project
  // (>= 2), defaulting the pick to the primary when the persisted one is gone/unset; clear it otherwise.
  // Writes localStorage whenever it changes the pick so a stale key never lingers (a persisted id that
  // vanished on a server restart, or a demotion to a single/generic backend) — otherwise a later send
  // could post an id the server has never heard of and hard-400.
  const reconcileTargets = useCallback((ts: AssistantTarget[]) => {
    if (ts.length >= 2) {
      setTargets(ts);
      setTargetId((cur) => {
        const next = cur && ts.some((t) => t.id === cur) ? cur : ts[0].id;
        if (next !== cur) localStorage.setItem(TARGET_KEY, next); // persist the reconciled pick
        return next;
      });
    } else {
      setTargets([]);
      setTargetId((cur) => {
        if (cur !== null) localStorage.removeItem(TARGET_KEY); // drop the now-meaningless persisted key
        return null;
      });
    }
  }, []);
  // Re-fetch + reconcile on demand (used by the chat send error path when a stale target 400s).
  const reconcileTargetsFromServer = useCallback(async () => {
    try {
      reconcileTargets(await getAssistants());
    } catch (e) {
      if (e instanceof AssistantsUnavailableError) reconcileTargets([]); // route gone -> clear the picker
      // other errors: transient, leave state as-is (the slow poll retries)
    }
  }, [reconcileTargets]);
  useEffect(() => {
    assistantsUnavailableRef.current = false; // (re)mount: re-probe /api/assistants on this backend
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
        .catch((e) => setError(`Library: ${e}`))
        .finally(() => setLibraryLoaded(true)); // distinguish "still loading" from "empty"
      getTools().then(setTools).catch(() => {}); // tools are optional; empty if none configured
      // Assistant targets: only a multi-target project (>= 2) shows the picker. Reconcile the
      // persisted pick against the live registry, defaulting to the primary (first) when it's gone
      // or unset; a generic/single-target server clears both so no `target` is ever sent. Skip the
      // request entirely once a backend has 404'd the route (an older server that lacks it).
      if (!assistantsUnavailableRef.current) {
        getAssistants()
          .then((ts) => reconcileTargets(ts))
          .catch((e) => {
            // 404 => the route is absent on this backend: clear any picker and stop polling it. Other
            // failures are transient (e.g. a restart mid-flight) — leave state, the next tick retries.
            if (e instanceof AssistantsUnavailableError) {
              assistantsUnavailableRef.current = true;
              reconcileTargets([]);
            }
          });
      }
      getTagRegistry().then(setTagRegistry).catch(() => {}); // tag styles are optional (derived fallback)
      getVoices().then(setVoices).catch(() => {}); // voices are optional (no TTS engine)
      getVoiceModels()
        .then((vm) => setSttAvailable(vm.some((m) => m.kind === "stt")))
        .catch(() => {}); // enables the composer's dictation mic when Whisper is present
      getDoctor().then(setHealth).catch(() => {});
    };
    refreshSlow();
    // Ambient polling is deliberately relaxed: a model load/eject/reload refreshes status
    // immediately on its own (and `pick` fast-polls while a load is in flight), so these
    // intervals only catch out-of-band changes (a server restart, another client). We also
    // pause entirely while the tab is hidden — no point hammering /api/status in the
    // background — and do one immediate refresh when it becomes visible again.
    const STATUS_POLL_MS = 15_000;
    const SLOW_POLL_MS = 60_000;
    const t = setInterval(() => {
      if (!document.hidden) void refreshStatus();
    }, STATUS_POLL_MS);
    const s = setInterval(() => {
      if (!document.hidden) refreshSlow();
    }, SLOW_POLL_MS);
    const onVisible = () => {
      if (!document.hidden) {
        void refreshStatus();
        refreshSlow();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(t);
      clearInterval(s);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshStatus, reconcileTargets]);

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
  // True only when the conversation on screen is the one streaming — drives the cursor and the
  // composer's Stop, so neither bleeds onto a different chat the user switched to (F-7).
  const activeStreaming = isStreaming && streamingConvId === activeId;

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
        const first = await loadModel(name, nCtx === undefined ? settings.contextLength : nCtx);
        setStatus(first);
        // Poll until the server reports ready (or leaves loading). Keep polling as
        // long as the runtime itself allows a load to take (server-reported), so a
        // big 15-20 GB model isn't abandoned mid-load with the UI re-enabling pick().
        const timeoutMs = (first.runtime_load_timeout || 600) * 1000 + 30_000;
        const deadline = Date.now() + timeoutMs;
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

  // Project auto-load: in a project dir (heim.toml [project].model), boot straight
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

  // --- primary view navigation (chat vs the model library grid vs the voice studio) ---
  // The project can turn the Voice surface off ([voice] enabled = false) for a text-only
  // assistant; default on until status loads. Speak-replies default to the project's chat_voice.
  const voiceEnabled = status?.voice_enabled !== false;
  const effectiveTtsVoice = ttsVoice || status?.default_chat_voice || undefined;
  const showChat = useCallback(() => setView("chat"), []);
  const showLibrary = useCallback(() => setView("library"), []);
  const showVoice = useCallback(() => setView("voice"), []);
  const showSettings = useCallback(() => setView("settings"), []);
  // If Voice is disabled while we're on it, fall back to chat.
  useEffect(() => {
    if (!voiceEnabled && view === "voice") setView("chat");
  }, [voiceEnabled, view]);
  const selectConversation = useCallback((id: string) => {
    setActiveId(id);
    setView("chat");
  }, []);
  const startNewChat = useCallback(() => {
    newConversation();
    setView("chat");
  }, [newConversation]);
  // From the Models grid: "Load" loads in place (stays on the grid; the card flips
  // to "Chat" when ready — no jarring auto-switch). "Chat" starts a fresh chat with
  // the loaded model rather than dropping into whatever conversation was open.
  const loadModelInPlace = useCallback(
    (name: string) => {
      if (status?.model !== name) pick(name);
    },
    [status?.model, pick],
  );
  const chatWithLoaded = useCallback(() => startNewChat(), [startNewChat]);
  // Edit a model's user tags: optimistic local update, then persist (server
  // normalizes/dedupes, so reconcile with what it returns).
  const setTags = useCallback(async (name: string, tags: string[]) => {
    setLibrary((lib) => lib.map((m) => (m.name === name ? { ...m, tags } : m)));
    try {
      const res = await setModelTags(name, tags);
      setLibrary((lib) => lib.map((m) => (m.name === name ? { ...m, tags: res.tags } : m)));
    } catch {
      getLibrary().then(setLibrary).catch(() => {}); // reconcile on failure
    }
  }, []);

  // --- core: run a chat completion into an assistant turn ---
  const runCompletion = useCallback(
    async (convId: string, priorMessages: ChatMessage[], assistantId: string) => {
      setStreamingConvId(convId);
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
          // Route this turn to the selected target only when a multi-target registry is present;
          // undefined otherwise leaves routing to the server default (generic/single-target apps).
          target: targets.length >= 2 ? (targetId ?? undefined) : undefined,
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
          } else if (evt.type === "confirm") {
            // The server is holding a write tool call: surface an Approve/Deny card. Do NOT abort
            // the stream — the server resumes and streams the tool result once a decision lands.
            const pending: PendingConfirm = { id: evt.id, tool: evt.tool, args: evt.args, status: "pending" };
            upsertConv(convId, (c) => ({
              ...c,
              updatedAt: Date.now(),
              messages: c.messages.map((m) =>
                m.id === assistantId ? { ...m, confirms: [...(m.confirms ?? []), pending] } : m,
              ),
            }));
          } else if (evt.type === "confirm_resolved") {
            // A user decision clears the card (the tool call/result chips carry it forward); a
            // timeout leaves an auto-denied note so the outcome stays visible.
            upsertConv(convId, (c) => ({
              ...c,
              updatedAt: Date.now(),
              messages: c.messages.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      confirms:
                        evt.reason === "timeout"
                          ? m.confirms?.map((cf) =>
                              cf.id === evt.id
                                ? { ...cf, status: "resolved", approved: evt.approved, reason: "timeout" }
                                : cf,
                            )
                          : m.confirms?.filter((cf) => cf.id !== evt.id),
                    }
                  : m,
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
          // A stale target selection (a persisted/selected id that vanished on a server restart) posts an
          // id the server rejects with a 400 "Unknown target" before the next slow poll reconciles it.
          // Re-fetch the registry now and reconcile the pick (persisting it) so a resend just works; the
          // error still surfaces below so the user knows to resend.
          if (/unknown target/i.test(detail)) void reconcileTargetsFromServer();
          upsertConv(convId, (c) => ({
            ...c,
            messages: c.messages.map((m) =>
              m.id === assistantId ? { ...m, content: m.content || `Error: ${detail}`, error: true } : m,
            ),
          }));
        }
      } finally {
        // The stream is over: strip any confirmation still awaiting a decision (nothing will
        // resolve it now), keeping only resolved ones (e.g. an auto-denied timeout note).
        upsertConv(convId, (c) => ({
          ...c,
          messages: c.messages.map((m) => {
            if (m.id !== assistantId || !m.confirms?.length) return m;
            const kept = m.confirms.filter((cf) => cf.status === "resolved");
            return { ...m, confirms: kept.length ? kept : undefined };
          }),
        }));
        setStreamingConvId(null);
        abortRef.current = null;
      }
    },
    [settings, tools, upsertConv, targets, targetId, reconcileTargetsFromServer],
  );

  // --- send a new user turn ---
  const send = useCallback(async () => {
    const text = input.trim();
    const images = attachments.filter((a) => a.kind === "image" && a.url).map((a) => a.url as string);
    const audios = attachments.filter((a) => a.kind === "audio" && a.url).map((a) => a.url as string);
    const files = attachments
      .filter((a) => a.kind === "text")
      .map((a) => ({ name: a.name ?? "file", text: a.text ?? "" }));
    if ((!text && attachments.length === 0) || isStreaming || !ready) return;

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
  }, [input, attachments, isStreaming, ready, status?.model, activeId, conversations, newConversation, upsertConv, runCompletion]);

  // --- regenerate: drop last assistant turn, re-run the last user turn ---
  const regenerate = useCallback(async () => {
    if (isStreaming || !ready || !activeConv) return;
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
    // Everything up to (not including) the old assistant turn, minus assistant turns that are
    // empty (aborted "..." ghosts) or error banners — so they're neither persisted as permanent
    // ghosts nor replayed to the model (F-8). This filtered list is both what we keep and what
    // we resend, so the two can't diverge.
    const kept = msgs.slice(0, lastAssistant).filter((m) => m.role !== "assistant" || (!!m.content && !m.error));
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
    await runCompletion(activeConv.id, kept, assistantMsg.id);
  }, [isStreaming, ready, status?.model, activeConv, upsertConv, runCompletion]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  // Approve/Deny a pending per-action confirmation. Optimistically flips the card to resolved so
  // the buttons disable immediately; the server's confirm_resolved echo then removes it. The stream
  // is NOT aborted — the server resumes and streams the tool result. Located by confirm id across
  // conversations (only the in-flight assistant turn ever carries a pending one).
  const resolveConfirm = useCallback((id: string, approve: boolean) => {
    setConversations((prev) =>
      prev.map((c) =>
        c.messages.some((m) => m.confirms?.some((cf) => cf.id === id))
          ? {
              ...c,
              messages: c.messages.map((m) =>
                m.confirms?.some((cf) => cf.id === id)
                  ? {
                      ...m,
                      confirms: m.confirms.map((cf) =>
                        cf.id === id ? { ...cf, status: "resolved", approved: approve, reason: "user" } : cf,
                      ),
                    }
                  : m,
              ),
            }
          : c,
      ),
    );
    void confirmAction(id, approve).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

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
  // Re-pin on new/updated messages AND when streaming ends — the hover action row
  // (copy/speak/regenerate) is only rendered once `streaming` flips false, growing the
  // last turn; without re-pinning here it gets clipped at the scroll bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, activeStreaming]);
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

  // Select an assistant target (multi-target projects); persisted per backend so the pick survives
  // a reload. Takes effect on the next chat turn — the server routes per turn.
  const chooseTarget = useCallback((id: string) => {
    setTargetId(id);
    localStorage.setItem(TARGET_KEY, id);
  }, []);

  // TTS voice (a global preference for the Listen button): "" = default OuteTTS.
  const chooseVoice = useCallback((name: string) => {
    setTtsVoice(name);
    if (name) localStorage.setItem("heim.tts_voice", name);
    else localStorage.removeItem("heim.tts_voice");
  }, []);

  // --- attachments (image / audio) ---
  const addAttachments = useCallback((items: Attachment[]) => setAttachments((a) => [...a, ...items]), []);
  const removeAttachment = useCallback((i: number) => setAttachments((a) => a.filter((_, idx) => idx !== i)), []);
  const accept = useMemo(
    // `known` is true once the loaded model is resolved in the library (so its caps
    // are known); until then media is accepted optimistically, not dropped.
    () => ({ image: ready && visionModel, audio: ready && audioModel, known: ready && !!loadedModel }),
    [ready, visionModel, audioModel, loadedModel],
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
      {/* Multi-target projects only ([[assistants]]): pick which instance this turn routes to.
          Single-target/generic servers render nothing here (zero change to the existing app). */}
      {targets.length >= 2 && (
        <TargetSelector targets={targets} selectedId={targetId} onSelect={chooseTarget} />
      )}
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
      {/* Mobile: the sidebar is an overlay drawer (a resizable rail would squeeze the content).
          The persistent IconRail is the closed-state nav; tapping expand opens this. */}
      {isMobile && sidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={closeSidebar} aria-hidden />
          <div className="absolute inset-y-0 left-0 w-[82%] max-w-xs shadow-2xl">
            <Sidebar
              conversations={conversations}
              activeId={activeId}
              view={view}
              onNew={() => {
                startNewChat();
                closeSidebar();
              }}
              onSelect={(id) => {
                selectConversation(id);
                closeSidebar();
              }}
              onShowChat={() => {
                showChat();
                closeSidebar();
              }}
              onShowLibrary={() => {
                showLibrary();
                closeSidebar();
              }}
              onShowVoice={() => {
                showVoice();
                closeSidebar();
              }}
              onShowSettings={() => {
                showSettings();
                closeSidebar();
              }}
              voiceEnabled={voiceEnabled}
              onRename={renameConversation}
              onDelete={deleteConversation}
              onCollapse={closeSidebar}
            />
          </div>
        </div>
      )}
      <div className="flex h-full overflow-hidden">
        {/* When the sidebar is collapsed, a thin icon rail keeps new-chat + Models +
            Voice reachable (and usable on mobile) rather than hiding nav entirely. */}
        {!sidebarOpen && (
          <IconRail
            view={view}
            onExpand={openSidebar}
            onNew={startNewChat}
            onShowLibrary={showLibrary}
            onShowVoice={showVoice}
            onShowSettings={showSettings}
            voiceEnabled={voiceEnabled}
          />
        )}
        <PanelGroup
          direction="horizontal"
          autoSaveId="heim-layout"
          className="h-full min-w-0 flex-1 overflow-hidden"
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
            view={view}
            onNew={startNewChat}
            onSelect={selectConversation}
            onShowChat={showChat}
            onShowLibrary={showLibrary}
            onShowVoice={showVoice}
            onShowSettings={showSettings}
            voiceEnabled={voiceEnabled}
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
            {/* Collapsed-sidebar actions (open + new chat) live in the persistent
                IconRail on the far left, so the top bar stays clean. */}
            <div className="flex items-center gap-1" />
            <div className="flex items-center gap-1.5">
              {/* The chat/LLM runtime badge belongs to the Chat surface only — it's
                  irrelevant on Library/Voice (voice models don't use the runtime). */}
              {view === "chat" && (
                <LoadedModelBadge
                  status={status}
                  loadingName={loadingName}
                  onEject={eject}
                  onShowLibrary={showLibrary}
                />
              )}
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
            </div>
          </header>

          {storageWarning && (
            <div className="mx-auto mt-1 w-full max-w-4xl px-4">
              <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
                <span className="flex-1">{storageWarning}</span>
                <button
                  type="button"
                  onClick={() => setStorageWarning(null)}
                  className="shrink-0 opacity-70 hover:opacity-100"
                  aria-label="Dismiss storage warning"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}

          {view === "library" ? (
            <LibraryView
              library={library}
              loaded={libraryLoaded}
              error={error && error.startsWith("Library: ") ? error.slice("Library: ".length) : null}
              status={status}
              loadingName={loadingName}
              onLoad={loadModelInPlace}
              onChat={chatWithLoaded}
              onSetTags={setTags}
              tagRegistry={tagRegistry}
            />
          ) : view === "voice" ? (
            <VoiceView />
          ) : view === "settings" ? (
            <SettingsView
              status={status}
              library={library}
              activeId={activeId}
              settings={settings}
              onChange={updateSettings}
              onReloadContext={reloadWithContext}
              busy={loadingName != null}
              voices={voices}
              ttsVoice={ttsVoice}
              onChooseVoice={chooseVoice}
            />
          ) : (
          <>
          {(error || status?.error) && (
            <div className="mx-auto mt-1 w-full max-w-4xl px-4">
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error && <div>{error}</div>}
                {status?.error && (
                  <details className={error ? "mt-1" : undefined}>
                    <summary className="cursor-pointer">
                      {friendlyRuntimeError(status.error) ?? "The model runtime stopped unexpectedly."}
                    </summary>
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
                  streaming={activeStreaming}
                  ready={ready}
                  autoFocus
                  leftSlot={composerControls}
                  attachments={attachments}
                  accept={accept}
                  canDictate={sttAvailable}
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
                      streaming={activeStreaming && i === messages.length - 1 && m.role === "assistant"}
                      canRegenerate={!activeStreaming && i === lastAssistantIndex}
                      onRegenerate={regenerate}
                      onResolveConfirm={resolveConfirm}
                      ttsVoice={effectiveTtsVoice}
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
                    streaming={activeStreaming}
                    ready={ready}
                    leftSlot={composerControls}
                    attachments={attachments}
                    accept={accept}
                    onAdd={addAttachments}
                    onRemove={removeAttachment}
                  />
                  <p className="mt-2 text-center text-[11px] text-muted-foreground">
                    heim runs your model locally. Responses may be inaccurate.
                  </p>
                </div>
              </div>
            </>
          )}
          </>
          )}
        </main>
        </Panel>
        </PanelGroup>
      </div>
    </TooltipProvider>
  );
}
