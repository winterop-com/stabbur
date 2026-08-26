// TargetBanner tab-match: a tab under a DIFFERENT origin than the assistant's
// base_url shows the mismatch banner; a tab under the SAME origin shows matched.
//
// `mock` serves the stabbur API and is the assistant's base_url origin. `other` is a
// second http origin (a different port) used to load a non-matching web page.

import { test, expect, openPanel, seedSettings, expandTarget } from "../fixtures";
import { TAB_MATCHED } from "../../lib/bannerText";
import { StabburMock } from "../mockServer";

const mock = new StabburMock();
const other = new StabburMock();

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

  // A tab under a different origin (the `other` server) -> mismatch. On an unrelated page the
  // target block collapses to the one-line notice: no metadata rows until Details is expanded.
  const tab = await context.newPage();
  await tab.goto(`${other.baseUrl()}/some/page`);
  await tab.bringToFront();
  await expect(panel.getByText(/Not a play42 page\./)).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText("auth: basic")).toHaveCount(0);
  await expect(panel.getByText(/^source: /)).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Verify" })).toHaveCount(0);
  await expect(panel.getByText("This tab does not match the assistant target.")).toHaveCount(0);
  await expandTarget(panel);
  await expect(panel.getByText("This tab does not match the assistant target.")).toBeVisible();
  await expect(panel.getByText("auth: basic")).toBeVisible();

  // Navigate the same tab under the assistant's own origin -> matched. The header chip flips green;
  // it asserts the match whether or not the block is expanded.
  await tab.goto(`${mock.baseUrl()}/dev-2-42/dhis-web-dashboard`);
  await tab.bringToFront();
  await expect(panel.getByText(TAB_MATCHED)).toBeVisible({ timeout: 15_000 });
});
