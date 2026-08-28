// What is pinned here is `mcpRowControls`, and specifically the gate on the on/off switch.
//
// `GET /api/mcp/servers` lists third-party servers from mcp.json alongside the ones stabbur ships.
// `POST /api/mcp/servers/{name}` does not accept them — it is an allow-list over the shipped set
// and answers 404 for everything else. The two halves therefore disagree about what a row can do,
// and the disagreement is invisible in the browser until someone presses the switch: a third-party
// row has `installed: true` (that is the field's default), so the old `!!server && server.installed`
// gate drew a control that always fails.
//
// That is exactly the failure a click-through does not find, because it only shows up on a machine
// whose mcp.json configures a server stabbur does not ship. Hence a test on the decision rather
// than on the markup. Nothing renders; the suite runs in vitest's node environment like the rest.

import { describe, expect, it } from "vitest";

import type { McpServerInfo } from "@/api";
import { mcpRowControls } from "@/components/ChatSettingsPanel";

function row(over: Partial<McpServerInfo> = {}): McpServerInfo {
  return {
    name: "datetime",
    command: "stabbur-mcp-datetime",
    description: "",
    enabled: true,
    scope: "global",
    installed: true,
    setup: "",
    env: {},
    settings: [],
    bundled: true,
    live: null,
    tools: null,
    ...over,
  };
}

describe("mcpRowControls — the switch", () => {
  it("offers one for a bundled server that is installed", () => {
    expect(mcpRowControls(row(), 0).canToggle).toBe(true);
  });

  it("withholds it from a third-party server, however installed it looks", () => {
    // The regression: `installed` defaults to true on a row stabbur does not ship, so it is not
    // the gate. Pressing the switch here would POST a name the route answers 404 for.
    expect(mcpRowControls(row({ bundled: false, installed: true, live: true }), 3).canToggle).toBe(false);
  });

  it("withholds it from a bundled server whose optional extra is missing", () => {
    expect(mcpRowControls(row({ installed: false }), 0).canToggle).toBe(false);
  });

  it("withholds it from a server attached without being in the catalogue at all", () => {
    expect(mcpRowControls(null, 2).canToggle).toBe(false);
  });
});

describe("mcpRowControls — the tool count", () => {
  it("prefers what this chat can call", () => {
    expect(mcpRowControls(row({ tools: 9 }), 4).toolCount).toBe(4);
  });

  it("falls back to the row's own count when this chat can call none", () => {
    // A server attached in the serving process but outside this conversation's allow-list. On a
    // read-only row there is no switch and no note, so the count is the only sign it is running.
    expect(mcpRowControls(row({ bundled: false, tools: 7 }), 0).toolCount).toBe(7);
  });

  it("is zero, not NaN, when neither side knows — `tools` is null without a bridge to ask", () => {
    expect(mcpRowControls(row({ tools: null }), 0).toolCount).toBe(0);
    expect(mcpRowControls(null, 0).toolCount).toBe(0);
  });
});
