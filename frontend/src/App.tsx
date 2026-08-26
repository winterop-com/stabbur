import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type TransitionEvent } from "react";
import { ArrowDown, PanelRight, Search, X } from "lucide-react";
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
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Composer } from "@/components/Composer";
import { HealthMenu } from "@/components/HealthMenu";
import { IconRail } from "@/components/IconRail";
import { useIsMobile } from "@/lib/use-mobile";
import { ViewTitleProvider, useViewTitleState } from "@/lib/view-title";
import type { TagRegistry } from "@/lib/tags";
import { MessageItem } from "@/components/MessageItem";
import { ModelSelector } from "@/components/ModelSelector";
import { TargetSelector } from "@/components/TargetSelector";
import { LibraryView } from "@/components/LibraryView";
import { VoiceView } from "@/components/VoiceView";
import { ChatSettingsPanel } from "@/components/ChatSettingsPanel";
import { CommandPalette, opensPalette } from "@/components/CommandPalette";
import { SettingsDialog } from "@/components/SettingsDialog";
import { Sidebar } from "@/components/Sidebar";
import { StatusBar } from "@/components/StatusBar";
import { DEFAULT_SETTINGS, baselineServers, deriveTitle, serverScopes, uid, type Settings } from "@/lib/store";
import { loadConversations, saveConversations } from "@/lib/history";
import { applyModelTitle, requestConversationTitle } from "@/lib/title";
import { greetingFor } from "@/lib/greeting";
import { useMcpServers } from "@/lib/useMcpServers";
import type { Attachment, ChatMessage, Conversation, PendingConfirm, ToolMarker } from "@/lib/types";
import { exportConversationMarkdown, exportConversationPdf } from "@/lib/export";
import { useTheme } from "@/lib/useTheme";

// The selected assistant target persists per backend (a stabbur project is served on one origin), so
// two projects served on different ports keep independent picks; a same-origin restart restores it.
const TARGET_KEY = `stabbur.target:${window.location.host}`;

/** Parse the active conversation id from the URL hash (#/c/<id>), or null. */
function conversationIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/c\/(.+)$/);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Which primary surface to show: the chat, the model library grid, or the voice studio.
 *  Settings is deliberately absent — it is a dialog over whichever surface you are on. */
type View = "chat" | "library" | "voice";

/** How far the history has got. The store is IndexedDB, so this is a real state the app renders in,
 *  not a formality: "loading" is what stops an empty first paint being read — or SAVED — as
 *  "no history", and "unavailable" is a store that could not be read, where the only safe thing to
 *  do is keep working and write nothing over it. Only "ready" persists. */
type HistoryState = "loading" | "ready" | "unavailable";

// Persistence is debounced, with a ceiling. A stream rebuilds the active conversation on every
// token, and writing each of those straight through would be a transaction per token. So a change
// schedules a flush this far out...
const SAVE_DEBOUNCE_MS = 300;
// ...but never further out than this from the FIRST unsaved change, because a stream produces a
// change every few milliseconds and a plain debounce would keep pushing the write past the end of
// a long answer. Between them: prompt when idle, at least twice a second while generating.
const SAVE_MAX_DEFER_MS = 2000;

/** The resizable panels, in group order. Matches each `<Panel id>` (and so `data-panel-id`). */
type PanelId = "sidebar" | "main" | "chat-settings";

// CHROME IS A WIDTH, NEVER A SHARE OF THE DISPLAY — and the percentages in this file are only ever
// a width re-expressed for a library that speaks in shares of the panel group. A rail sized at 18%
// is 230px on a 1280px screen and 461px on a 2560px one, so on a big monitor every frame around the
// text grows while the text does not: the app reads as though it had been zoomed out, which is
// exactly the complaint. `groupWidth` (measured below) is what converts one to the other.
//
// The sidebar's own numbers. The default matches the sibling app's fixed ~240px rail; the bounds
// are a drag range around it, not a layout constraint — a rail wider than ~420px is a reader's
// choice, not a design.
const SIDEBAR_DEFAULT_PX = 240;
const SIDEBAR_MIN_PX = 180;
const SIDEBAR_MAX_PX = 420;
// How wide the chat settings rail has to be to be worth having. Below this its sliders lose their
// track and "Max response tokens" wraps to three lines — at which point an overlay that covers the
// chat is strictly better than a rail that ruins it. Raised from 320 when the panel's prose went to
// `text-sm`: 320px of rail held 11px sentences, and a slider description at 14px wants the extra
// 40px or it runs to four lines under every knob.
const CHAT_SETTINGS_MIN_PX = 360;
// And the width below which it stops being a rail at all. 48px of icon rail + 360px of settings +
// ~560px of chat is ~968px, rounded up to Tailwind's lg so the chat keeps a comfortable 616px. This
// is about narrow, not about phones: a desktop window snapped to half a screen lands here too.
const CHAT_SETTINGS_RAIL_MIN_VIEWPORT = 1024;
// ...which makes this the narrowest panel group the rail is ever laid out in: the breakpoint less
// the collapsed sidebar's icon rail (`w-12`). Never derive the floor from anything narrower — see
// `chatSettingsMin`. 360 of 976 is 36.9%, still under the rail's 40% maximum.
const NARROWEST_RAIL_GROUP = CHAT_SETTINGS_RAIL_MIN_VIEWPORT - 48;

/** One panel's pixel intent as the percentage of `group` the library needs, clamped to sane bounds. */
function pctOfGroup(px: number, group: number): number {
  return Math.min(100, Math.max(0, (px / group) * 100));
}

/** A programmatic collapse/expand in flight — see `beginRailAnim` for why the content is pinned. */
type RailAnim = {
  /** Which rail is moving — the right panel unmounts its content on collapse, so it needs to know. */
  rail: PanelId;
  /** Bumped per toggle, so re-toggling mid-animation re-measures rather than reusing stale widths. */
  nonce: number;
  /** Each panel's width the instant before the toggle — the fallback for one collapsing to nothing. */
  before: Record<string, number>;
  /** Pinned content width per panel id, in px. Null until the layout effect has read the targets. */
  widths: Record<string, number> | null;
};

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
  const { mode, toggleMode, theme, setTheme } = useTheme();

  // Server state.
  const [status, setStatus] = useState<Status | null>(null);
  const [library, setLibrary] = useState<LibModel[]>([]);
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [tagRegistry, setTagRegistry] = useState<TagRegistry>({}); // first-class tag colors/icons
  const [tools, setTools] = useState<ToolInfo[]>([]);
  // Tools are optional (empty when no MCP server is switched on) — a failure here is never fatal.
  // Also called straight after switching a server on, which attaches its tools live: waiting for
  // the slow poll would show a stale list for up to a refresh interval.
  const refreshTools = useCallback(() => {
    getTools()
      .then(setTools)
      .catch(() => {});
  }, []);
  // The MCP server catalogue lives here, not in the settings panel: a chat's tool allow-list falls
  // back to a baseline derived from each server's scope, and the send path below needs that answer
  // whether or not the panel was ever opened. One instance, one fetch, one optimistic update path.
  const mcp = useMcpServers(refreshTools);
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
  const [ttsVoice, setTtsVoice] = useState<string>(() => localStorage.getItem("stabbur.tts_voice") || "");
  const [ttsSpeed, setTtsSpeed] = useState<number>(() => {
    const raw = Number(localStorage.getItem("stabbur.tts_speed"));
    return Number.isFinite(raw) && raw >= 0.25 && raw <= 2 ? raw : 1;
  });
  const chooseSpeed = useCallback((v: number) => {
    setTtsSpeed(v);
    try {
      localStorage.setItem("stabbur.tts_speed", String(v));
    } catch {
      /* storage full/blocked: the pick still applies this session */
    }
  }, []);
  const [health, setHealth] = useState<DoctorReport | null>(null);
  const [loadingName, setLoadingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // App state. The history is READ ASYNCHRONOUSLY (IndexedDB), so unlike every other initialiser
  // here these two start empty and are filled by the effect below. `historyState` is what keeps
  // that honest — see the type, and the two effects that gate on it.
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [historyState, setHistoryState] = useState<HistoryState>("loading");
  // Settings live per-conversation (see activeSettings below). This holds the
  // draft used before a conversation exists (the empty state); it seeds the first
  // conversation on send, then resets — so nothing carries between chats.
  const [draftSettings, setDraftSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [view, setView] = useState<View>(() =>
    window.location.hash === "#/library" ? "library" : window.location.hash === "#/voice" ? "voice" : "chat",
  );
  // Settings used to be a route (#/settings). It is a dialog now, but an old bookmark or a
  // back-button into a pre-dialog history entry still means "show me settings", so honour it —
  // the hash effect below then rewrites the URL to the surface actually on screen.
  const [settingsOpen, setSettingsOpen] = useState(() => window.location.hash === "#/settings");

  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  // Owned here, not in the panel: the panel unmounts while collapsed, and an affordance that
  // opens it (the tools pill) has to land on the Tools tab, not on whatever it showed last.
  const [chatSettingsTab, setChatSettingsTab] = useState<"parameters" | "tools">("parameters");
  const [paletteOpen, setPaletteOpen] = useState(false);

  // --- resizable layout: imperative handles to collapse/expand the rails. ---
  const leftPanel = useRef<ImperativePanelHandle>(null);
  const rightPanel = useRef<ImperativePanelHandle>(null);
  // Animate programmatic collapse/expand, but never during a manual drag (which
  // must track the cursor 1:1). react-resizable-panels sets flex inline, so a CSS
  // flex transition animates the collapse — suppressed while a handle is dragging.
  const [dragging, setDragging] = useState(false);
  // react-resizable-panels animates the panel via inline flex-grow; transition that.
  const railTransition = dragging ? "" : "transition-[flex-grow] duration-200 ease-out";
  // flex-grow is a *layout* property, so on its own that transition re-runs layout every frame and
  // the panels' text re-wraps all the way in — the "squished text growing into its space" look. So
  // for the 200ms of a toggle we pin each panel's content to the width it ends at and let the panel
  // (which the library already gives `overflow: hidden`) clip it: one layout, then a pure slide.
  const layoutRef = useRef<HTMLDivElement>(null);
  const [railAnim, setRailAnim] = useState<RailAnim | null>(null);
  const railAnimNonce = useRef(0);
  // Snapshot each panel's width the instant before the toggle, while the old layout is still the
  // rendered one — that is the only exact record of it once the transition is under way.
  const beginRailAnim = useCallback((rail: PanelId) => {
    const root = layoutRef.current;
    if (!root) return;
    const before: Record<string, number> = {};
    for (const el of root.querySelectorAll<HTMLElement>("[data-panel-id]")) {
      before[el.dataset.panelId ?? ""] = el.getBoundingClientRect().width;
    }
    railAnimNonce.current += 1;
    setRailAnim({ rail, nonce: railAnimNonce.current, before, widths: null });
  }, []);
  // Post-commit, pre-paint: React has just written the *target* flex-grow inline on every panel
  // (only the rendered width animates), so every final width is derivable now — before the browser
  // has painted a single intermediate frame. Panels are flex-basis:0 with flex-grow set to their
  // percentage of the group, so their widths sum to the group's content box however far the
  // transition has got; that sum splits by the targets. It has to be measured *here* rather than
  // alongside the snapshot above, because expanding the sidebar unmounts the collapsed-state icon
  // rail and so widens the group by its width. Reading the DOM (not getSize()) also picks up a rail
  // the user has dragged to a custom width, and a window resized while a rail sat collapsed.
  useLayoutEffect(() => {
    const root = layoutRef.current;
    if (!railAnim || railAnim.widths || !root) return;
    const panels = [...root.querySelectorAll<HTMLElement>("[data-panel-id]")];
    const total = panels.reduce((sum, el) => sum + el.getBoundingClientRect().width, 0);
    const widths: Record<string, number> = {};
    for (const el of panels) {
      const id = el.dataset.panelId ?? "";
      const target = parseFloat(el.style.flexGrow) || 0;
      // A panel collapsing to nothing has no final layout to settle into, so it keeps the one it
      // already had and slides out behind the clip instead of being crushed to zero.
      widths[id] = target > 0 ? (total * target) / 100 : (railAnim.before[id] ?? 0);
    }
    setRailAnim((a) => (a && a.nonce === railAnim.nonce ? { ...a, widths } : a));
  }, [railAnim]);
  // Release the pin. transitionend is authoritative — unlike a hardcoded timeout it can't drift out
  // of sync with the duration — but a transition that never runs (a toggle that didn't change the
  // width, prefers-reduced-motion, a background tab) must not leave content pinned to a width that
  // then goes stale, so a timer and any window resize release it too.
  useEffect(() => {
    if (!railAnim) return;
    const release = () => setRailAnim(null);
    const timer = setTimeout(release, 600);
    window.addEventListener("resize", release);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("resize", release);
    };
  }, [railAnim]);
  const onRailTransitionEnd = useCallback((e: TransitionEvent<HTMLElement>) => {
    // Panels only; every button and hairline in here transitions something of its own.
    if (e.propertyName === "flex-grow" && (e.target as HTMLElement).hasAttribute?.("data-panel-id")) {
      setRailAnim(null);
    }
  }, []);
  // The sidebar's width in PIXELS — the thing the reader actually chose, and the only form of it
  // that survives a window resize (see SIDEBAR_DEFAULT_PX). The panel's percentage is derived from
  // this on every group resize; this is never derived from the panel's percentage, because the
  // group's resize and the panel's re-layout don't land in the same frame and reading the
  // percentage mid-flight would record the scaled width and bake the zoom right back in.
  const [sidebarPx, setSidebarPx] = useState(SIDEBAR_DEFAULT_PX);
  /** The group width `sidebarPx` was last expressed against — see `onSidebarResize` for why. */
  const heldAtGroup = useRef(0);
  const onHandleDragging = useCallback((isDragging: boolean) => {
    setDragging(isDragging);
    if (isDragging) setRailAnim(null); // a drag tracks the cursor 1:1 — nothing may be pinned
  }, []);
  /** The pinned width for one panel's content, or undefined when nothing is animating. */
  const pinned = useCallback(
    (id: PanelId) => (railAnim?.widths ? { width: railAnim.widths[id] } : undefined),
    [railAnim],
  );
  // On a narrow window a resizable rail squeezes the content, so it becomes an overlay instead (see
  // the branches below); wide enough, and both stay resizable panels. The two rails have different
  // thresholds because they need different room: a list of chat titles reads fine at 200px, a column
  // of labelled sliders does not.
  const isMobile = useIsMobile();
  const chatSettingsAsSheet = useIsMobile(CHAT_SETTINGS_RAIL_MIN_VIEWPORT);
  // The rail's minSize is a percentage of the group, so translate the px floor into whatever
  // percentage *this* group makes it — that, plus the breakpoint above, is what guarantees the rail
  // is never rendered narrower than CHAT_SETTINGS_MIN_PX at any window size. Floored at the
  // narrowest group the rail is used in: below the breakpoint the panel is a Sheet and the
  // constraint is moot, but a percentage that ballooned down there would leak back as an over-wide
  // rail on the way up, because the media query and the group's own resize don't land in the same
  // frame (a 786px group would ask for 41%, and the rail would come back at its 40% maximum).
  const [groupWidth, setGroupWidth] = useState(0);
  const chatSettingsMin = (CHAT_SETTINGS_MIN_PX / Math.max(groupWidth, NARROWEST_RAIL_GROUP)) * 100;
  useEffect(() => {
    const el = layoutRef.current?.querySelector<HTMLElement>("[data-panel-group]");
    if (!el) return;
    const measure = () => setGroupWidth(el.getBoundingClientRect().width);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  // The sidebar's bounds, as the percentages *this* group makes them. Before the group has been
  // measured these fall back to the old fixed shares rather than to 0 — a minSize/maxSize of zero
  // for one frame would crush the panel and the library would never give the width back.
  const groupMeasured = groupWidth > 0;
  const sidebarMin = groupMeasured ? pctOfGroup(SIDEBAR_MIN_PX, groupWidth) : 12;
  // Never more than half the group: at 834px SIDEBAR_MAX_PX alone would let a drag take 50%+ of the
  // window for a list of chat titles, and a maxSize the current size exceeds is what leaks a wrong
  // width back on the way up.
  const sidebarMax = groupMeasured ? pctOfGroup(Math.min(SIDEBAR_MAX_PX, groupWidth / 2), groupWidth) : 32;
  // HOLD THE RAIL AT ITS WIDTH as the window changes — this is the whole fix. `defaultSize` alone
  // cannot do it: it is read once at mount, `autoSaveId` restores a *percentage* over the top of
  // it, and either way a percentage re-scales with every resize. So the width is re-expressed
  // imperatively whenever the group changes. Idempotent by construction (resize(p) makes the panel
  // exactly `sidebarPx` wide again), skipped mid-drag so the handle tracks the cursor 1:1, and
  // skipped while collapsed — on mobile and behind the icon rail the panel is at zero and
  // resizing it would expand it.
  useEffect(() => {
    const p = leftPanel.current;
    if (!p || !groupMeasured || dragging || isMobile || !sidebarOpen || p.isCollapsed()) return;
    heldAtGroup.current = groupWidth;
    const want = Math.min(Math.max(sidebarPx, SIDEBAR_MIN_PX), SIDEBAR_MAX_PX, groupWidth / 2);
    p.resize(pctOfGroup(want, groupWidth));
  }, [groupWidth, groupMeasured, sidebarPx, dragging, isMobile, sidebarOpen]);
  /**
   * Record a size the READER chose — a handle drag, or the keyboard resize the handle offers as a
   * `separator`. Both arrive here, so this is the one place a new width is learned.
   *
   * THREE THINGS MUST NOT BE LEARNED FROM, and each is a way the zoom crept back in:
   *
   * - A percentage the WINDOW changed the meaning of. `heldAtGroup` is the group width this rail was
   *   last expressed against, so a callback that arrives before the effect above has caught up with
   *   a resize is dropped — the effect is about to set the right size anyway. (Its own `resize()`
   *   passes the guard and re-records the identical width, which is a no-op.)
   * - A collapse, which reports 0. Collapsing is not choosing a narrow rail.
   * - The expand restore. The library reinstates the *percentage* it collapsed from, which at a
   *   different window size is a different width; `sidebarOpen` is still false in this callback's
   *   closure at that moment, so it is dropped and the effect re-applies the real width instead.
   */
  const onSidebarResize = useCallback(
    (pct: number) => {
      if (!sidebarOpen || pct <= 0 || groupWidth <= 0 || heldAtGroup.current !== groupWidth) return;
      setSidebarPx((pct / 100) * groupWidth);
    },
    [groupWidth, sidebarOpen],
  );
  // Where the main panel starts, which is exactly how wide the rail column is right now: the icon
  // rail's 48px while collapsed, the sidebar's own width (drag-resized or not) while expanded, plus
  // the handle between them. The status bar's Settings segment matches it, so the bar's divider
  // continues the rail's right edge straight down. Measured rather than derived: a percentage-sized
  // panel the user has dragged has no width this code could compute. The sidebar panel is always
  // mounted (it collapses to zero), so observing it catches every case — a drag, a toggle, and the
  // icon rail mounting alongside it, which happens in the same commit.
  const [railWidth, setRailWidth] = useState(48);
  useEffect(() => {
    const root = layoutRef.current;
    if (!root) return;
    const measure = () => {
      const main = root.querySelector<HTMLElement>('[data-panel-id="main"]');
      if (main) setRailWidth(Math.round(main.getBoundingClientRect().left - root.getBoundingClientRect().left));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    const sidebar = root.querySelector<HTMLElement>('[data-panel-id="sidebar"]');
    if (sidebar) observer.observe(sidebar);
    return () => observer.disconnect();
  }, []);
  const toggleSidebar = useCallback(() => {
    const p = leftPanel.current;
    if (!p) return;
    beginRailAnim("sidebar");
    if (p.isCollapsed()) p.expand();
    else p.collapse();
  }, [beginRailAnim]);
  const openSidebar = useCallback(() => {
    if (isMobile) setSidebarOpen(true); // open the overlay drawer, don't expand the rail
    else {
      beginRailAnim("sidebar");
      leftPanel.current?.expand();
    }
  }, [isMobile, beginRailAnim]);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  // Cmd/Ctrl+K anywhere opens the palette; it also closes it, so the chord toggles.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!opensPalette(e)) return;
      e.preventDefault();
      setPaletteOpen((v) => !v);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
  const toggleChatSettings = useCallback(() => {
    // As a Sheet there is no rail to widen, so nothing to animate and nothing to pin.
    if (chatSettingsAsSheet) {
      setChatSettingsOpen((v) => !v);
      return;
    }
    const p = rightPanel.current;
    if (!p) return;
    beginRailAnim("chat-settings");
    if (p.isCollapsed()) p.expand();
    else p.collapse();
  }, [chatSettingsAsSheet, beginRailAnim]);
  const closeChatSettings = useCallback(() => setChatSettingsOpen(false), []);
  // Crossing a breakpoint collapses the rail to 0 (the overlay replaces it) — and, via onCollapse,
  // leaves it closed, so neither direction ever strands one open. Crossing back with it "open"
  // restores the rail so it doesn't vanish on resize/rotate. One effect per rail: they have
  // different thresholds, and each must react only to its own.
  useEffect(() => {
    const p = leftPanel.current;
    if (!p) return;
    if (isMobile) p.collapse();
    else if (sidebarOpen) p.expand();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMobile]);
  useEffect(() => {
    const p = rightPanel.current;
    if (!p) return;
    if (chatSettingsAsSheet) p.collapse();
    else if (chatSettingsOpen) p.expand();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatSettingsAsSheet]);

  // Chat state.
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]); // pending image/audio/document attachments
  // Which conversation is currently streaming (null = none). Tracked by id rather than a
  // global boolean so streaming UI (cursor, Stop) only shows on the conversation actually
  // streaming — switching away no longer makes another chat look like it's streaming, nor
  // lets its Stop button abort the real one (F-7).
  const [streamingConvId, setStreamingConvId] = useState<string | null>(null);
  const isStreaming = streamingConvId !== null; // any stream in flight (one at a time)
  const abortRef = useRef<AbortController | null>(null);

  // --- persistence ---
  const [storageWarning, setStorageWarning] = useState<string | null>(null);

  // THE READ. One shot, on mount. Everything about it is arranged around the fact that the app is
  // already on screen and usable before it lands.
  useEffect(() => {
    let cancelled = false;
    void loadConversations().then((res) => {
      if (cancelled) return;
      if (!res.ok) {
        // The store could not be read. Not the same as "there is nothing" — so the app keeps
        // working and writes NOTHING, rather than saving a fresh empty history over the top of
        // one that is sitting there unreadable.
        setHistoryState("unavailable");
        setStorageWarning(
          "This browser won't let stabbur open its chat storage, so nothing from this session will be saved.",
        );
        return;
      }
      // A chat the user started before the read landed is live state and must survive it: merge,
      // and let it keep the head of the list rather than replacing it wholesale.
      setConversations((live) => {
        const started = new Set(live.map((c) => c.id));
        return [...live, ...res.conversations.filter((c) => !started.has(c.id))];
      });
      setActiveId((current) => {
        if (current) return current; // already in a chat: don't yank the surface out from under it
        // The deep link resolves HERE rather than at mount, because at mount there was nothing to
        // resolve it against. The hash still says what the app was opened with because the effect
        // that rewrites it is gated on this same state — see below.
        const fromUrl = conversationIdFromHash();
        if (fromUrl && res.conversations.some((c) => c.id === fromUrl)) return fromUrl;
        const newest = [...res.conversations].sort((a, b) => b.updatedAt - a.updatedAt)[0];
        return newest ? newest.id : null;
      });
      setHistoryState("ready");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // THE WRITE. Debounced with a ceiling (see SAVE_DEBOUNCE_MS / SAVE_MAX_DEFER_MS) and gated on
  // "ready", which is the whole defence against the empty-state race: until the read has finished
  // there is nothing here worth writing, and what IS here is an empty list that would erase
  // everything. The store writes only the rows that changed, so a flush mid-stream is one small
  // message row, not the transcript.
  const latestConversations = useRef(conversations);
  latestConversations.current = conversations;
  const saveDeadline = useRef(0);
  const flushHistory = useCallback(() => {
    saveDeadline.current = 0;
    void saveConversations(latestConversations.current).then((result) =>
      setStorageWarning(
        result === "failed"
          ? "Chat storage couldn't be written — recent messages may not survive a reload."
          : null,
      ),
    );
  }, []);
  useEffect(() => {
    if (historyState !== "ready") return;
    if (saveDeadline.current === 0) saveDeadline.current = Date.now() + SAVE_MAX_DEFER_MS;
    const wait = Math.max(0, Math.min(SAVE_DEBOUNCE_MS, saveDeadline.current - Date.now()));
    const timer = setTimeout(flushHistory, wait);
    return () => clearTimeout(timer);
  }, [conversations, historyState, flushHistory]);
  // A tab being hidden or closed must not take the debounce window's worth of messages with it.
  // visibilitychange is the one lifecycle event that fires reliably on a real close, and an
  // IndexedDB write started here is allowed to finish.
  useEffect(() => {
    if (historyState !== "ready") return;
    const onHidden = () => {
      if (document.visibilityState === "hidden") flushHistory();
    };
    document.addEventListener("visibilitychange", onHidden);
    return () => document.removeEventListener("visibilitychange", onHidden);
  }, [historyState, flushHistory]);

  // --- URL routing: reflect the active conversation's id in the hash (#/c/<id>)
  // so a reload / bookmark / back-button lands on the same chat. ---
  // The one place app state maps to a hash. Derived rather than computed inside the effect so the
  // mount-only handler below can read the current answer from a ref (see #/settings there).
  const hashTarget =
    view === "library" ? "#/library" : view === "voice" ? "#/voice" : activeId ? `#/c/${activeId}` : "";
  useEffect(() => {
    // NOT WHILE THE HISTORY IS STILL LOADING. `activeId` is null until the read lands, so a chat
    // deep link would map to an empty `hashTarget` and this would replaceState the #/c/<id> away
    // before there was anything to match it against — the app would come up on a new chat and the
    // link would be gone from the URL bar. The read resolves it; this takes over afterwards.
    if (historyState === "loading") return;
    if (window.location.hash === hashTarget) return;
    if (hashTarget) window.location.hash = hashTarget;
    else history.replaceState(null, "", window.location.pathname + window.location.search);
  }, [hashTarget, historyState]);
  // The hashchange handler is mount-only, so read the latest conversations (and the hash the app
  // state currently maps to) via refs rather than a stale closure.
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;
  const hashTargetRef = useRef(hashTarget);
  hashTargetRef.current = hashTarget;
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
        // A dead route kept honest: open the dialog and put the hash back to whatever surface is
        // actually showing. replaceState (not assignment) so this doesn't re-enter as a second
        // hashchange, and because the effect above only fires when `hashTarget` itself changes —
        // which this doesn't, so nothing else would ever clean the stale hash up.
        setSettingsOpen(true);
        const target = hashTargetRef.current;
        history.replaceState(null, "", target || window.location.pathname + window.location.search);
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
      refreshTools();
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
  }, [refreshStatus, reconcileTargets, refreshTools]);

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
  // What the one top bar reads on the left. Chat answers for itself — the conversation's own name is
  // real information the bar showed nowhere, and "New chat" is the honest answer before there is
  // one. Library and Voice publish theirs (lib/view-title), and a record from a surface that is no
  // longer on screen is ignored rather than raced against.
  const [publishedTitle, publishViewTitle] = useViewTitleState();
  const published = publishedTitle?.view === view ? publishedTitle : null;
  const headerTitle = view === "chat" ? (activeConv?.title ?? "New chat") : (published?.title ?? "");
  const headerChip = view === "chat" ? null : (published?.chip ?? null);
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

  // Every server this UI knows of, with the scope that decides whether a fresh chat may call it.
  // While the catalogue is still in flight (mount) every attached server reads as external, which
  // resolves to the baseline-on side — a sub-second window that errs toward the old behavior rather
  // than stripping a project's own tools off its first turn.
  const knownServers = useMemo(
    () => serverScopes(mcp.servers ?? [], tools.map((t) => t.server)),
    [mcp.servers, tools],
  );
  // Which servers *this* conversation may call: its own allow-list once the user has chosen, else
  // the baseline. Not a denylist — a server switched on here is switched on for the machine, so
  // "everything running" would silently carry into every chat opened afterwards.
  const allowedServers = useMemo(
    () => new Set(settings.enabledServers ?? baselineServers(knownServers)),
    [settings.enabledServers, knownServers],
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

  // Project auto-load: in a project dir (stabbur.toml [project].model), boot straight
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
      titledBy: "derived", // a placeholder, and replaceable by everything that follows
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

  // A name someone typed. `titledBy` is what stops the model's background title from landing on top
  // of it two seconds later, and what stops the first send from replacing it with a slice of the
  // message — a chat can be renamed before it has been sent to.
  const renameConversation = useCallback(
    (id: string, title: string) => upsertConv(id, (c) => ({ ...c, title, titledBy: "user" })),
    [upsertConv],
  );

  // --- primary view navigation (chat vs the model library grid vs the voice studio) ---
  // The project can turn the Voice surface off ([voice] enabled = false) for a text-only
  // assistant; default on until status loads. Speak-replies default to the project's chat_voice.
  const voiceEnabled = status?.voice_enabled !== false;
  const effectiveTtsVoice = settings.ttsVoice ?? (ttsVoice || status?.default_chat_voice || undefined);
  const effectiveTtsSpeed = settings.ttsSpeed ?? ttsSpeed;
  const showChat = useCallback(() => setView("chat"), []);
  const showLibrary = useCallback(() => setView("library"), []);
  const showVoice = useCallback(() => setView("voice"), []);
  // Not navigation: Settings opens over whatever surface you are on, and leaves it there.
  const openSettings = useCallback(() => setSettingsOpen(true), []);
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
  // Returns what the assistant actually said — the streamed content, and nothing else: not the
  // reasoning, not an error banner this function wrote into the turn itself. `send` names the
  // conversation from it (lib/title), and a title drawn from stabbur's own error text would be a
  // conversation called "Error: runtime unreachable".
  const runCompletion = useCallback(
    async (convId: string, priorMessages: ChatMessage[], assistantId: string): Promise<string> => {
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

      // Token accounting for this turn. The runtime reports authoritative usage only at the
      // end of each round, so tick a live estimate from streamed deltas (llama.cpp emits one
      // token per delta) and correct it with the real numbers when they land.
      const startedAt = Date.now();
      let assistantText = ""; // the reply as it arrives, for the caller (see the note above)
      let firstTokenAt: number | null = null; // set on the first streamed delta
      let promptTokens = 0;
      let usageCompletion = 0; // authoritative, summed across rounds
      let deltas = 0; // live estimate: streamed content + reasoning chunks
      // llama.cpp reports its own decode timings; summed across rounds they beat any
      // client-side inference, which is inflated by network and render latency.
      let predictedTokens = 0;
      let predictedMs = 0;
      let promptMs = 0;
      const statsFor = (tokens: number) => {
        const now = Date.now();
        // Prefer the runtime's measurement of its own decode rate. Falling back to arrival
        // times, measure from the FIRST token over the tokens that followed it — measuring
        // from the request would fold prompt processing in, making the figure ramp from ~0.
        const genSeconds = firstTokenAt != null ? (now - firstTokenAt) / 1000 : 0;
        const measured = predictedMs > 0 ? predictedTokens / (predictedMs / 1000) : 0;
        return {
          promptTokens,
          completionTokens: tokens,
          seconds: (now - startedAt) / 1000,
          ttftSeconds: promptMs > 0 ? promptMs / 1000 : firstTokenAt != null ? (firstTokenAt - startedAt) / 1000 : 0,
          tokensPerSecond: measured || (genSeconds > 0 && tokens > 1 ? (tokens - 1) / genSeconds : 0),
        };
      };
      const markFirstToken = () => {
        if (firstTokenAt == null) firstTokenAt = Date.now();
      };
      const stampStats = () => {
        const tokens = usageCompletion || deltas;
        if (!tokens) return;
        const stats = statsFor(tokens);
        upsertConv(convId, (c) => ({
          ...c,
          messages: c.messages.map((m) => (m.id === assistantId ? { ...m, stats } : m)),
        }));
      };

      try {
        // The allow-list, always explicit: the tools of the servers this chat may call, minus the
        // ones switched off inside them. Sent even when it is empty (the server reads `[]` as "no
        // tools"), because omitting it means *all* attached tools — which is how a server started
        // for one question ended up live in every later chat.
        const disabled = new Set(settings.disabledTools);
        const enabledTools = tools
          .filter((t) => allowedServers.has(t.server) && !disabled.has(t.name))
          .map((t) => t.name);

        for await (const evt of streamChat(wire, ctrl.signal, {
          maxTokens: settings.maxTokens ?? undefined,
          temperature: settings.temperature ?? undefined,
          topP: settings.topP ?? undefined,
          topK: settings.topK ?? undefined,
          minP: settings.minP ?? undefined,
          repeatPenalty: settings.repeatPenalty ?? undefined,
          useTools: settings.useTools,
          enabledTools,
          systemPrompt: settings.systemPrompt,
          reasoning: settings.reasoning,
          // Route this turn to the selected target only when a multi-target registry is present;
          // undefined otherwise leaves routing to the server default (generic/single-target apps).
          target: targets.length >= 2 ? (targetId ?? undefined) : undefined,
        })) {
          if (evt.type === "token") {
            markFirstToken();
            assistantText += evt.text;
            deltas += 1;
            const live = statsFor(usageCompletion + deltas);
            upsertConv(convId, (c) => ({
              ...c,
              updatedAt: Date.now(),
              messages: c.messages.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + evt.text, stats: live } : m,
              ),
            }));
          } else if (evt.type === "reasoning") {
            markFirstToken();
            deltas += 1;
            const live = statsFor(usageCompletion + deltas);
            upsertConv(convId, (c) => ({
              ...c,
              updatedAt: Date.now(),
              messages: c.messages.map((m) =>
                m.id === assistantId ? { ...m, reasoning: (m.reasoning ?? "") + evt.text, stats: live } : m,
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
          } else if (evt.type === "usage") {
            promptTokens = Math.max(promptTokens, evt.promptTokens);
            usageCompletion += evt.completionTokens;
            deltas = 0; // that round is now counted authoritatively; keep estimating the next
            if (evt.timings) {
              predictedTokens += evt.timings.predictedTokens;
              predictedMs += evt.timings.predictedMs;
              promptMs += evt.timings.promptMs;
            }
          } else if (evt.type === "done") {
            stampStats();
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
      return assistantText;
    },
    [settings, tools, allowedServers, upsertConv, targets, targetId, reconcileTargetsFromServer],
  );

  /**
   * Name a conversation with the model that just answered it, in the background.
   *
   * Fired once, after the first exchange has finished streaming — the user's reply is already on
   * screen and nothing here delays it. THE MODEL IS THE LOADED ONE, never a choice: see lib/title
   * for why asking for a different one would ping-pong tens of gigabytes of weights to produce five
   * words. Every failure is silent, including no model at all, so the conversation simply keeps the
   * title `deriveTitle` gave it.
   */
  const nameConversation = useCallback(
    async (convId: string, prompt: string, image: string | null, reply: string) => {
      const model = status?.model;
      if (!model) return;
      const title = await requestConversationTitle({ model, prompt, reply, image });
      if (!title) return;
      // `applyModelTitle` is where the never-overwrite-a-rename rule lives, and it is checked HERE
      // rather than before the request: the user can rename the chat while this call is in flight.
      upsertConv(convId, (c) => applyModelTitle(c, title));
    },
    [status?.model, upsertConv],
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
    const existing = conversations.find((c) => c.id === convId);
    const prior = (existing?.messages ?? []).concat(userMsg);
    // A chat renamed before it was ever sent to keeps that name whatever comes back, so there is
    // nothing to ask for. `applyModelTitle` is still the authority — this only avoids spending a
    // model call on an answer that could not be used.
    const nameable = prior.length === 1 && existing?.titledBy !== "user";

    upsertConv(convId, (c) => ({
      ...c,
      // The derived title is the placeholder the model replaces once it has answered. It is skipped
      // entirely for a chat that was renamed before its first message — that name is the user's.
      title: c.messages.length === 0 && c.titledBy !== "user" ? deriveTitle(text || "Attachment") : c.title,
      updatedAt: Date.now(),
      messages: [...c.messages, userMsg, assistantMsg],
    }));
    setInput("");
    setAttachments([]);

    const reply = await runCompletion(convId, prior, assistantMsg.id);
    // Now, not before: the answer has streamed, so this costs the user nothing, and the reply is
    // the only description an image-only first message has. Only ONE image is sent, and only to a
    // model with vision — a text-only model handed a picture answers confidently about nothing.
    // Deliberately not awaited: the title lands whenever it lands.
    if (nameable) void nameConversation(convId, text, visionModel ? (images[0] ?? null) : null, reply);
  }, [
    input,
    attachments,
    isStreaming,
    ready,
    status?.model,
    visionModel,
    activeId,
    conversations,
    newConversation,
    upsertConv,
    runCompletion,
    nameConversation,
  ]);

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
  // Per-chat allow-list, not the machine-wide switch: the first toggle materialises the baseline
  // into an explicit list, so a server that appears later can't silently join this conversation.
  const toggleServer = useCallback(
    (name: string, enabled: boolean) => {
      const set = new Set(settings.enabledServers ?? baselineServers(knownServers));
      if (enabled) set.add(name);
      else set.delete(name);
      updateSettings({ ...settings, enabledServers: [...set] });
    },
    [settings, knownServers, updateSettings],
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

  // TTS voice (a global preference for the Listen button): "" = the default voice.
  const chooseVoice = useCallback((name: string) => {
    setTtsVoice(name);
    if (name) localStorage.setItem("stabbur.tts_voice", name);
    else localStorage.removeItem("stabbur.tts_voice");
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

  // Controls docked in the composer are "what am I talking to" (model + routing target).
  // Everything that shapes *how* it answers lives in the per-chat settings panel.
  // Held stable per conversation: a line that re-rolled on every render would be the most
  // distracting possible version of this. libraryLoaded gates the count so it is never a guess.
  const greeting = useMemo(
    () =>
      greetingFor(
        { models: libraryLoaded ? library.length : undefined, upstream: status ? (status.upstream ?? null) : undefined },
        activeId ?? "new",
      ),
    [libraryLoaded, library.length, status, activeId],
  );
  const disabledSet = useMemo(() => new Set(settings.disabledTools), [settings.disabledTools]);
  const composerControls = (
    <>
      <ModelSelector
        status={status}
        library={library}
        loadingName={loadingName}
        onPick={pick}
        onEject={eject}
        onShowLibrary={showLibrary}
      />
      {/* Multi-target projects only ([[assistants]]): pick which instance this turn routes to.
          Single-target/generic servers render nothing here (zero change to the existing app). */}
      {targets.length >= 2 && (
        <TargetSelector targets={targets} selectedId={targetId} onSelect={chooseTarget} />
      )}
    </>
  );

  // One definition, two surfaces: the desktop rail and the mobile drawer render the same panel,
  // and only ever one of them at a time.
  const chatSettingsEl = (
    <ChatSettingsPanel
      status={status}
      library={library}
      activeId={activeId}
      settings={settings}
      onChange={updateSettings}
      onCollapse={toggleChatSettings}
      onReloadContext={reloadWithContext}
      busy={loadingName != null}
      voices={voices}
      defaultVoice={ttsVoice}
      defaultSpeed={ttsSpeed}
      tools={tools}
      disabled={disabledSet}
      allowedServers={allowedServers}
      mcp={mcp}
      onToggleUse={setUseTools}
      onToggleTool={toggleTool}
      onToggleServer={toggleServer}
      tab={chatSettingsTab}
      onTabChange={setChatSettingsTab}
    />
  );

  // Shown above the composer when a better audio model is available for the attachment.
  const nudgeEl = audioNudge && (
    <div className="mx-auto mb-2 flex w-full max-w-4xl items-center justify-between gap-3 rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm">
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
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        status={status}
        library={library}
        conversations={conversations}
        mode={mode}
        theme={theme}
        voiceEnabled={voiceEnabled}
        hasConversation={!!activeConv && messages.length > 0}
        actions={{
          onShowChat: showChat,
          onShowLibrary: showLibrary,
          onShowVoice: showVoice,
          onOpenSettings: openSettings,
          onNewChat: startNewChat,
          onSelectConversation: selectConversation,
          onPickModel: pick,
          onToggleSidebar: toggleSidebar,
          onToggleChatSettings: toggleChatSettings,
          onToggleMode: toggleMode,
          onChooseTheme: setTheme,
          onDeleteChat: () => activeId && deleteConversation(activeId),
          onExportMarkdown: () => activeConv && exportConversationMarkdown(activeConv, status?.model ?? null),
          onExportPdf: () => {
            if (activeConv) void exportConversationPdf(activeConv, status?.model ?? null);
          },
        }}
      />
      {/* A modal over whichever surface is showing, rather than a surface of its own: it is a
          handful of thin sections, and it has no business displacing the chat. Mounted here
          (not per-surface) so the sidebar, the icon rail and ⌘K all reach the same instance. */}
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        status={status}
        library={library}
        voices={voices}
        ttsVoice={ttsVoice}
        onChooseVoice={chooseVoice}
        ttsSpeed={ttsSpeed}
        onChooseSpeed={chooseSpeed}
        mode={mode}
        onToggleMode={toggleMode}
        theme={theme}
        onChooseTheme={setTheme}
      />
      {/* Mobile: the sidebar is an overlay drawer (a resizable rail would squeeze the content).
          The persistent IconRail is the closed-state nav; tapping expand opens this. */}
      {isMobile && sidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={closeSidebar} aria-hidden />
          <div className="absolute inset-y-0 left-0 w-[82%] max-w-xs shadow-2xl">
            <Sidebar
              conversations={conversations}
              loading={historyState === "loading"}
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
              voiceEnabled={voiceEnabled}
              onRename={renameConversation}
              onDelete={deleteConversation}
              onCollapse={closeSidebar}
            />
          </div>
        </div>
      )}
      {/* Mobile: chat settings becomes an overlay too — as a rail it squeezes itself and the chat
          into nothing (61px of panel at 390px). A Sheet rather than the sidebar's hand-rolled
          drawer: Radix gives the backdrop dismiss, Escape, focus trap and scroll lock, and it is
          the same component the sibling dhis2w projects use. 92vw so the sampling controls and
          their descriptions have room at 390px, while the backdrop stays tappable. */}
      <Sheet open={chatSettingsAsSheet && chatSettingsOpen} onOpenChange={(open) => !open && closeChatSettings()}>
        <SheetContent
          side="right"
          showCloseButton={false} // the panel's own header carries one
          aria-describedby={undefined}
          className="w-[92vw] max-w-sm gap-0 p-0"
        >
          <SheetTitle className="sr-only">Chat settings</SheetTitle>
          {chatSettingsAsSheet && chatSettingsEl}
        </SheetContent>
      </Sheet>
      {/* The status bar is a sibling of the whole panel group, not of any surface inside it: it
          reports on this stabbur, which is the same fact whichever view is showing, and a strip that
          only existed on some of them would read as part of that view instead. */}
      <div className="flex h-full flex-col overflow-hidden">
      <div ref={layoutRef} onTransitionEnd={onRailTransitionEnd} className="flex min-h-0 flex-1 overflow-hidden">
        {/* When the sidebar is collapsed, a thin icon rail keeps new-chat + Models +
            Voice reachable (and usable on mobile) rather than hiding nav entirely. */}
        {!sidebarOpen && (
          <IconRail
            view={view}
            onExpand={openSidebar}
            onNew={startNewChat}
            onShowLibrary={showLibrary}
            onShowVoice={showVoice}
            voiceEnabled={voiceEnabled}
          />
        )}
        <PanelGroup
          direction="horizontal"
          autoSaveId="stabbur-layout"
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
          // A seed for the one frame before the group has been measured (and for a first run with
          // no saved layout). The width that actually holds is applied by the effect above — see
          // SIDEBAR_DEFAULT_PX for why a share of the window is the wrong unit for chrome.
          defaultSize={18}
          minSize={sidebarMin}
          maxSize={sidebarMax}
          onResize={onSidebarResize}
          onCollapse={() => setSidebarOpen(false)}
          onExpand={() => setSidebarOpen(true)}
          className={cn("min-w-0", railTransition)}
        >
          {/* The pin (see railAnim) needs an element of its own: Sidebar itself is w-full, and the
              Panel's own width is what animates. */}
          <div className="h-full" style={pinned("sidebar")}>
          <Sidebar
            conversations={conversations}
            loading={historyState === "loading"}
            activeId={activeId}
            view={view}
            onNew={startNewChat}
            onSelect={selectConversation}
            onShowChat={showChat}
            onShowLibrary={showLibrary}
            onShowVoice={showVoice}
            voiceEnabled={voiceEnabled}
            onRename={renameConversation}
            onDelete={deleteConversation}
            onCollapse={toggleSidebar}
          />
          </div>
        </Panel>

        {/* Always mounted (stable child order); hairline hidden when the left rail is collapsed so
            there's no stray divider at the edge — and on mobile, where "open" means the drawer is
            showing and the rail behind it must stay at zero. */}
        <ResizeHandle
          onDragging={onHandleDragging}
          className={cn((isMobile || !sidebarOpen) && "pointer-events-none bg-transparent")}
        />

        <Panel id="main" order={2} minSize={30} className={cn("flex min-w-0 flex-col", railTransition)}>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col" style={pinned("main")}>
          {/* THE TOP BAR, AND THERE IS ONLY ONE. It used to be this strip with an empty div on the
              left, and then a second titled band drawn *inside* Library and Voice immediately below
              it — so on a wide display the app's top edge was a blank 48px band with the real
              heading under it, and the chat had no title anywhere but the sidebar. The title now
              lives here on every surface: the conversation's own name on Chat, and whatever the data
              views publish (see lib/view-title). Same fault the status bar had, same fix.

              THE BAR LOOKS THE SAME ON EVERY SURFACE. The band it replaced was tinted, which was
              right for a strip that only two views wore — it marked them as dense data views against
              the transcript. As the app's ONE top bar it is chrome, and chrome that changes colour
              when you navigate reads as an inconsistency rather than as a distinction. So: title
              everywhere, one ground everywhere, and the hairline is what separates it from the
              content instead of a wash. */}
          <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border px-3">
            {/* Collapsed-sidebar actions (open + new chat) live in the persistent IconRail on the
                far left, so this side carries only the title. It truncates rather than wrapping or
                pushing: at 390px the controls opposite are the half a thumb can act on. */}
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
              <h1 className="min-w-0 truncate text-sm font-semibold tracking-tight">{headerTitle}</h1>
              {/* Gone below `sm`, where the title and the controls have already taken the row. */}
              {headerChip !== null && (
                <span className="hidden shrink-0 rounded-full border border-border bg-background/60 px-2 py-0.5 text-xs tabular-nums text-muted-foreground sm:inline">
                  {headerChip}
                </span>
              )}
            </div>
            {/* WHICH MODEL IS LOADED IS NOT SAID HERE. It used to be, in a pill beside these
                controls, while the composer's own picker said it again three inches below — one
                fact in two places, which is the same fault as the two stacked bars and the empty
                left half of this bar. The composer wins it outright: the model governs the NEXT
                message, so it belongs beside the box that message is typed into, the composer is
                pinned so it is on screen just as persistently, and the picker is a superset of
                what the pill offered (it also chooses a model and filters by capability). */}
            <div className="flex shrink-0 items-center gap-1.5">
              {/* Export, theme, and the rest live in the palette (⌘K) rather than as a row
                  of icons here; this button is the discoverable way in. */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setPaletteOpen(true)}
                    aria-label="Open command palette"
                    className="gap-1.5 px-2 text-muted-foreground"
                  >
                    <Search className="h-3.5 w-3.5" />
                    <kbd className="font-sans text-xs tracking-wide">⌘K</kbd>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Command palette</TooltipContent>
              </Tooltip>
              <HealthMenu health={health} />
              {/* Last in the row, because the panel's own close button sits at the far right when
                  it is open — anywhere else and the control jumps sideways as you toggle it. */}
              {view === "chat" && !chatSettingsOpen && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={toggleChatSettings}
                      aria-label="Open chat settings"
                    >
                      <PanelRight className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Chat settings</TooltipContent>
                </Tooltip>
              )}
            </div>
          </header>

          {storageWarning && (
            <div className="mx-auto mt-1 w-full max-w-4xl px-4">
              <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning-ink">
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
            <ViewTitleProvider publish={publishViewTitle}>
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
            </ViewTitleProvider>
          ) : view === "voice" ? (
            <ViewTitleProvider publish={publishViewTitle}>
              <VoiceView />
            </ViewTitleProvider>
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
                    <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs opacity-80">
                      {status.error}
                    </pre>
                  </details>
                )}
              </div>
            </div>
          )}

          {messages.length === 0 ? (
            // Empty state: badge + centered greeting + composer. The badge lives here rather
            // than in the sidebar because it is an illustration, not a glyph: it needs ~48px
            // before the pillars and ladder resolve, and the sidebar mark is 20px.
            <div className="flex flex-1 flex-col items-center justify-center px-4">
              <img src="/logo.png" alt="" width={144} height={144} className="mb-6" />
              <h1 className="mb-8 text-2xl font-semibold tracking-tight">
                {ready ? greeting : "Select a model to start"}
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
                  pdfAsImage={settings.pdfAsImage}
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
                      ttsSpeed={effectiveTtsSpeed}
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
                    canDictate={sttAvailable}
                    pdfAsImage={settings.pdfAsImage}
                    onAdd={addAttachments}
                    onRemove={removeAttachment}
                  />
                  <p className="mt-2 text-center text-sm text-muted-foreground">
                    stabbur runs your model locally. Responses may be inaccurate.
                  </p>
                </div>
              </div>
            </>
          )}
          </>
          )}
        </main>
        </Panel>

        {/* Always mounted (stable PanelGroup child order); the hairline hides while collapsed. */}
        <ResizeHandle
          onDragging={onHandleDragging}
          className={cn((chatSettingsAsSheet || !chatSettingsOpen) && "pointer-events-none bg-transparent")}
        />

        <Panel
          ref={rightPanel}
          id="chat-settings"
          order={3}
          collapsible
          collapsedSize={0}
          defaultSize={0}
          minSize={chatSettingsMin}
          maxSize={40}
          onCollapse={() => setChatSettingsOpen(false)}
          onExpand={() => setChatSettingsOpen(true)}
          className={cn("min-w-0", railTransition)}
        >
          {/* Never both: on mobile the drawer above owns it, or a second instance would run its
              own fetches and input state. Kept mounted for the length of a desktop collapse
              (onCollapse has already flipped chatSettingsOpen) so the panel slides out behind
              the clip instead of blanking. */}
          <div className="h-full" style={pinned("chat-settings")}>
            {!chatSettingsAsSheet && (chatSettingsOpen || railAnim?.rail === "chat-settings") && chatSettingsEl}
          </div>
        </Panel>
        </PanelGroup>
      </div>
      <StatusBar status={status} width={railWidth} onOpenSettings={openSettings} />
      </div>
    </TooltipProvider>
  );
}
