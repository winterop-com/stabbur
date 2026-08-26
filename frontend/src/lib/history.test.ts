// The history store is the only place in the SPA that holds something the user cannot get back if
// it goes wrong, and this change moved it between two storage engines. That is what these tests
// are for — not coverage, but the four moments where the user's transcripts could be lost: a first
// run, the one-way migration off localStorage, that migration failing halfway, and the ordinary
// save/reload round trip that has to preserve an attachment byte for byte.
//
// Every test gets a brand-new IDBFactory and a brand-new module instance: lib/history caches an
// open database and the write diff at module scope, and a test that inherited either from the one
// before it would be testing the wrong thing.

import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_SETTINGS } from "@/lib/store";
import type { ChatMessage, Conversation } from "@/lib/types";

const LEGACY_KEY = "stabbur.conversations";

/** A localStorage that behaves like the browser's, including throwing on a missing key never. */
class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length(): number {
    return this.map.size;
  }
  clear(): void {
    this.map.clear();
  }
  getItem(key: string): string | null {
    return this.map.get(key) ?? null;
  }
  key(index: number): string | null {
    return [...this.map.keys()][index] ?? null;
  }
  removeItem(key: string): void {
    this.map.delete(key);
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
}

/** Load a pristine copy of the module under test (fresh db handle, empty write diff) — which is
 *  also how a reload is simulated: the page comes back with neither. */
async function freshModule(): Promise<typeof import("@/lib/history")> {
  vi.resetModules();
  return import("@/lib/history");
}

const PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

function message(id: string, content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return { id, role: "user", content, ...extra };
}

function conversation(id: string, messages: ChatMessage[], at = 1000): Conversation {
  return {
    id,
    title: `chat ${id}`,
    titledBy: "derived",
    settings: { ...DEFAULT_SETTINGS },
    createdAt: at,
    updatedAt: at,
    messages,
  };
}

/** Read a message row exactly as it sits in the database, bypassing the module's decode. */
function rawMessage(id: string): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("stabbur");
    open.onsuccess = () => {
      const request = open.result.transaction("messages", "readonly").objectStore("messages").get(id);
      request.onsuccess = () => resolve(request.result as Record<string, unknown>);
      request.onerror = () => reject(request.error);
    };
    open.onerror = () => reject(open.error);
  });
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  globalThis.localStorage = new MemoryStorage();
});

describe("a fresh install", () => {
  it("reads an empty history rather than failing", async () => {
    const { loadConversations } = await freshModule();
    // "unknown" persistence because node has no Storage API to ask — which is also the assertion
    // that a browser without one still loads rather than falling over on the request.
    expect(await loadConversations()).toEqual({
      conversations: [],
      ok: true,
      migrated: 0,
      persistence: "unknown",
    });
  });
});

describe("migrating off localStorage", () => {
  it("moves every conversation across, keeps the attachment, and only then clears the old key", async () => {
    const legacy = [
      conversation("b", [message("m2", "with a picture", { images: [PIXEL] })], 2000),
      conversation("a", [message("m1", "hello"), message("m1b", "there")], 1000),
    ];
    localStorage.setItem(LEGACY_KEY, JSON.stringify(legacy));

    const { loadConversations } = await freshModule();
    const result = await loadConversations();

    expect(result.ok).toBe(true);
    expect(result.migrated).toBe(2);
    // Newest first, and each transcript back in its own order.
    expect(result.conversations.map((c) => c.id)).toEqual(["b", "a"]);
    expect(result.conversations[1].messages.map((m) => m.id)).toEqual(["m1", "m1b"]);
    // The attachment survives the round trip through bytes and back, character for character.
    expect(result.conversations[0].messages[0].images).toEqual([PIXEL]);
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull();

    // And it stays migrated: a second load finds it in IndexedDB with nothing left to migrate.
    const second = await (await freshModule()).loadConversations();
    expect(second.migrated).toBe(0);
    expect(second.conversations.map((c) => c.id)).toEqual(["b", "a"]);
  });

  it("stores an attachment as bytes, not as the base64 string it arrived as", async () => {
    localStorage.setItem(LEGACY_KEY, JSON.stringify([conversation("a", [message("m1", "", { images: [PIXEL] })])]));
    await (await freshModule()).loadConversations();

    const row = await rawMessage("m1");
    const images = row.images as Blob[];
    expect(images[0]).toBeInstanceOf(Blob);
    expect(images[0].type).toBe("image/png");
    // Bytes, so smaller than the base64 that encoded them — the 33% tax this move was for.
    expect(images[0].size).toBeLessThan(PIXEL.length);
  });

  it("keeps the old key when the write fails partway, and migrates cleanly on the retry", async () => {
    const legacy = [
      conversation("a", [message("m1", "one"), message("m2", "two")], 3000),
      conversation("b", [message("m3", "three")], 2000),
    ];
    localStorage.setItem(LEGACY_KEY, JSON.stringify(legacy));

    // Fail on the third put — after the first conversation and its first message are already in
    // the transaction, so this is genuinely "partway" and not "before anything happened".
    const put = IDBObjectStore.prototype.put;
    let calls = 0;
    IDBObjectStore.prototype.put = function patched(this: IDBObjectStore, ...args: Parameters<typeof put>) {
      calls += 1;
      if (calls === 3) throw new Error("simulated write failure");
      return put.apply(this, args);
    };
    let failed;
    try {
      failed = await (await freshModule()).loadConversations();
    } finally {
      IDBObjectStore.prototype.put = put;
    }

    // Rolled back whole: nothing landed, nothing was reported as migrated...
    expect(failed.ok).toBe(true);
    expect(failed.migrated).toBe(0);
    expect(failed.conversations).toEqual([]);
    // ...and the only surviving copy is still exactly where it was.
    expect(JSON.parse(localStorage.getItem(LEGACY_KEY) ?? "null")).toHaveLength(2);

    const retried = await (await freshModule()).loadConversations();
    expect(retried.migrated).toBe(2);
    expect(retried.conversations.map((c) => c.id)).toEqual(["a", "b"]);
    expect(retried.conversations[0].messages.map((m) => m.content)).toEqual(["one", "two"]);
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
  });

  it("leaves content it cannot parse alone rather than discarding it", async () => {
    localStorage.setItem(LEGACY_KEY, "{ not json");
    const result = await (await freshModule()).loadConversations();
    expect(result.ok).toBe(true);
    expect(result.migrated).toBe(0);
    expect(localStorage.getItem(LEGACY_KEY)).toBe("{ not json");
  });
});

describe("save and load", () => {
  it("round-trips conversations, transcripts and attachments", async () => {
    const first = await freshModule();
    expect(await first.loadConversations()).toMatchObject({ ok: true });
    const convs = [
      conversation("b", [message("m2", "second", { images: [PIXEL], reasoning: "thinking" })], 2000),
      conversation("a", [message("m1", "first", { role: "assistant", stats: undefined })], 1000),
    ];
    expect(await first.saveConversations(convs)).toBe("ok");

    const reloaded = await (await freshModule()).loadConversations();
    expect(reloaded.conversations).toEqual(convs);
  });

  it("carries an edit, an append and a delete through to the next load", async () => {
    const first = await freshModule();
    await first.loadConversations();
    const a = conversation("a", [message("m1", "one")], 1000);
    const b = conversation("b", [message("m2", "two")], 2000);
    expect(await first.saveConversations([b, a])).toBe("ok");

    // Same shape a React update produces: new objects for what changed, the rest by reference.
    const edited: Conversation = {
      ...b,
      title: "renamed",
      updatedAt: 3000,
      messages: [{ ...b.messages[0], content: "two, edited" }, message("m4", "appended")],
    };
    expect(await first.saveConversations([edited])).toBe("ok");

    const reloaded = await (await freshModule()).loadConversations();
    expect(reloaded.conversations).toHaveLength(1); // conversation "a" is gone, not orphaned
    expect(reloaded.conversations[0].title).toBe("renamed");
    expect(reloaded.conversations[0].messages.map((m) => m.content)).toEqual(["two, edited", "appended"]);
  });

  it("remembers that a title was the user's own, so a reload cannot make it replaceable", async () => {
    const first = await freshModule();
    await first.loadConversations();
    const named: Conversation = { ...conversation("a", [message("m1", "one")]), title: "Q3 numbers", titledBy: "user" };
    expect(await first.saveConversations([named])).toBe("ok");

    // The whole point of persisting the field: after a reload the title is a string like any other,
    // and only `titledBy` still says it may not be overwritten.
    const reloaded = await (await freshModule()).loadConversations();
    expect(reloaded.conversations[0].title).toBe("Q3 numbers");
    expect(reloaded.conversations[0].titledBy).toBe("user");
  });

  it("reads a record written before titles were tracked as replaceable, not as corrupt", async () => {
    // Exactly what is in the store for every conversation that existed before this feature: the
    // record, minus the field. It must come back usable, on the replaceable side — as must one
    // carrying something that isn't a title source at all.
    const old = { ...conversation("a", [message("m1", "one")]) } as Record<string, unknown>;
    delete old.titledBy;
    const bogus = { ...conversation("b", [message("m2", "two")], 2000), titledBy: 7 };
    localStorage.setItem(LEGACY_KEY, JSON.stringify([old, bogus]));

    const result = await (await freshModule()).loadConversations();
    expect(result.conversations.map((c) => c.titledBy)).toEqual(["derived", "derived"]);
  });

  it("drops a confirmation still awaiting a decision, since the stream it belonged to is over", async () => {
    const first = await freshModule();
    await first.loadConversations();
    const pending = message("m1", "", {
      role: "assistant",
      confirms: [
        { id: "c1", tool: "write", args: {}, status: "pending" },
        { id: "c2", tool: "write", args: {}, status: "resolved", approved: false, reason: "timeout" },
      ],
    });
    await first.saveConversations([conversation("a", [pending])]);

    const reloaded = await (await freshModule()).loadConversations();
    expect(reloaded.conversations[0].messages[0].confirms?.map((c) => c.id)).toEqual(["c2"]);
  });

  it("reports a failure instead of throwing, and retries the whole delta next time", async () => {
    const mod = await freshModule();
    await mod.loadConversations();
    const convs = [conversation("a", [message("m1", "one")])];

    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function patched(this: IDBObjectStore) {
      throw new Error("simulated write failure");
    };
    let outcome;
    try {
      outcome = await mod.saveConversations(convs);
    } finally {
      IDBObjectStore.prototype.put = put;
    }
    expect(outcome).toBe("failed");

    // The failed write must not have been recorded as done: saving the same list again writes it.
    expect(await mod.saveConversations(convs)).toBe("ok");
    const reloaded = await (await freshModule()).loadConversations();
    expect(reloaded.conversations.map((c) => c.id)).toEqual(["a"]);
  });
});
