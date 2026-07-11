import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { configureApi } from "@/lib/http";
import { activeBackend, getSettings, normalizeBaseUrl } from "../../lib/settings";
import { PanelApp } from "./PanelApp";
import "./style.css";

// The shared theme (frontend/src/index.css) is `.dark`-class driven, not
// prefers-color-scheme. The panel has no theme picker, so mirror the OS setting
// onto <html class="dark"> and keep it in sync as the OS theme changes.
function syncTheme(): void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = (dark: boolean): void => {
    document.documentElement.classList.toggle("dark", dark);
  };
  apply(mq.matches);
  mq.addEventListener("change", (e) => apply(e.matches));
}

// Configure the shared API client from persisted settings BEFORE first render,
// so the very first status probe already targets the right origin with the token.
async function main(): Promise<void> {
  syncTheme();
  const settings = await getSettings();
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
