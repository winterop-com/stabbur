import { useMemo } from "react";
import { ChevronDown, Wrench } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import type { ToolInfo } from "@/api";
import { cn } from "@/lib/utils";

/**
 * Composer-docked tools control (LM Studio / ChatGPT style): a pill showing how
 * many MCP tools are active, opening a menu with a master on/off switch and one
 * **fly-out sub-menu per server**. Cascading sub-menus keep the top level compact
 * whether a server exposes 3 tools (datetime) or 300 (dhis2); each sub-menu has a
 * toggle-all plus the individual tools. Tools are opt-out (a denylist), so a
 * newly-attached tool defaults on.
 *
 * Rows are plain elements (not menu items) so toggling a switch doesn't close the
 * menu — you can flip several tools in one open.
 */
export function ToolsControl({
  tools,
  useTools,
  disabled,
  onToggleUse,
  onToggleTool,
  onToggleServer,
}: {
  tools: ToolInfo[];
  useTools: boolean;
  disabled: Set<string>;
  onToggleUse: (on: boolean) => void;
  onToggleTool: (name: string, enabled: boolean) => void;
  onToggleServer: (names: string[], enabled: boolean) => void;
}) {
  const grouped = useMemo(() => {
    const by: Record<string, ToolInfo[]> = {};
    for (const t of tools) (by[t.server] ??= []).push(t);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [tools]);

  const enabledCount = useTools ? tools.filter((t) => !disabled.has(t.name)).length : 0;
  const none = tools.length === 0;
  const label = none ? "No tools" : useTools ? `Tools ${enabledCount}/${tools.length}` : "Tools off";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        disabled={none}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
          useTools && enabledCount > 0 ? "text-foreground" : "text-muted-foreground",
        )}
        title={none ? "No MCP tools configured" : "MCP tools"}
      >
        <Wrench className="h-3.5 w-3.5" />
        <span className="truncate">{label}</span>
        {!none && <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />}
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="w-72 p-0">
        {/* Master switch */}
        <div className="flex items-center justify-between gap-3 px-3 py-2.5">
          <div className="min-w-0">
            <div className="text-sm font-medium">Enable tools</div>
            <p className="text-[11px] text-muted-foreground">Let the model call MCP tools.</p>
          </div>
          <Switch checked={useTools} onCheckedChange={onToggleUse} aria-label="Enable tools" />
        </div>

        <div className="border-t border-border p-1">
          {grouped.map(([server, list]) => {
            const onCount = list.filter((t) => !disabled.has(t.name)).length;
            const allOn = onCount === list.length;
            return (
              <DropdownMenuSub key={server}>
                <DropdownMenuSubTrigger className={cn(!useTools && "opacity-50")}>
                  <span className="flex-1 truncate font-medium">{server}</span>
                  <span className="ml-2 text-[11px] text-muted-foreground">
                    {useTools ? `${onCount}/${list.length}` : list.length}
                  </span>
                </DropdownMenuSubTrigger>

                <DropdownMenuSubContent className="max-h-80 w-72 overflow-y-auto p-0">
                  {/* Toggle-all for the whole server. */}
                  <div className="flex items-center justify-between gap-3 px-3 py-2">
                    <span className="text-sm font-medium">All {server} tools</span>
                    <Switch
                      checked={useTools && allOn}
                      disabled={!useTools}
                      onCheckedChange={(v) => onToggleServer(list.map((t) => t.name), v)}
                      aria-label={`Toggle all ${server} tools`}
                    />
                  </div>
                  <DropdownMenuSeparator />
                  {list.map((t) => {
                    const on = useTools && !disabled.has(t.name);
                    return (
                      <div
                        key={t.name}
                        className="flex items-start justify-between gap-3 rounded-sm px-3 py-2 hover:bg-accent/50"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-sm" title={t.name}>
                            {t.tool}
                          </div>
                          {t.description && (
                            <p className="line-clamp-2 text-[11px] text-muted-foreground" title={t.description}>
                              {t.description}
                            </p>
                          )}
                        </div>
                        <Switch
                          checked={on}
                          disabled={!useTools}
                          onCheckedChange={(v) => onToggleTool(t.name, v)}
                          aria-label={`Toggle ${t.name}`}
                        />
                      </div>
                    );
                  })}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            );
          })}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
