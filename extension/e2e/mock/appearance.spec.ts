// The panel's appearance settings: the named theme, the light/dark mode, and the narrow-column
// chat body size.
//
// Worth a spec because all three are things a click-through can miss. A theme is only half-applied
// unless BOTH marks land on <html> (`data-theme` for the palette, `.dark` for the mode) — the panel
// used to write only the class, which is exactly the bug that reads as "the picker does nothing" —
// and the choice has to survive a panel *close*, which is the state chrome.storage exists for.
// The chat body size lives in a CSS override of a shared component's own class, so only a computed
// style can prove it wins.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { StabburMock } from "../mockServer";
import type { Page } from "@playwright/test";

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

function backend() {
  return {
    backends: [{ id: "default", name: "Default", baseUrl: mock.baseUrl(), token: "" }],
    activeBackendId: "default",
  };
}

/** What <html> currently says about appearance, plus the palette's own ground token. */
async function appearance(panel: Page): Promise<{ theme: string | null; dark: boolean; background: string }> {
  return panel.evaluate(() => ({
    theme: document.documentElement.getAttribute("data-theme"),
    dark: document.documentElement.classList.contains("dark"),
    background: getComputedStyle(document.documentElement).getPropertyValue("--background").trim(),
  }));
}

async function openSettings(panel: Page): Promise<void> {
  await panel.getByLabel("Settings").click();
  await expect(panel.getByRole("heading", { name: "Settings" })).toBeVisible({ timeout: 10_000 });
}

test("a fresh install is the default palette following the OS", async ({ context, extensionId }) => {
  await seedSettings(context, extensionId, backend());
  const panel = await openPanel(context, extensionId);
  await panel.emulateMedia({ colorScheme: "dark" });
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });

  // No stored choice -> no data-theme at all (the base :root block IS the default palette; an
  // attribute reading "default" would match no rule in index.css).
  const os = await appearance(panel);
  expect(os.theme).toBeNull();
  expect(os.dark).toBe(true);

  await panel.emulateMedia({ colorScheme: "light" });
  await expect.poll(async () => (await appearance(panel)).dark).toBe(false);
});

test("picking a theme repaints the panel and survives a reopen", async ({ context, extensionId }) => {
  await seedSettings(context, extensionId, backend());
  const panel = await openPanel(context, extensionId);
  await panel.emulateMedia({ colorScheme: "light" });
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  const before = await appearance(panel);

  await openSettings(panel);
  await panel.getByTestId("theme-picker").selectOption("paper");

  // Applied on pick, without leaving the settings view: the ground token actually moves.
  await expect.poll(async () => (await appearance(panel)).theme).toBe("paper");
  expect((await appearance(panel)).background).not.toBe(before.background);
  // Still in settings -- the appearance selects do not commit-and-close like Save does.
  await expect(panel.getByRole("heading", { name: "Settings" })).toBeVisible();
  await panel.close();

  // A second panel opens already painted (main.tsx stamps <html> before the first render).
  const reopened = await openPanel(context, extensionId);
  await reopened.emulateMedia({ colorScheme: "light" });
  await expect(reopened.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });
  const after = await appearance(reopened);
  expect(after.theme).toBe("paper");
  expect(after.background).not.toBe(before.background);
});

test("an explicit light/dark mode overrides the OS setting", async ({ context, extensionId }) => {
  await seedSettings(context, extensionId, backend());
  const panel = await openPanel(context, extensionId);
  await panel.emulateMedia({ colorScheme: "light" });
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });

  await openSettings(panel);
  await panel.getByTestId("mode-picker").selectOption("dark");
  await expect.poll(async () => (await appearance(panel)).dark).toBe(true);

  // The OS flipping the other way must NOT win back: the mode is no longer "system".
  await panel.emulateMedia({ colorScheme: "light" });
  await panel.waitForTimeout(200);
  expect((await appearance(panel)).dark).toBe(true);

  // Back to system and the OS is authoritative again.
  await panel.getByTestId("mode-picker").selectOption("system");
  await expect.poll(async () => (await appearance(panel)).dark).toBe(false);
});

test("chat prose renders at the panel's narrow-column body size", async ({ context, extensionId }) => {
  await seedSettings(context, extensionId, backend());
  const panel = await openPanel(context, extensionId);
  await expect(panel.getByPlaceholder(/Message \(Enter to send/)).toBeVisible({ timeout: 15_000 });

  await panel.getByPlaceholder(/Message \(Enter to send/).fill("hello");
  await panel.getByRole("button", { name: "Send" }).click();
  await expect(panel.getByText("ok", { exact: true })).toBeVisible({ timeout: 10_000 });

  // 14px, not the shared component's own 16px: the panel's stylesheet steps the chat body down
  // one place on the scale for a ~400px column (docs/ui-conventions.md, "The narrow-surface step").
  const size = await panel.evaluate(() => {
    const el = document.querySelector(".prose-chat");
    return el ? getComputedStyle(el).fontSize : null;
  });
  expect(size).toBe("14px");
});
