// TargetBanner tab-match: a tab under a DIFFERENT origin than the assistant's
// base_url shows the mismatch banner; a tab under the SAME origin shows matched.
//
// `mock` serves the heim API and is the assistant's base_url origin. `other` is a
// second http origin (a different port) used to load a non-matching web page.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { HeimMock } from "../mockServer";

const mock = new HeimMock();
const other = new HeimMock();

test.beforeAll(async () => {
  await mock.start();
  await other.start();
});
test.afterAll(async () => {
  await mock.stop();
  await other.stop();
});
test.beforeEach(() => {
  mock.reset();
  mock.state.phase = "ready";
  mock.state.assistant = {
    name: "play42",
    base_url: mock.baseUrl(),
    auth: "basic",
    readonly: true,
    can_verify: false,
    verified: null,
  };
});

test("mismatch then matched tab drive the banner state", async ({ context, extensionId }) => {
  await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByText("play42")).toBeVisible({ timeout: 15_000 });

  // A tab under a different origin (the `other` server) -> mismatch.
  const tab = await context.newPage();
  await tab.goto(`${other.baseUrl()}/some/page`);
  await tab.bringToFront();
  await expect(panel.getByText("This tab does not match the assistant target.")).toBeVisible({
    timeout: 15_000,
  });

  // Navigate the same tab under the assistant's own origin -> matched.
  await tab.goto(`${mock.baseUrl()}/dev-2-42/dhis-web-dashboard`);
  await tab.bringToFront();
  await expect(panel.getByText("This tab matches the assistant target.")).toBeVisible({
    timeout: 15_000,
  });
});
