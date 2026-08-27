import { describe, expect, it } from "vitest";

import { greetingFor } from "@/lib/greeting";

describe("greetingFor", () => {
  it("holds still for a given conversation", () => {
    const facts = { models: 27, upstream: null };
    const first = greetingFor(facts, "conv-1");
    for (let i = 0; i < 20; i++) expect(greetingFor(facts, "conv-1")).toBe(first);
  });

  it("varies across conversations", () => {
    const facts = { models: 27, upstream: null };
    const seen = new Set(Array.from({ length: 60 }, (_, i) => greetingFor(facts, `conv-${i}`)));
    expect(seen.size).toBeGreaterThan(1);
  });

  it("never states a fact it was not given", () => {
    // A fresh install knows neither the library size nor the backend: nothing may be invented.
    const seen = new Set(Array.from({ length: 200 }, (_, i) => greetingFor({}, `c${i}`)));
    for (const line of seen) {
      expect(line).not.toMatch(/shelf/);
      expect(line).not.toMatch(/Running on/);
    }
  });

  it("never says '1 models'", () => {
    const seen = new Set(Array.from({ length: 200 }, (_, i) => greetingFor({ models: 1 }, `c${i}`)));
    expect([...seen].some((l) => l === "One model on the shelf.")).toBe(true);
    for (const line of seen) expect(line).not.toMatch(/\b1 models\b/);
  });

  it("names the upstream host without its scheme, and says so only when there is one", () => {
    const remote = new Set(
      Array.from({ length: 200 }, (_, i) => greetingFor({ upstream: "http://gpu-box:8080" }, `c${i}`)),
    );
    expect([...remote].some((l) => l === "Running on gpu-box:8080.")).toBe(true);
    for (const line of remote) expect(line).not.toMatch(/http/);

    const local = new Set(Array.from({ length: 200 }, (_, i) => greetingFor({ upstream: null }, `c${i}`)));
    expect([...local].some((l) => l === "Running on this machine.")).toBe(true);
  });
});
