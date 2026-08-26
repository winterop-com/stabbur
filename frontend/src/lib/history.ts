// IndexedDB-backed persistence for the chat history — the conversations, their messages, and the
// image/audio a message carries. THE ONE WAY IN AND OUT: no component opens a database, and no call
// site knows what a record looks like at rest. That is deliberate beyond tidiness — the whole
// serialisation boundary is `encodeMessage`/`decodeMessage` and the two `put` calls below, so a
// later encrypt-at-rest layer is a wrapper around whole records here rather than a change smeared
// across every caller. (Appearance and per-machine preferences stay in localStorage; see store.ts.)
//
// WHY NOT localStorage. History used to be one JSON blob under `stabbur.conversations`, and a
// conversation carries its attachments inline as base64 data URLs. localStorage is ~5 MB per
// origin, base64 inflates binary by a third, and a single pasted screenshot is often a megabyte:
// two or three images and the quota was gone. What the old code did about it — re-save the
// transcript with the user's attachments deleted — was data loss dressed as a fallback. IndexedDB
// has orders of magnitude more room, stores a Blob as bytes with no base64 tax, and takes a write
// per changed row rather than a rewrite of everything on every keystroke of a stream.
//
// TWO STORES, NOT ONE. `conversations` holds the record without its transcript; `messages` holds
// each turn keyed by its own id with a `convId`. Appending a turn (or growing one token by token)
// then writes one small row instead of rewriting a conversation that may hold 40 images.
//
// INDEXED ON IDS ONLY. The single index is `messages.convId`. Nothing is indexed on a title or on
// message text, because an index has to hold its key in the clear to be useful — indexing content
// would quietly make content the one thing an encrypted mode could never encrypt. Title search is
// a filter over the loaded list in the sidebar, which needs no index at all.

import { normalizeSettings, type Settings } from "@/lib/store";
import type { ChatMessage, Conversation, TitleSource } from "@/lib/types";

const DB_NAME = "stabbur";
const DB_VERSION = 1;
const CONVERSATIONS = "conversations";
const MESSAGES = "messages";
/** The one index: a message's conversation. See the header for why nothing else is indexed. */
const BY_CONVERSATION = "convId";
/** Where history lived before this store. Read once on first load, then cleared — see `migrate`. */
const LEGACY_KEY = "stabbur.conversations";

/** A conversation at rest: everything but its transcript, which lives in `messages`. */
interface StoredConversation {
  id: string;
  title: string;
  /** OPTIONAL at rest, and it has to be: every conversation written before the model started
   *  naming them has no such field, and `undefined` there means "derived", not "corrupt". */
  titledBy?: TitleSource;
  settings: Settings;
  createdAt: number;
  updatedAt: number;
}

/** Read a stored `titledBy` defensively. An older record, or anything that isn't one of the three
 *  words, is `derived`. That is safe rather than lossy because titling is only ever attempted on a
 *  conversation whose first exchange happened in this session — a chat read back from the store is
 *  never re-titled, whatever this says — while guessing `user` would make a placeholder permanent. */
function titleSource(value: unknown): TitleSource {
  return value === "user" || value === "model" ? value : "derived";
}

/** An attachment at rest. A Blob normally — the string form is the escape hatch for a `url` that
 *  wasn't a data URL we could decode, which is kept verbatim rather than dropped. */
type StoredMedia = Blob | string;

/** A message at rest: the in-memory turn, plus which conversation it belongs to and where in it,
 *  and with its data-URL attachments held as bytes. */
type StoredMessage = Omit<ChatMessage, "images" | "audios"> & {
  convId: string;
  seq: number;
  images?: StoredMedia[];
  audios?: StoredMedia[];
};

/** Outcome of a persistence attempt. There is no third "saved, minus your attachments" state any
 *  more: the quota cliff that justified one is gone, and a store that silently discards what it
 *  was handed is worse than one that says it failed. */
export type SaveResult = "ok" | "failed";

/**
 * What the browser has promised about this origin's storage.
 *
 * This is not a detail. IndexedDB's default bucket is BEST-EFFORT: the browser may evict the whole
 * thing under disk pressure, without asking and without telling anyone. Moving history here would
 * otherwise have swapped "silently drops your attachments past 5 MB" for "silently drops
 * everything when the disk fills" — a different failure, not a fixed one. `persist()` asks for the
 * store to be marked persistent, which means it is not reclaimed without the user's own action.
 *
 * - `persistent` — granted (or already held). Nothing evicts it behind the user's back.
 * - `best-effort` — asked and refused. Chrome decides on engagement heuristics, so this is a
 *   normal outcome on a site opened for the first time, and the history is evictable.
 * - `unknown` — no Storage API to ask (an insecure context, an older browser).
 */
export type Persistence = "persistent" | "best-effort" | "unknown";

export interface LoadResult {
  conversations: Conversation[];
  /** False when the store could not be read at all (IndexedDB blocked, e.g. some private modes).
   *  The caller must then refuse to save: overwriting history we could not read is how it is lost. */
  ok: boolean;
  /** How many conversations came across from the old localStorage key on this load. */
  migrated: number;
  /** Whether the browser will keep this store — see `Persistence`. */
  persistence: Persistence;
}

/** How much room the history is using and may use, alongside what the browser has promised of it.
 *  `null` for either figure means the browser declined to say (the Storage API is absent). */
export interface HistorySpace {
  usage: number | null;
  quota: number | null;
  persistence: Persistence;
}

// --- data URL <-> Blob ---------------------------------------------------------------------
//
// The in-memory shape is unchanged: a message still carries data URL strings, because that is what
// <img>/<audio> take, what the export path embeds, and what `buildContent` puts back on the wire
// when history is replayed to the model. Converting only at the storage boundary buys the binary
// win where it counts (at rest) without threading Blob lifetimes through the render tree.
//
// Hand-rolled via atob/btoa rather than fetch()/FileReader so the encode direction stays
// SYNCHRONOUS: an IndexedDB transaction commits at the end of its event-loop turn, so a write path
// that had to await mid-transaction would abort it.

const DATA_URL = /^data:([^;,]*)(;base64)?,([\s\S]*)$/;

function toStoredMedia(url: string): StoredMedia {
  const m = DATA_URL.exec(url);
  if (!m) return url; // not a data URL (a blob:/http: src): keep the string, don't guess
  const [, mime, base64, payload] = m;
  try {
    if (!base64) return new Blob([decodeURIComponent(payload)], { type: mime || "text/plain" });
    const binary = atob(payload);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: mime || "application/octet-stream" });
  } catch {
    return url; // malformed base64: store it as it came rather than lose the attachment
  }
}

async function toDataUrl(media: StoredMedia): Promise<string> {
  if (typeof media === "string") return media;
  const bytes = new Uint8Array(await media.arrayBuffer());
  // Chunked because String.fromCharCode is applied to its argument list, and a multi-megabyte
  // image spread over one call blows the engine's argument limit.
  const CHUNK = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += CHUNK) binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  return `data:${media.type || "application/octet-stream"};base64,${btoa(binary)}`;
}

// --- IndexedDB plumbing --------------------------------------------------------------------

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      if (typeof indexedDB === "undefined") {
        reject(new Error("IndexedDB is unavailable"));
        return;
      }
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(CONVERSATIONS)) db.createObjectStore(CONVERSATIONS, { keyPath: "id" });
        if (!db.objectStoreNames.contains(MESSAGES)) {
          db.createObjectStore(MESSAGES, { keyPath: "id" }).createIndex(BY_CONVERSATION, "convId");
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("IndexedDB open failed"));
      request.onblocked = () => reject(new Error("IndexedDB open blocked by another tab"));
    });
    // A failed open must not be cached forever — a later call gets a fresh attempt.
    dbPromise.catch(() => {
      dbPromise = null;
    });
  }
  return dbPromise;
}

// --- eviction ---------------------------------------------------------------------------------

let persistence: Persistence = "unknown";
let persistenceAsked = false;

/** Ask, ONCE per session, for this origin's storage to be kept. Asking is cheap but it is a
 *  request, not a setting: the answer is the browser's, and a refusal is reported rather than
 *  swallowed so the UI can say the history is evictable. */
async function ensurePersistence(): Promise<Persistence> {
  if (persistenceAsked) return persistence;
  persistenceAsked = true;
  try {
    if (typeof navigator === "undefined" || !navigator.storage?.persist) return persistence;
    // `persisted()` first: an origin already granted this must not be asked again, which in some
    // browsers is a permission prompt.
    persistence = (await navigator.storage.persisted()) || (await navigator.storage.persist())
      ? "persistent"
      : "best-effort";
  } catch {
    persistence = "unknown";
  }
  return persistence;
}

/** What the history costs and what it is allowed to cost, for anything that wants to report it.
 *  Nothing here writes; it is safe to call whenever a surface wants the number. */
export async function historySpace(): Promise<HistorySpace> {
  const kept = await ensurePersistence();
  try {
    if (typeof navigator === "undefined" || !navigator.storage?.estimate) {
      return { usage: null, quota: null, persistence: kept };
    }
    const estimate = await navigator.storage.estimate();
    return { usage: estimate.usage ?? null, quota: estimate.quota ?? null, persistence: kept };
  } catch {
    return { usage: null, quota: null, persistence: kept };
  }
}

function asPromise<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function committed(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

// --- record (de)serialisation ---------------------------------------------------------------
//
// Whole records in, whole records out. Both functions are the seam an encrypt/decrypt pair would
// wrap: encode the object to bytes here, decode there, and nothing above this file changes.

function encodeConversation(c: Conversation): StoredConversation {
  return {
    id: c.id,
    title: c.title,
    titledBy: c.titledBy,
    settings: c.settings,
    createdAt: c.createdAt,
    updatedAt: c.updatedAt,
  };
}

function encodeMessage(m: ChatMessage, convId: string, seq: number): StoredMessage {
  const stored: StoredMessage = { ...m, convId, seq };
  if (m.images) stored.images = m.images.map(toStoredMedia);
  if (m.audios) stored.audios = m.audios.map(toStoredMedia);
  return stored;
}

async function decodeMessage(stored: StoredMessage): Promise<ChatMessage> {
  // convId/seq are where the row lives, not part of the turn — dropped on the way back in.
  const { convId: _convId, seq: _seq, images, audios, ...rest } = stored;
  const m: ChatMessage = { ...rest };
  if (images) m.images = await Promise.all(images.map(toDataUrl));
  if (audios) m.audios = await Promise.all(audios.map(toDataUrl));
  // Drop any still-pending confirmation: it belongs to a stream that no longer exists (a reload
  // mid-stream), so its Approve/Deny buttons would post an id the server already dropped.
  // Resolved notes (an auto-denied timeout) stay.
  if (m.confirms) m.confirms = m.confirms.filter((cf) => cf.status !== "pending");
  return m;
}

// --- the write diff -------------------------------------------------------------------------
//
// What was last written, so a save can put only what actually changed. React's updates are
// immutable — `upsertConv` rebuilds exactly the message it touched and leaves the rest by
// reference — so object identity is an accurate and very cheap "is this row stale".

interface Snapshot {
  conversations: Map<string, StoredConversation>;
  /** message id -> where it sat and the exact object that was written for it */
  messages: Map<string, { convId: string; seq: number; source: ChatMessage }>;
}

let snapshot: Snapshot = { conversations: new Map(), messages: new Map() };

function metaChanged(was: StoredConversation | undefined, now: StoredConversation): boolean {
  return (
    !was ||
    was.title !== now.title ||
    // Compared in its own right, not folded into the title: renaming a chat to the string it
    // already had changes nothing visible and everything about whether the model may replace it.
    was.titledBy !== now.titledBy ||
    was.createdAt !== now.createdAt ||
    was.updatedAt !== now.updatedAt ||
    was.settings !== now.settings
  );
}

/**
 * Persist the history, writing only the rows that changed since the last successful save.
 *
 * Never throws: a store that cannot be written is reported, not raised, because every caller is a
 * React effect with nowhere to put an exception. On failure the snapshot is deliberately left
 * alone, so the next save retries the whole delta rather than assuming it landed.
 */
export async function saveConversations(convs: Conversation[]): Promise<SaveResult> {
  // Everything is computed BEFORE the transaction opens: an IndexedDB transaction auto-commits at
  // the end of the event-loop turn it was created in, so every put/delete has to be issued without
  // an await in between.
  const nextConversations = new Map<string, StoredConversation>();
  const nextMessages: Snapshot["messages"] = new Map();
  const convPuts: StoredConversation[] = [];
  const msgPuts: StoredMessage[] = [];
  for (const c of convs) {
    const meta = encodeConversation(c);
    nextConversations.set(c.id, meta);
    if (metaChanged(snapshot.conversations.get(c.id), meta)) convPuts.push(meta);
    c.messages.forEach((m, seq) => {
      nextMessages.set(m.id, { convId: c.id, seq, source: m });
      const was = snapshot.messages.get(m.id);
      // `seq` is part of the comparison because a regenerate drops turns from the middle, which
      // moves every message after it without touching the objects themselves.
      if (!was || was.source !== m || was.seq !== seq || was.convId !== c.id) msgPuts.push(encodeMessage(m, c.id, seq));
    });
  }
  const convDeletes = [...snapshot.conversations.keys()].filter((id) => !nextConversations.has(id));
  const msgDeletes = [...snapshot.messages.keys()].filter((id) => !nextMessages.has(id));
  if (!convPuts.length && !msgPuts.length && !convDeletes.length && !msgDeletes.length) return "ok";

  try {
    const db = await openDb();
    const tx = db.transaction([CONVERSATIONS, MESSAGES], "readwrite");
    const conversationStore = tx.objectStore(CONVERSATIONS);
    const messageStore = tx.objectStore(MESSAGES);
    for (const c of convPuts) conversationStore.put(c);
    for (const m of msgPuts) messageStore.put(m);
    for (const id of convDeletes) conversationStore.delete(id);
    for (const id of msgDeletes) messageStore.delete(id);
    await committed(tx);
  } catch {
    return "failed";
  }
  snapshot = { conversations: nextConversations, messages: nextMessages };
  return "ok";
}

/**
 * Read the whole history, migrating anything still in localStorage first.
 *
 * Also (re)seeds the write diff, so the first save after a load writes nothing. Never throws — an
 * unreadable store comes back as `ok: false`, which the caller turns into "don't persist" rather
 * than into an empty history it would then save over the top of the real one.
 */
export async function loadConversations(): Promise<LoadResult> {
  snapshot = { conversations: new Map(), messages: new Map() };
  // The store is being opened: this is the one moment to ask the browser to keep it (see
  // `Persistence`). Asked here rather than per save, and never asked twice.
  const kept = await ensurePersistence();
  let db: IDBDatabase;
  try {
    db = await openDb();
  } catch {
    return { conversations: [], ok: false, migrated: 0, persistence: kept };
  }

  // Before reading: a migration failure is not a load failure. It leaves the old key in place and
  // rolls its own transaction back, so this read simply finds whatever was already here and the
  // next load tries again.
  let migrated = 0;
  try {
    migrated = await migrate(db);
  } catch {
    migrated = 0;
  }

  let storedConvs: StoredConversation[];
  let storedMsgs: StoredMessage[];
  try {
    const tx = db.transaction([CONVERSATIONS, MESSAGES], "readonly");
    const convRequest = asPromise(tx.objectStore(CONVERSATIONS).getAll() as IDBRequest<StoredConversation[]>);
    const msgRequest = asPromise(tx.objectStore(MESSAGES).getAll() as IDBRequest<StoredMessage[]>);
    [storedConvs, storedMsgs] = await Promise.all([convRequest, msgRequest]);
  } catch {
    return { conversations: [], ok: false, migrated, persistence: kept };
  }

  const byConversation = new Map<string, StoredMessage[]>();
  for (const m of storedMsgs) {
    const list = byConversation.get(m.convId);
    if (list) list.push(m);
    else byConversation.set(m.convId, [m]);
  }
  // A message whose conversation is gone is unreachable — it can only come from a half-written
  // delete — and is left out here rather than resurrected under a conversation that doesn't exist.
  const conversations: Conversation[] = [];
  for (const stored of storedConvs) {
    const rows = (byConversation.get(stored.id) ?? []).sort((a, b) => a.seq - b.seq);
    const messages = await Promise.all(rows.map(decodeMessage));
    conversations.push({
      id: stored.id,
      title: stored.title,
      titledBy: titleSource(stored.titledBy),
      settings: normalizeSettings(stored.settings),
      createdAt: stored.createdAt,
      updatedAt: stored.updatedAt,
      messages,
    });
  }
  // Newest first, which is what the old array order was: a new conversation was prepended and
  // nothing ever reordered the list. Every consumer re-sorts by `updatedAt` anyway.
  conversations.sort((a, b) => b.createdAt - a.createdAt);

  // Seed the diff from what was just read, so the caller's first flush is a no-op.
  for (const c of conversations) {
    snapshot.conversations.set(c.id, encodeConversation(c));
    c.messages.forEach((m, seq) => snapshot.messages.set(m.id, { convId: c.id, seq, source: m }));
  }
  return { conversations, ok: true, migrated, persistence: kept };
}

// --- migration off localStorage ---------------------------------------------------------------

/** Coerce one untrusted legacy record into a conversation, or null if it isn't one. */
function normalizeConversation(value: unknown): Conversation | null {
  if (!value || typeof value !== "object") return null;
  const c = value as Partial<Conversation>;
  if (typeof c.id !== "string" || !c.id || !Array.isArray(c.messages)) return null;
  const messages = c.messages.filter((m): m is ChatMessage => !!m && typeof m === "object" && typeof m.id === "string");
  return {
    id: c.id,
    title: typeof c.title === "string" ? c.title : "New chat",
    titledBy: titleSource(c.titledBy),
    settings: normalizeSettings(c.settings),
    createdAt: typeof c.createdAt === "number" ? c.createdAt : Date.now(),
    updatedAt: typeof c.updatedAt === "number" ? c.updatedAt : Date.now(),
    messages,
  };
}

/**
 * Move `stabbur.conversations` into IndexedDB, once, and only clear it once that is proven.
 *
 * This is real user history, so the order is migrate, verify, then delete — never delete on the
 * assumption that the write worked. The write is ONE transaction, which is what makes "fails
 * partway" a non-state: IndexedDB rolls the whole thing back, this throws, and the old key is
 * still there for the next load to try again. Unparseable content is also left in place, because
 * data we could not interpret is not data we may throw away.
 *
 * Returns how many conversations came across (0 when there was nothing to do).
 */
async function migrate(db: IDBDatabase): Promise<number> {
  let raw: string | null;
  try {
    raw = localStorage.getItem(LEGACY_KEY);
  } catch {
    return 0; // storage blocked: there is nothing to read and nothing to lose
  }
  if (!raw) return 0;

  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) throw new Error("legacy history is not an array");
  const conversations = parsed.map(normalizeConversation).filter((c): c is Conversation => c !== null);

  const tx = db.transaction([CONVERSATIONS, MESSAGES], "readwrite");
  const conversationStore = tx.objectStore(CONVERSATIONS);
  const messageStore = tx.objectStore(MESSAGES);
  try {
    for (const c of conversations) {
      conversationStore.put(encodeConversation(c));
      c.messages.forEach((m, seq) => messageStore.put(encodeMessage(m, c.id, seq)));
    }
  } catch (e) {
    // A put that throws synchronously (a value the structured clone can't take) leaves the
    // transaction open; abort it explicitly so the partial write is rolled back rather than
    // committed at the end of this turn.
    tx.abort();
    throw e;
  }
  await committed(tx);

  // VERIFY BEFORE DELETING. The transaction committing is a strong signal, but the key is the only
  // remaining copy until this passes, so read the ids back and insist every one of them is there.
  const check = db.transaction(CONVERSATIONS, "readonly");
  const ids = await asPromise(check.objectStore(CONVERSATIONS).getAllKeys());
  const present = new Set(ids.map(String));
  const missing = conversations.filter((c) => !present.has(c.id));
  if (missing.length) throw new Error(`migration verification failed: ${missing.length} conversation(s) missing`);

  localStorage.removeItem(LEGACY_KEY);
  return conversations.length;
}
