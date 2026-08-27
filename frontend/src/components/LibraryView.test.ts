// The Library page's grouping decisions, which are pure functions precisely so they can be
// asserted here: the SPA's vitest runs in `environment: "node"` with no DOM library, so what is
// testable is the shape the page is handed, not the markup it produces from it.
//
// The load-bearing case is the FIRST describe block: with one backend the page must render the
// exact list it rendered before backends existed. That is provable rather than assertable by eye
// because both paths are the same `groupByFormat` output fed to the same `FormatGroups` element —
// so pinning `kind === "formats"` and a deep-equal grouping pins the rendering.

import { describe, expect, test } from "vitest";

import type { LibModel } from "@/api";
import {
  backendSections,
  groupByFormat,
  isDegraded,
  libraryShape,
  needsBackendAxis,
  partitionLibrary,
} from "@/components/LibraryView";

/** A healthy model row. */
function model(name: string, model_format: string, backend = "local", extra: Partial<LibModel> = {}): LibModel {
  return {
    name,
    model_format,
    backend,
    error: null,
    size_bytes: 1_000,
    size_human: "1 kB",
    vision: false,
    audio: false,
    tools: false,
    context_length: null,
    tags: [],
    ...extra,
  };
}

/** The degraded row /api/library emits for a backend it could not list. */
function down(backend: string, reason: string): LibModel {
  return {
    name: backend,
    model_format: "unavailable",
    backend,
    error: reason,
    size_bytes: 0,
    size_human: "—",
    vision: false,
    audio: false,
    tools: false,
    context_length: null,
    tags: [],
  };
}

const SINGLE = [
  model("unsloth/Qwen3.5-4B-GGUF", "gguf"),
  model("mlx-community/gemma-4-12b", "mlx"),
  model("meta/llama-9b", "gguf"),
];

describe("one backend renders exactly as before backends existed", () => {
  test("no backend axis is introduced", () => {
    expect(needsBackendAxis(SINGLE)).toBe(false);
  });

  test("the shape is the flat format grouping, identical to grouping by format alone", () => {
    const shape = libraryShape(SINGLE, needsBackendAxis(SINGLE));
    expect(shape.kind).toBe("formats");
    // The pre-backend page did exactly this: bucket by model_format, sort names inside a
    // bucket, sort the buckets. Deep equality here is the "unchanged" claim.
    if (shape.kind !== "formats") throw new Error("unreachable");
    expect(shape.groups).toEqual([
      ["gguf", [model("meta/llama-9b", "gguf"), model("unsloth/Qwen3.5-4B-GGUF", "gguf")]],
      ["mlx", [model("mlx-community/gemma-4-12b", "mlx")]],
    ]);
    expect(shape.groups).toEqual(groupByFormat(SINGLE));
  });

  test("a backend named anything else is still one backend", () => {
    const remote = SINGLE.map((m) => ({ ...m, backend: "gpu-box" }));
    expect(needsBackendAxis(remote)).toBe(false);
    expect(libraryShape(remote, false).kind).toBe("formats");
  });

  test("an empty library needs no backend axis either", () => {
    expect(needsBackendAxis([])).toBe(false);
    expect(libraryShape([], false)).toEqual({ kind: "formats", groups: [] });
  });

  test("nothing is degraded, so nothing is withheld from the model list", () => {
    const { models, degraded } = partitionLibrary(SINGLE);
    expect(models).toEqual(SINGLE);
    expect(degraded).toEqual([]);
  });
});

describe("a second backend turns on the backend axis", () => {
  const MULTI = [
    model("meta/llama-9b", "gguf"),
    model("mlx-community/gemma-4-12b", "mlx"),
    model("gemma-4-12b", "remote", "gpu-box"),
    model("qwen3.5-4b", "remote", "gpu-box"),
  ];

  test("needsBackendAxis flips on the second distinct backend", () => {
    expect(needsBackendAxis(MULTI)).toBe(true);
  });

  test("sections keep the server's listing order, formats sort within a section", () => {
    const shape = libraryShape(MULTI, true);
    if (shape.kind !== "backends") throw new Error("expected backend sections");
    expect(shape.sections.map((s) => s.backend)).toEqual(["local", "gpu-box"]);
    expect(shape.sections[0].groups.map(([fmt]) => fmt)).toEqual(["gguf", "mlx"]);
    expect(shape.sections[0].count).toBe(2);
    expect(shape.sections[1].count).toBe(2);
    expect(shape.sections.every((s) => s.reason === null)).toBe(true);
  });

  test("two hosts serving the same model name stay distinct rows under distinct headings", () => {
    const collide = [model("gemma-4-12b", "gguf"), model("gemma-4-12b", "remote", "gpu-box")];
    const sections = backendSections(collide);
    expect(sections).toHaveLength(2);
    expect(sections.map((s) => s.count)).toEqual([1, 1]);
  });

  test("a backend the tag filter emptied is dropped; only what is on screen is sectioned", () => {
    // `backendSections` is given the tag-filtered rows, so a backend with nothing left
    // contributes nothing. The axis itself was decided from the unfiltered listing.
    const visible = MULTI.filter((m) => m.backend === "gpu-box");
    expect(backendSections(visible).map((s) => s.backend)).toEqual(["gpu-box"]);
  });
});

describe("a backend that could not be listed is a backend, never a model", () => {
  const REASON = "timed out after 5.0s";
  const WITH_DOWN = [model("meta/llama-9b", "gguf"), down("gpu-box", REASON)];

  test("the degraded row is recognised by its error", () => {
    expect(isDegraded(down("gpu-box", REASON))).toBe(true);
    expect(isDegraded(model("meta/llama-9b", "gguf"))).toBe(false);
  });

  test("a row marked unavailable is degraded even without a reason, so it can never be a card", () => {
    expect(isDegraded({ ...down("gpu-box", REASON), error: null })).toBe(true);
  });

  test("it is kept out of the model list, so it cannot be counted or tagged", () => {
    const { models, degraded } = partitionLibrary(WITH_DOWN);
    expect(models).toEqual([model("meta/llama-9b", "gguf")]);
    expect(degraded).toHaveLength(1);
    expect(models.reduce((n, m) => n + m.size_bytes, 0)).toBe(1_000);
  });

  test("it never reaches a format group, in either shape", () => {
    const flat = groupByFormat(partitionLibrary(WITH_DOWN).models);
    expect(flat.map(([fmt]) => fmt)).toEqual(["gguf"]);
    const shape = libraryShape(WITH_DOWN, needsBackendAxis(WITH_DOWN));
    if (shape.kind !== "backends") throw new Error("expected backend sections");
    for (const s of shape.sections) {
      for (const [fmt, list] of s.groups) {
        expect(fmt).not.toBe("unavailable");
        expect(list.every((m) => !isDegraded(m))).toBe(true);
      }
    }
  });

  test("it renders as its backend, with the reason and a zero model count", () => {
    const shape = libraryShape(WITH_DOWN, needsBackendAxis(WITH_DOWN));
    if (shape.kind !== "backends") throw new Error("expected backend sections");
    expect(shape.sections).toEqual([
      { backend: "local", reason: null, groups: groupByFormat([model("meta/llama-9b", "gguf")]), count: 1 },
      { backend: "gpu-box", reason: REASON, groups: [], count: 0 },
    ]);
  });

  test("a lone down backend still forces the axis, so its reason is shown rather than swallowed", () => {
    const only = [down("gpu-box", REASON)];
    expect(needsBackendAxis(only)).toBe(true);
    expect(backendSections(only)).toEqual([{ backend: "gpu-box", reason: REASON, groups: [], count: 0 }]);
  });

  test("a degraded backend is never dropped for being empty, unlike a filtered-out healthy one", () => {
    const sections = backendSections([down("gpu-box", REASON)]);
    expect(sections).toHaveLength(1);
  });

  test("a reason-less degraded row still gets an explanation rather than a blank line", () => {
    const [section] = backendSections([{ ...down("gpu-box", REASON), error: null }]);
    expect(section.reason).toBe("no reason reported");
  });
});

describe("defensive normalisation", () => {
  test("a row from a server predating the backend field falls back to local", () => {
    const legacy = { ...model("meta/llama-9b", "gguf"), backend: "" };
    expect(needsBackendAxis([legacy])).toBe(false);
    expect(backendSections([legacy])[0].backend).toBe("local");
  });
});
