// stabbur's cross-site guard: a mutating POST (loading the model) is 403-blocked
// while GETs pass, and the panel surfaces the cors_origins hint with this
// extension's id.

import { test, expect, openPanel, seedSettings } from "../fixtures";
import { HeimMock, MOCK_MODEL } from "../mockServer";

test("403 on the model-load POST renders the cors_origins hint", async ({ context, extensionId }) => {
  const mock = new HeimMock();
  await mock.start();
  try {
    mock.state.phase = "stopped"; // needs-model, so the panel offers a Load button
    mock.state.projectModel = MOCK_MODEL;
    mock.state.block403Post = true; // every mutating POST is cross-site blocked
    await seedSettings(context, extensionId, { baseUrl: mock.baseUrl(), token: "" });
    const panel = await openPanel(context, extensionId);

    const loadBtn = panel.getByRole("button", { name: new RegExp(`^Load ${MOCK_MODEL}`) });
    await expect(loadBtn).toBeVisible({ timeout: 15_000 });
    await loadBtn.click();

    await expect(panel.getByText(/cross-site guard/)).toBeVisible({ timeout: 10_000 });
    await expect(
      panel.getByText(`cors_origins = ["chrome-extension://${extensionId}"]`),
    ).toBeVisible();
  } finally {
    await mock.stop();
  }
});
