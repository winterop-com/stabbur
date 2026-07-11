// Connection lifecycle: disconnected -> auto-connect, needs-token, and
// needs-model -> Load -> loading -> ready.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { KodoMock, MOCK_MODEL, reservePort } from "../mockServer";

test.describe("connection lifecycle", () => {
  test("disconnected, then auto-connects once the server comes up", async ({ context, extensionId }) => {
    // Reserve a port the server is NOT yet listening on, so the first probe fails.
    const port = await reservePort();
    const baseUrl = `http://127.0.0.1:${port}`;
    await seedSettings(context, extensionId, { baseUrl, token: "" });
    const panel = await openPanel(context, extensionId);

    await expect(panel.getByText(/kodo is not reachable/)).toBeVisible({ timeout: 15_000 });
    await expect(panel.getByText(/Retrying automatically every 3s/)).toBeVisible();

    // Bring the server up on that exact port; the 3s auto-retry should connect
    // within a couple of windows.
    const mock = new KodoMock();
    mock.state.phase = "ready";
    await mock.start(port);
    try {
      await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
    } finally {
      await mock.stop();
    }
  });

  test("needs-token, then connects after the token is entered", async ({ context, extensionId }) => {
    const mock = new KodoMock();
    await mock.start();
    try {
      mock.state.token = "s3cret";
      mock.state.phase = "ready";
      await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
      const panel = await openPanel(context, extensionId);

      await expect(panel.getByText("This kodo server requires an access token.")).toBeVisible({
        timeout: 15_000,
      });
      await panel.getByPlaceholder("Bearer token").fill("s3cret");
      await panel.getByRole("button", { name: "Save token and connect" }).click();

      await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
    } finally {
      await mock.stop();
    }
  });

  test("needs-model -> Load -> loading -> ready", async ({ context, extensionId }) => {
    const mock = new KodoMock();
    await mock.start();
    try {
      mock.state.phase = "stopped";
      mock.state.projectModel = MOCK_MODEL;
      mock.state.locked = false;
      mock.state.loadReadyAfterMs = 300; // flips to ready shortly after Load
      await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
      const panel = await openPanel(context, extensionId);

      const loadBtn = panel.getByRole("button", { name: new RegExp(`^Load ${MOCK_MODEL}`) });
      await expect(loadBtn).toBeVisible({ timeout: 15_000 });
      await loadBtn.click();

      await expect(panel.getByText("Loading model...")).toBeVisible({ timeout: 10_000 });
      // The connection polls status every ~2s; ready arrives on the next poll.
      await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 20_000 });
    } finally {
      await mock.stop();
    }
  });
});
