import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { configureApi } from "@/lib/http";
import { activeBackend, getSettings, normalizeBaseUrl } from "../../lib/settings";
import { applyAppearance } from "./appearance";
import { PanelApp } from "./PanelApp";
import "./style.css";

// Configure the shared API client from persisted settings BEFORE first render,
// so the very first status probe already targets the right origin with the token.
async function main(): Promise<void> {
  const settings = await getSettings();
  // Paint the stored theme + mode before the first render, so opening the panel never shows a
  // frame of the default palette first. PanelApp owns it from here (and installs the OS listener).
  applyAppearance(settings.theme, settings.mode);
  const b = activeBackend(settings);
  configureApi({ baseUrl: normalizeBaseUrl(b.baseUrl), token: b.token || null });

  const el = document.getElementById("root");
  if (!el) throw new Error("root element missing");
  createRoot(el).render(
    <StrictMode>
      <PanelApp initialSettings={settings} />
    </StrictMode>,
  );
}

void main();
