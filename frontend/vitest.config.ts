import path from "node:path";

import { defineConfig } from "vitest/config";

// Deliberately NOT vite.config.ts: the app's config carries the React and Tailwind plugins, which
// a node-environment unit test has no use for. The one thing the tests need from vite is the `@/`
// alias, so that is the whole of it.
//
// `environment: "node"` because the only thing under test is lib/history, which talks to
// IndexedDB (supplied by fake-indexeddb) and localStorage (stubbed per test). Nothing renders,
// so a DOM implementation would be a dependency bought for nothing.
export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
