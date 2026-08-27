// What is pinned here is `groupForPicker`, and specifically the two things about it that a
// browser click-through cannot show you.
//
// The first is a *negative*: with one backend the picker must be byte-for-byte the menu it was
// before backends existed. That is not something you can see by looking at a multi-backend
// machine, and it is the failure mode nobody would report — a single-backend user paying in
// screen furniture for a feature they do not use. The function reports `multi: false` there, and
// `multi` is the single gate on every heading and badge in the JSX, so pinning it pins the look.
//
// The second is the error row. `error` set means the row is a backend that could not be listed,
// not a model, and it must never reach the loadable path. Here that is asserted structurally: an
// unavailable row is a member of `unavailable` and never of `formats`, and `formats` is the only
// collection the render turns into menu items.
//
// Nothing renders. The suite runs in vitest's node environment like the rest of the SPA's tests,
// so this proves the grouping and not the markup; the markup is gated on `multi` by inspection.

import { describe, expect, it } from "vitest";

import type { LibModel } from "@/api";
import { groupForPicker, type CapabilityFilters } from "@/components/ModelSelector";

const NO_FILTERS: CapabilityFilters = { tools: false, vision: false, audio: false };
const NO_TAGS = new Set<string>();

function model(name: string, backend: string, over: Partial<LibModel> = {}): LibModel {
  return {
    name,
    model_format: "gguf",
    backend,
    error: null,
    size_bytes: 1,
    size_human: "1 GB",
    vision: false,
    audio: false,
    tools: false,
    context_length: null,
    tags: [],
    ...over,
  };
}

/** The degraded row the server sends for a backend it could not list. */
function unavailable(backend: string, error: string): LibModel {
  return model(backend, backend, { model_format: "unavailable", error, size_human: "—", size_bytes: 0 });
}

describe("groupForPicker — one backend", () => {
  it("reports multi: false, so no heading or badge renders", () => {
    const { multi } = groupForPicker(
      [model("a", "local"), model("b", "local", { model_format: "mlx" })],
      NO_FILTERS,
      NO_TAGS,
    );
    expect(multi).toBe(false);
  });

  it("keeps the format shelves alphabetical, which is the old grouping exactly", () => {
    const library = [
      model("q", "local", { model_format: "mlx" }),
      model("a", "local"),
      model("z", "local", { model_format: "remote" }),
      model("b", "local"),
    ];
    const { groups } = groupForPicker(library, NO_FILTERS, NO_TAGS);
    expect(groups).toHaveLength(1);
    expect(groups[0].formats.map(([fmt]) => fmt)).toEqual(["gguf", "mlx", "remote"]);
    // Within a shelf the server's order is kept — the old code pushed in library order too.
    expect(groups[0].formats[0][1].map((m) => m.name)).toEqual(["a", "b"]);
  });

  it("stays single-backend for a server too old to send the field", () => {
    // `backend` is required by the type, so a stale payload arrives as undefined. Every row keys
    // to the same missing value, which is one group: the picker degrades to its old self.
    const stale = [model("a", undefined as unknown as string), model("b", undefined as unknown as string)];
    const { multi, groups } = groupForPicker(stale, NO_FILTERS, NO_TAGS);
    expect(multi).toBe(false);
    expect(groups).toHaveLength(1);
  });

  it("drops the group entirely when the filters match nothing", () => {
    const { groups, shown } = groupForPicker([model("a", "local")], { ...NO_FILTERS, tools: true }, NO_TAGS);
    expect(groups).toEqual([]);
    expect(shown).toBe(0);
  });
});

describe("groupForPicker — several backends", () => {
  const library = [
    model("gemma-4-12b", "local"),
    model("qwen-3", "local", { model_format: "mlx" }),
    model("gemma-4-12b", "gpu-box", { model_format: "remote" }),
  ];

  it("groups by backend in the server's declaration order", () => {
    const { multi, groups } = groupForPicker(library, NO_FILTERS, NO_TAGS);
    expect(multi).toBe(true);
    expect(groups.map((g) => g.backend)).toEqual(["local", "gpu-box"]);
  });

  it("keeps a name served by two backends as two distinct rows", () => {
    const { groups } = groupForPicker(library, NO_FILTERS, NO_TAGS);
    const rows = groups.flatMap((g) => g.formats.flatMap(([, models]) => models));
    const collisions = rows.filter((m) => m.name === "gemma-4-12b");
    expect(collisions.map((m) => m.backend)).toEqual(["local", "gpu-box"]);
  });

  it("counts multi before filtering, so filtering down to one backend keeps the headings", () => {
    // Otherwise every row's origin would disappear the moment a chip narrowed the list to one
    // host — the reader would be told less precisely when they had asked for something specific.
    const withTools = [...library, model("tool-model", "gpu-box", { tools: true, model_format: "remote" })];
    const { multi, groups } = groupForPicker(withTools, { ...NO_FILTERS, tools: true }, NO_TAGS);
    expect(multi).toBe(true);
    expect(groups.map((g) => g.backend)).toEqual(["gpu-box"]);
  });

  it("applies tag filters as AND across a model's own tags", () => {
    const tagged = [
      model("a", "local", { tags: ["fast", "tested"] }),
      model("b", "local", { tags: ["fast"] }),
      model("c", "gpu-box", { tags: ["tested"], model_format: "remote" }),
    ];
    const { groups, shown } = groupForPicker(tagged, NO_FILTERS, new Set(["fast", "tested"]));
    expect(shown).toBe(1);
    expect(groups[0].formats[0][1].map((m) => m.name)).toEqual(["a"]);
  });
});

describe("groupForPicker — a backend that could not be listed", () => {
  const down = unavailable("gpu-box", "did not answer within 2s");

  it("never puts an error row where the render makes menu items", () => {
    const { groups, shown } = groupForPicker([model("a", "local"), down], NO_FILTERS, NO_TAGS);
    const gpu = groups.find((g) => g.backend === "gpu-box");
    expect(gpu?.formats).toEqual([]); // formats is the only collection that becomes loadable rows
    expect(gpu?.unavailable.map((m) => m.error)).toEqual(["did not answer within 2s"]);
    expect(shown).toBe(1); // the error row is not a model and is not counted as one
  });

  it("survives every filter, because no capability can apply to a host that is down", () => {
    const { groups } = groupForPicker(
      [model("a", "local"), down],
      { tools: true, vision: true, audio: true },
      new Set(["tested"]),
    );
    expect(groups.map((g) => g.backend)).toEqual(["gpu-box"]);
    expect(groups[0].unavailable).toHaveLength(1);
  });

  it("does not turn a lone down backend into a multi-backend picker on its own", () => {
    // One declared backend, unreachable: still one origin, so no headings — just the row saying
    // why the list is empty.
    const { multi, groups, shown } = groupForPicker([down], NO_FILTERS, NO_TAGS);
    expect(multi).toBe(false);
    expect(shown).toBe(0);
    expect(groups[0].unavailable).toHaveLength(1);
  });
});
