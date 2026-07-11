// Multi-backend switcher: the header <select> swaps which kodo the panel talks to.
// Covers (1) switching swaps the TargetBanner assistant name, (2) each backend keeps
// its own conversation transcript, and (3) a legacy flat {baseUrl, token} seed still
// works — settings are v2-only (no migrate-on-read); seedSettings translates the flat
// shape into a single v2 backend, so the panel connects and chats normally.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { KodoMock } from "../mockServer";
import type { BrowserContext } from "@playwright/test";

const mockA = new KodoMock();
const mockB = new KodoMock();

test.beforeAll(async () => {
  await mockA.start();
  await mockB.start();
});
test.afterAll(async () => {
  await mockA.stop();
  await mockB.stop();
});
test.beforeEach(() => {
  mockA.reset();
  mockA.state.phase = "ready";
  mockB.reset();
  mockB.state.phase = "ready";
});

/** Two named backends (A active) pointed at the two mocks. */
function twoBackends() {
  return {
    backends: [
      { id: "a", name: "Alpha", baseUrl: mockA.baseUrl(), token: "" },
      { id: "b", name: "Beta", baseUrl: mockB.baseUrl(), token: "" },
    ],
    activeBackendId: "a",
  };
}

async function openWithTwo(context: BrowserContext, extensionId: string) {
  await seedSettings(context, extensionId, twoBackends());
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  return panel;
}

test("switching backends swaps the TargetBanner assistant name", async ({ context, extensionId }) => {
  mockA.state.assistant = { name: "AssistantAlpha", base_url: mockA.baseUrl(), can_verify: false, verified: null };
  mockB.state.assistant = { name: "AssistantBeta", base_url: mockB.baseUrl(), can_verify: false, verified: null };

  const panel = await openWithTwo(context, extensionId);

  await expect(panel.getByText("AssistantAlpha", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText("AssistantBeta", { exact: true })).toHaveCount(0);

  await panel.getByTestId("backend-switcher").selectOption("b");

  await expect(panel.getByText("AssistantBeta", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText("AssistantAlpha", { exact: true })).toHaveCount(0);
});

test("each backend keeps its own conversation transcript", async ({ context, extensionId }) => {
  const panel = await openWithTwo(context, extensionId);

  // Send a message on backend A and let the canned stream complete (persist-on-end).
  await panel.getByPlaceholder(/Message \(Enter to send/).fill("message-on-A");
  await panel.getByRole("button", { name: "Send" }).click();
  await expect(panel.getByText("message-on-A")).toBeVisible({ timeout: 10_000 });
  await expect(panel.getByText("ok", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(panel.getByRole("button", { name: "Send" })).toBeVisible();

  // Switch to B: its transcript is empty (fresh backend, empty-state prompt shows).
  await panel.getByTestId("backend-switcher").selectOption("b");
  await expect(panel.getByText("Ask your local kodo assistant anything.")).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText("message-on-A")).toHaveCount(0);

  // Switch back to A: its message is still there.
  await panel.getByTestId("backend-switcher").selectOption("a");
  await expect(panel.getByText("message-on-A")).toBeVisible({ timeout: 15_000 });
});

test("legacy flat {baseUrl, token} install migrates and chats", async ({ context, extensionId }) => {
  // Settings are v2-only (no migrate-on-read). A flat {baseUrl, token} seed has no v2 backends, so
  // seedSettings (fixtures) translates it into a single "default" backend; the panel connects and
  // chats as before.
  await seedSettings(context, extensionId, { baseUrl: mockA.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });

  // One backend -> no switcher.
  await expect(panel.getByTestId("backend-switcher")).toHaveCount(0);

  await panel.getByPlaceholder(/Message \(Enter to send/).fill("hello");
  await panel.getByRole("button", { name: "Send" }).click();
  await expect(panel.getByText("ok", { exact: true })).toBeVisible({ timeout: 10_000 });
});
