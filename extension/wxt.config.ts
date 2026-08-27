import path from "node:path";
import { defineConfig } from "wxt";
import tailwindcss from "@tailwindcss/vite";

const FRONTEND_SRC = path.resolve(__dirname, "../frontend/src");

// Build flavor, selected by the STABBUR_FLAVOR env var (see lib/flavor.ts). "dhis2" ships the
// "stabbur for DHIS2" build (branded name/description + DHIS2 copy) into its own output dir so
// both flavors can be built side by side; anything else is the neutral generic build. Only
// name/description/title and a few words of in-app copy differ — every feature is in both.
const FLAVOR = process.env.STABBUR_FLAVOR === "dhis2" ? "dhis2" : "generic";
const IS_DHIS2 = FLAVOR === "dhis2";

// TEST-ONLY build variant (STABBUR_E2E=1). It lands in its own output dir (`.output/chrome-mv3-e2e`)
// and adds the live-tier target origins to the STATIC host_permissions, so the E2E harness's
// `grantHostPermission` short-circuits (chrome.permissions.contains already true) instead of
// calling chrome.permissions.request, whose prompt wedges the headless renderer. These origins are
// NEVER added to the shipped generic/dhis2 builds. Only ever built via `bun run build:e2e`.
const IS_E2E = process.env.STABBUR_E2E === "1";
const E2E_HOST_PERMISSIONS = ["https://play.im.dhis2.org/*", "http://localhost:8080/*"];
// Output-dir suffix: e2e takes precedence over dhis2 so the three builds never collide.
const OUT_SUFFIX = IS_E2E ? "-e2e" : IS_DHIS2 ? "-dhis2" : "";

const MANIFEST_NAME = IS_DHIS2 ? "stabbur for DHIS2" : "stabbur";
const MANIFEST_DESCRIPTION = IS_DHIS2
  ? "Your local AI assistant for DHIS2 - chat, verify, and act on your instance with your own model"
  : "Side panel for your local stabbur assistant";

// Per-flavor toolbar/store icons: the stabbur emblem (docs/assets/logo.png), resized into
// public/icon/ by scripts/gen_extension_icons.py and copied verbatim by WXT. The dhis2 set is the
// same emblem ringed in DHIS2 blue - a rim rather than the old corner accent, because at 16px the
// rim colour is the only thing that still distinguishes the two flavors.
const ICON_PREFIX = IS_DHIS2 ? "dhis2" : "generic";
const MANIFEST_ICONS = {
  "16": `icon/${ICON_PREFIX}-16.png`,
  "32": `icon/${ICON_PREFIX}-32.png`,
  "48": `icon/${ICON_PREFIX}-48.png`,
  "128": `icon/${ICON_PREFIX}-128.png`,
};

// The extension reuses the shared frontend source (frontend/src/{api,lib,components}),
// where "@/..." resolves to frontend/src — both for the modules the panel imports and
// for the imports those modules make of each other. WXT forces "@" -> its own srcDir
// (the extension root) via a Vite plugin; the redirect below reclaims "@" for the
// shared source (see the hooks block). No extension-local code uses "@" to mean the
// extension root, so this is safe.
export default defineConfig({
  modules: ["@wxt-dev/module-react"],
  // The generic build keeps the default `.output/chrome-mv3`; the dhis2 build lands in
  // `.output/chrome-mv3-dhis2` and the test-only e2e build in `.output/chrome-mv3-e2e`, so all
  // three coexist and the E2E/screenshots tooling can pick one. The `{{modeSuffix}}` keeps `-dev`
  // on the WXT dev server output as usual.
  ...(OUT_SUFFIX ? { outDirTemplate: `{{browser}}-mv{{manifestVersion}}${OUT_SUFFIX}{{modeSuffix}}` } : {}),
  vite: () => ({
    plugins: [tailwindcss()],
    // Bake the flavor into the bundle so app code (lib/flavor.ts) can branch copy on it.
    define: { __STABBUR_FLAVOR__: JSON.stringify(FLAVOR) },
    resolve: {
      dedupe: ["react", "react-dom"],
    },
  }),
  // Redirect the "@" alias to the shared frontend source. WXT forces
  // "@" -> srcDir via its own Vite plugin (wxt:aliases), and Vite applies plugin
  // config() results as overrides in plugin order, so a plain user alias always
  // loses. We append a plugin to the final per-entrypoint config (in the
  // vite:build:extendConfig hook) so its config() runs LAST and wins. No
  // extension-local code uses "@" to mean the extension root, so this is safe.
  hooks: {
    "vite:build:extendConfig": (_entrypoints, config) => {
      config.plugins ??= [];
      config.plugins.push({
        name: "stabbur:at-alias-to-frontend",
        config: () => ({ resolve: { alias: { "@": FRONTEND_SRC } } }),
      });
    },
  },
  manifest: {
    name: MANIFEST_NAME,
    description: MANIFEST_DESCRIPTION,
    icons: MANIFEST_ICONS,
    permissions: ["sidePanel", "storage", "tabs", "activeTab", "scripting"],
    // Requested on demand during the session-fallback bind consent (user gesture), so stabbur can read
    // and re-sync the target site's session cookie. Not in the base grant — most users only ever mint
    // a scoped PAT, which needs no cookie access.
    optional_permissions: ["cookies"],
    optional_host_permissions: ["http://*/*", "https://*/*"],
    // The e2e variant statically pre-grants the live-tier target origins (see IS_E2E above); the
    // shipped generic/dhis2 builds keep only the loopback origins.
    host_permissions: ["http://127.0.0.1/*", "http://localhost/*", ...(IS_E2E ? E2E_HOST_PERMISSIONS : [])],
    side_panel: { default_path: "sidepanel.html" },
    action: { default_title: MANIFEST_NAME },
  },
});
