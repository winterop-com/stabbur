// The MCP server catalogue (what heim *could* run) plus the one path that changes it.
//
// This is not the settings panel's private state, even though the panel is the only thing that
// renders it: a chat's per-conversation allow-list falls back to a baseline derived from each
// server's `scope` (see `baselineServers`), and the send path needs that whether or not the user
// ever opened the panel. So the app owns one instance of this hook and hands it down — one fetch
// of /api/mcp/servers, one place that applies an optimistic update, no chance of the two surfaces
// disagreeing about which servers are running.

import { useCallback, useEffect, useState } from "react";

import { getMcpServers, setMcpServer, setMcpServerEnv, type McpServerInfo, type McpUpdateResult } from "@/api";

/** The last outcome of a change, per server: tone drives how the panel renders it. */
export interface McpNote {
  tone: "warn" | "error";
  text: string;
}

export interface McpServersState {
  /** The catalogue; null while the first fetch is in flight, `[]` on a backend without the route. */
  servers: McpServerInfo[] | null;
  /** The server a change is currently in flight for (its controls are disabled meanwhile). */
  pending: string | null;
  /** Per-server outcome notes, kept only for this session — the server tells us whether the
   *  change actually took effect, and that answer is the whole point of the control. */
  notes: Record<string, McpNote>;
  /** Start/stop a server machine-wide (writes the global mcp.json). */
  toggle: (name: string, on: boolean) => void;
  /** Persist declared env settings for a server (same file, same honest-outcome contract). */
  saveEnv: (name: string, env: Record<string, string>) => void;
}

/**
 * Load the catalogue once and keep it in step with the changes made through it.
 *
 * ``onToolsChanged`` re-reads /api/tools: switching a server on attaches its tools live, so the
 * caller's tool list is stale the moment a toggle applies.
 */
export function useMcpServers(onToolsChanged: () => void): McpServersState {
  const [servers, setServers] = useState<McpServerInfo[] | null>(null); // null = still loading
  const [pending, setPending] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, McpNote>>({});

  useEffect(() => {
    let cancelled = false;
    getMcpServers()
      .then((s) => !cancelled && setServers(s))
      .catch(() => !cancelled && setServers([])); // an older backend has no route; show the attached tools only
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Run one change against a server and record *what actually happened*. Shared by the on/off
   * switch and the settings fields because the honest-outcome contract is the same for both: the
   * response carries the refreshed row (an enable the project vetoes comes back still disabled) and
   * says whether the change is live, needs a restart, or failed outright.
   */
  const change = useCallback(
    async (name: string, run: () => Promise<McpUpdateResult>, retools: boolean) => {
      setPending(name);
      setNotes((n) => {
        const next = { ...n };
        delete next[name];
        return next;
      });
      try {
        const res = await run();
        setServers((list) => (list ?? []).map((s) => (s.name === res.server.name ? res.server : s)));
        if (retools && res.applied) onToolsChanged(); // its tools are attached (or gone) right now
        if (res.restart_required)
          setNotes((n) => ({
            ...n,
            [name]: { tone: "warn", text: res.detail || "restart heim serve for this to take effect" },
          }));
        else if (!res.applied)
          setNotes((n) => ({ ...n, [name]: { tone: "error", text: res.detail || "the change did not take effect" } }));
      } catch (e) {
        setNotes((n) => ({ ...n, [name]: { tone: "error", text: e instanceof Error ? e.message : String(e) } }));
      } finally {
        setPending(null);
      }
    },
    [onToolsChanged],
  );

  const toggle = useCallback(
    (name: string, on: boolean) => void change(name, () => setMcpServer(name, on), true),
    [change],
  );

  const saveEnv = useCallback(
    // Settings never change the tool list — only which files/hosts those tools reach — so no re-read.
    (name: string, env: Record<string, string>) => void change(name, () => setMcpServerEnv(name, env), false),
    [change],
  );

  return { servers, pending, notes, toggle, saveEnv };
}
