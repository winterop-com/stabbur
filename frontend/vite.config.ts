import path from "node:path";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Built to dist/, served by `kodo serve --ui` (same-origin, so /api and /v1 are
// relative). In `vite dev`, proxy those to a kodo server — run `kodo serve
// --port 8000` alongside, or set KODO_DEV_API to its printed URL.
const target = process.env.KODO_DEV_API || "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": target,
      "/v1": target,
      "/health": target,
    },
  },
});
