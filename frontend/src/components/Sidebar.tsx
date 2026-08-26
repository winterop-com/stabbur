import { useMemo, useState } from "react";
import {
  AudioLines,
  Boxes,
  Check,
  MessagesSquare,
  PanelLeftClose,
  PencilLine,
  Search,
  SquarePen,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { Conversation } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The shape every row in the rail wears, active or not — a nav destination and a conversation
 * row are the same object at two sizes, so they must not be highlighted two different ways.
 *
 * THE LEFT BORDER IS ALWAYS THERE, transparent when idle. That is what makes the column of rows
 * line up: an active marker that only exists while active (an absolutely-positioned bar, or a
 * border that appears) either sits outside the box model or shoves the row 3px sideways the
 * moment you land on it. Reserving the space costs nothing and the text never moves.
 *
 * THE ASYMMETRIC RADIUS pairs with it: the right side rounds like any card, while the left edge
 * stays nearly square so the accent border reads as an edge of the row rather than a floating pill.
 *
 * AND THE FILLS ARE SOLID `--sidebar-*` TOKENS, never an alpha of the page's accent. The rail has a
 * ground of its own; a wash over a wash is what made the old highlight invisible on some themes.
 */
const ROW = "flex w-full rounded-l-[4px] rounded-r-lg border-l-[3px] text-left transition-colors";
const ROW_ACTIVE = "border-sidebar-primary bg-sidebar-accent text-sidebar-accent-foreground";
const ROW_IDLE = "border-transparent text-sidebar-muted-foreground hover:bg-sidebar-wash hover:text-sidebar-foreground";
/** Ghost buttons inside the rail. The shared variant hovers to the PAGE's `--accent`, which over
 *  the rail's own ground is a patch of a different room; the wash is the rail's own hover. */
const RAIL_GHOST = "text-sidebar-muted-foreground hover:bg-sidebar-wash hover:text-sidebar-foreground";

/** A primary nav row: icon + title + one-line subtitle, with an accent border when active. */
function NavItem({
  icon,
  title,
  subtitle,
  active = false,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  /** Omitted for rows that are actions rather than destinations (Settings opens a dialog). */
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(ROW, "items-start gap-3 px-3 py-2", active ? `${ROW_ACTIVE} font-medium` : ROW_IDLE)}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span className="min-w-0">
        <span className="block text-sm leading-tight">{title}</span>
        {/* The subtitle takes its own colour from nothing: it inherits the row's, at 75%, so it
            stays a step quieter than the title in the idle, hover and active states alike rather
            than being pinned to one ink that only reads against one of them. */}
        {subtitle && <span className="mt-0.5 block truncate text-xs leading-tight opacity-75">{subtitle}</span>}
      </span>
    </button>
  );
}

/**
 * The left rail: brand + compose at top, primary destinations as subtitled nav
 * rows (Chat, Library, Voice), and the "Recents" conversation list (hover reveals
 * rename/delete). The current model lives in the top bar, not here.
 *
 * Settings is NOT here: it sits in the status bar's left segment, which lines up with this
 * column. It used to be a row pinned at the foot of the rail, which put two stacked strips
 * across the bottom of the window once the status bar existed — one strip, one gear.
 */
export function Sidebar({
  conversations,
  loading = false,
  activeId,
  view,
  onNew,
  onSelect,
  onShowChat,
  onShowLibrary,
  onShowVoice,
  voiceEnabled = true,
  onRename,
  onDelete,
  onCollapse,
}: {
  conversations: Conversation[];
  /** The history is still being read (IndexedDB). An empty list means "not yet", not "none" — and
   *  the difference matters here, because this column is where a reader looks to find out. */
  loading?: boolean;
  activeId: string | null;
  view: "chat" | "library" | "voice";
  onNew: () => void;
  onSelect: (id: string) => void;
  onShowChat: () => void;
  onShowLibrary: () => void;
  onShowVoice: () => void;
  voiceEnabled?: boolean;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onCollapse: () => void;
}) {
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
    if (!q) return sorted;
    return sorted.filter((c) => c.title.toLowerCase().includes(q));
  }, [conversations, query]);

  const startEdit = (c: Conversation) => {
    setEditingId(c.id);
    setDraft(c.title);
  };
  const commitEdit = () => {
    if (editingId) {
      const t = draft.trim();
      if (t) onRename(editingId, t);
    }
    setEditingId(null);
  };

  return (
    // `bg-sidebar` rather than a tint of the page: the row fills below are solid colours mixed
    // against exactly this ground, so the rail has to actually be it for them to land as designed.
    <aside className="flex h-full w-full min-w-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center justify-between px-3 py-3">
        <span className="px-1 text-sm font-semibold tracking-tight">
          <span className="md:hidden">Stabbur</span>
          <span className="hidden md:inline">Stabbur Studio</span>
        </span>
        <div className="flex items-center gap-0.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-sm" onClick={onNew} aria-label="New chat" className={RAIL_GHOST}>
                <SquarePen className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>New chat</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-sm" onClick={onCollapse} aria-label="Collapse sidebar" className={RAIL_GHOST}>
                <PanelLeftClose className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Collapse sidebar</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-sidebar-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search"
            className="h-8 border-transparent bg-sidebar-wash pl-8 text-sm focus-visible:ring-1"
          />
        </div>
      </div>

      <div className="flex flex-col gap-0.5 px-2 pb-1">
        <NavItem
          icon={<MessagesSquare className="h-4 w-4" />}
          title="Chat"
          subtitle="Talk to your models"
          active={view === "chat"}
          onClick={onShowChat}
        />
        <NavItem
          icon={<Boxes className="h-4 w-4" />}
          title="Library"
          subtitle="Browse and load models"
          active={view === "library"}
          onClick={onShowLibrary}
        />
        {voiceEnabled && (
          <NavItem
            icon={<AudioLines className="h-4 w-4" />}
            title="Voice"
            subtitle="Speak, transcribe, clone"
            active={view === "voice"}
            onClick={onShowVoice}
          />
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <div className="px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-sidebar-muted-foreground">
          Recents
        </div>
        {filtered.length === 0 && (
          <div className="px-2 py-2 text-xs text-sidebar-muted-foreground">
            {loading ? "Loading history…" : conversations.length === 0 ? "No conversations yet." : "No matches."}
          </div>
        )}
        {filtered.map((c) => {
          const active = view === "chat" && c.id === activeId;
          const editing = c.id === editingId;
          return (
            // Same treatment as the nav rows above, at conversation-row size — a chat you are in
            // and a surface you are on are the same kind of "here", and the eye should not have to
            // learn two markers for it in one column.
            <div
              key={c.id}
              className={cn(
                ROW,
                "group items-center gap-1 py-1.5 pl-2 pr-2 text-sm",
                active ? ROW_ACTIVE : ROW_IDLE,
              )}
            >
              {editing ? (
                <>
                  <Input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitEdit();
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    className="h-6 flex-1 border-transparent bg-background px-1.5 py-0 text-sm"
                  />
                  <Button variant="ghost" size="icon-sm" onClick={commitEdit} aria-label="Save name" className={RAIL_GHOST}>
                    <Check className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => setEditingId(null)} aria-label="Cancel" className={RAIL_GHOST}>
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => onSelect(c.id)}
                    className="flex-1 truncate text-left"
                    title={c.title}
                  >
                    {c.title}
                  </button>
                  <div className="flex items-center opacity-0 transition-opacity group-hover:opacity-100">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => startEdit(c)}
                      aria-label="Rename"
                      className={RAIL_GHOST}
                    >
                      <PencilLine className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => onDelete(c.id)}
                      aria-label="Delete"
                      className={cn(RAIL_GHOST, "hover:text-destructive")}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
