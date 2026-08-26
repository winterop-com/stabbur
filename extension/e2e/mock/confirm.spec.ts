// Per-action write confirmation: a `confirm` SSE frame renders an inline Approve/Deny card;
// approving POSTs {approve:true} and the tool result streams in; denying POSTs {approve:false}
// and the declined result streams in; a `confirm_resolved` clears the card. A timed-out
// confirmation is shown as auto-denied. Drives the panel against a StabburMock that pauses the
// stream on the confirm frame until the client resolves it.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { StabburMock, type ChatFrame } from "../mockServer";

const mock = new StabburMock();

test.beforeAll(async () => {
  await mock.start();
});
test.afterAll(async () => {
  await mock.stop();
});
test.beforeEach(() => {
  mock.reset();
  mock.state.phase = "ready";
});

async function openReady(context: import("@playwright/test").BrowserContext, extensionId: string) {
  await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  return panel;
}

test("approving a confirmation posts approve:true and streams the tool result", async ({ context, extensionId }) => {
  const confirm: ChatFrame = { type: "confirm", id: "c-approve", tool: "dhis2__data_aggregate_set", args: { value: 42 } };
  mock.state.chatFrames = [{ type: "token", text: "Preparing write " }, confirm];
  mock.state.confirmApprovedTail = [
    { type: "tool", kind: "result", detail: "data value stored" },
    { type: "token", text: "Saved." },
    { type: "done" },
  ];
  mock.state.chatGapMs = 20;

  const panel = await openReady(context, extensionId);
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("set the value");
  await panel.getByRole("button", { name: "Send" }).click();

  // The pending confirmation card appears with the tool name; no resolve POST yet.
  const card = panel.getByTestId("chat-confirm");
  await expect(card).toBeVisible({ timeout: 10_000 });
  await expect(card).toContainText("dhis2__data_aggregate_set");
  expect(mock.state.confirmCalls.length).toBe(0);

  await panel.getByTestId("chat-confirm-approve").click();

  // The approve reached the server, the tool result streamed in, and the card cleared.
  await expect.poll(() => mock.state.confirmCalls).toContainEqual({ id: "c-approve", approve: true });
  await expect(panel.getByTestId("tool-chip-result")).toContainText("data value stored", { timeout: 10_000 });
  await expect(panel.getByText("Saved.")).toBeVisible();
  await expect(panel.getByTestId("chat-confirm")).toHaveCount(0);
});

test("denying a confirmation posts approve:false and streams the declined result", async ({ context, extensionId }) => {
  const confirm: ChatFrame = { type: "confirm", id: "c-deny", tool: "dhis2__metadata_data_element_delete", args: { id: "abc" } };
  mock.state.chatFrames = [confirm];
  mock.state.confirmDeniedTail = [
    { type: "tool", kind: "result", detail: "action declined by user" },
    { type: "done" },
  ];
  mock.state.chatGapMs = 20;

  const panel = await openReady(context, extensionId);
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("delete it");
  await panel.getByRole("button", { name: "Send" }).click();

  await expect(panel.getByTestId("chat-confirm")).toBeVisible({ timeout: 10_000 });
  await panel.getByTestId("chat-confirm-deny").click();

  await expect.poll(() => mock.state.confirmCalls).toContainEqual({ id: "c-deny", approve: false });
  await expect(panel.getByTestId("tool-chip-result")).toContainText("action declined by user", { timeout: 10_000 });
  await expect(panel.getByTestId("chat-confirm")).toHaveCount(0);
});

test("a timed-out confirmation is shown as auto-denied", async ({ context, extensionId }) => {
  const confirm: ChatFrame = { type: "confirm", id: "c-timeout", tool: "dhis2__data_aggregate_push", args: {} };
  mock.state.chatFrames = [confirm];
  mock.state.confirmWaitMs = 400; // resolve as a timeout without any client action
  mock.state.confirmDeniedTail = [{ type: "done" }];
  mock.state.chatGapMs = 20;

  const panel = await openReady(context, extensionId);
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("push data");
  await panel.getByRole("button", { name: "Send" }).click();

  // No click: the server auto-denies on timeout, and the card reflects it.
  await expect(panel.getByTestId("chat-confirm-outcome")).toContainText("Auto-denied (timed out)", { timeout: 10_000 });
  expect(mock.state.confirmCalls.length).toBe(0);
});
