import { useMemo, useState } from "react";
import { Check, PanelLeftClose, PencilLine, Search, SquarePen, Trash2, X } from "lucide-react";

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
 * ChatGPT-style left rail: compose + collapse at top, a search field, and a
 * "Recents" conversation list (hover reveals rename/delete). The current model
 * lives in the top bar, not here.
 */
export function Sidebar({
  conversations,
  activeId,
  onNew,
  onSelect,
  onRename,
  onDelete,
  onCollapse,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
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
    <aside className="flex h-full w-[260px] shrink-0 flex-col border-r border-border bg-muted/40 text-foreground">
      <div className="flex items-center justify-between px-3 py-3">
        <span className="px-1 text-sm font-semibold tracking-tight">kodo</span>
        <div className="flex items-center gap-0.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-sm" onClick={onNew} aria-label="New chat">
                <SquarePen className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>New chat</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-sm" onClick={onCollapse} aria-label="Collapse sidebar">
                <PanelLeftClose className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Collapse sidebar</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search"
            className="h-8 border-transparent bg-black/20 pl-8 text-sm focus-visible:ring-1"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <div className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Recents
        </div>
        {filtered.length === 0 && (
          <div className="px-2 py-2 text-xs text-muted-foreground">
            {conversations.length === 0 ? "No conversations yet." : "No matches."}
          </div>
        )}
        {filtered.map((c) => {
          const active = c.id === activeId;
          const editing = c.id === editingId;
          return (
            <div
              key={c.id}
              className={cn(
                "group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm transition-colors",
                active ? "bg-white/10" : "hover:bg-white/[0.06]",
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
                    className="h-6 flex-1 border-transparent bg-black/30 px-1.5 py-0 text-sm"
                  />
                  <Button variant="ghost" size="icon-sm" onClick={commitEdit} aria-label="Save name">
                    <Check className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon-sm" onClick={() => setEditingId(null)} aria-label="Cancel">
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
                      className="text-muted-foreground"
                    >
                      <PencilLine className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => onDelete(c.id)}
                      aria-label="Delete"
                      className="text-muted-foreground hover:text-destructive"
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
