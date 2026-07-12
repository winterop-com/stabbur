// Framework-free connection state machine driving the ConnectionGate. It owns
// the status probe / model-load lifecycle so the React layer just subscribes.
// All side effects (fetch, timers, clock) are injected, so this is unit-testable
// without chrome or a real server.

import { apiConfig, apiFetch } from "@/lib/http";
import type { Status } from "@/api";

export type ConnectionPhase =
  | "disconnected"
  | "connecting"
  | "needs-token"
  | "needs-model"
  | "loading"
  | "ready"
  | "error";

export interface ConnectionSnapshot {
  phase: ConnectionPhase;
  status: Status | null;
  error: string | null;
  /** True once a mutating POST returned 403 (heim cross-site guard). */
  guardBlocked: boolean;
  /** ms-epoch the current load began, for elapsed display. */
  loadStartedAt: number | null;
  /** ms-epoch after which a load is considered timed out. */
  loadDeadline: number | null;
}

/** Result of probing GET /api/status. */
export type StatusProbe =
  | { kind: "status"; status: Status }
  | { kind: "unauthorized" }
  | { kind: "network-error" }
  | { kind: "http-error"; code: number };

/** Result of POST /api/load/{name}. */
export type LoadResult =
  | { kind: "status"; status: Status }
  | { kind: "guard-blocked" }
  | { kind: "conflict" }
  | { kind: "not-found" }
  | { kind: "unauthorized" }
  | { kind: "network-error" }
  | { kind: "http-error"; code: number };

export type TimerHandle = unknown;

export interface ConnectionDeps {
  probeStatus: () => Promise<StatusProbe>;
  loadModel: (name: string) => Promise<LoadResult>;
  setTimer: (fn: () => void, ms: number) => TimerHandle;
  clearTimer: (handle: TimerHandle) => void;
  now: () => number;
}

const RETRY_MS = 3000; // disconnected auto-retry
const POLL_MS = 2000; // loading poll cadence
const DEFAULT_LOAD_TIMEOUT_S = 300;

export interface Connection {
  subscribe: (cb: (snap: ConnectionSnapshot) => void) => () => void;
  getState: () => ConnectionSnapshot;
  connect: () => void;
  loadModel: (name: string) => void;
  retry: () => void;
  dispose: () => void;
}

export function createConnection(deps: ConnectionDeps): Connection {
  let snap: ConnectionSnapshot = {
    phase: "disconnected",
    status: null,
    error: null,
    guardBlocked: false,
    loadStartedAt: null,
    loadDeadline: null,
  };
  const listeners = new Set<(s: ConnectionSnapshot) => void>();
  let timer: TimerHandle | null = null;
  let disposed = false;
  // Guards against a late async result from a superseded action clobbering state.
  let epoch = 0;

  function emit(patch: Partial<ConnectionSnapshot>): void {
    snap = { ...snap, ...patch };
    for (const cb of listeners) cb(snap);
  }

  function clearTimer(): void {
    if (timer !== null) {
      deps.clearTimer(timer);
      timer = null;
    }
  }

  function schedule(fn: () => void, ms: number): void {
    clearTimer();
    timer = deps.setTimer(() => {
      timer = null;
      if (!disposed) fn();
    }, ms);
  }

  function loadTimeoutMs(status: Status | null): number {
    const secs = status?.runtime_load_timeout ?? DEFAULT_LOAD_TIMEOUT_S;
    return secs * 1000;
  }

  // Enter (or continue) the loading phase; `keepLoadClock` preserves loadStartedAt
  // across polls of an in-progress load.
  function enterLoading(status: Status, keepLoadClock: boolean): void {
    const startedAt = keepLoadClock && snap.loadStartedAt !== null ? snap.loadStartedAt : deps.now();
    emit({
      phase: "loading",
      status,
      error: null,
      loadStartedAt: startedAt,
      loadDeadline: startedAt + loadTimeoutMs(status),
    });
    schedule(pollLoading, POLL_MS);
  }

  // Map a fetched Status onto a phase.
  function reduceStatus(status: Status, keepLoadClock: boolean): void {
    if (status.state === "ready") {
      clearTimer();
      emit({ phase: "ready", status, error: null, loadStartedAt: null, loadDeadline: null });
      return;
    }
    if (status.state === "loading") {
      enterLoading(status, keepLoadClock);
      return;
    }
    // stopped
    if (status.error) {
      clearTimer();
      emit({ phase: "error", status, error: status.error, loadStartedAt: null, loadDeadline: null });
      return;
    }
    if (status.locked) {
      // A locked server loads its single model at startup; treat as loading.
      enterLoading(status, keepLoadClock);
      return;
    }
    // stopped, unlocked, no error -> needs a model (gate shows Load or a note).
    clearTimer();
    emit({ phase: "needs-model", status, error: null, loadStartedAt: null, loadDeadline: null });
  }

  function applyProbe(probe: StatusProbe, keepLoadClock: boolean): void {
    switch (probe.kind) {
      case "network-error":
        clearTimer();
        emit({ phase: "disconnected", error: null });
        schedule(connect, RETRY_MS);
        return;
      case "unauthorized":
        clearTimer();
        emit({ phase: "needs-token", error: null });
        return;
      case "http-error":
        clearTimer();
        emit({ phase: "error", error: `Server error (HTTP ${probe.code})` });
        return;
      case "status":
        reduceStatus(probe.status, keepLoadClock);
        return;
    }
  }

  function connect(): void {
    if (disposed) return;
    clearTimer();
    const mine = ++epoch;
    // Reset guardBlocked: the user may have fixed cors_origins and restarted the server, and
    // the CORS hint replaces the Load button while the flag is set — a reconnect must be able
    // to recover instead of wedging on the hint until the panel remounts.
    emit({ phase: "connecting", error: null, guardBlocked: false });
    void deps.probeStatus().then((probe) => {
      if (disposed || mine !== epoch) return;
      applyProbe(probe, false);
    });
  }

  function pollLoading(): void {
    if (disposed) return;
    if (snap.loadDeadline !== null && deps.now() > snap.loadDeadline) {
      clearTimer();
      emit({ phase: "error", error: "Model load timed out", loadStartedAt: null, loadDeadline: null });
      return;
    }
    const mine = epoch; // polling does not bump epoch; a new connect() will
    void deps.probeStatus().then((probe) => {
      if (disposed || mine !== epoch) return;
      applyProbe(probe, true);
    });
  }

  function loadModel(name: string): void {
    if (disposed) return;
    clearTimer();
    const mine = ++epoch;
    const startedAt = deps.now();
    emit({
      phase: "loading",
      error: null,
      guardBlocked: false,
      loadStartedAt: startedAt,
      loadDeadline: startedAt + loadTimeoutMs(snap.status),
    });
    void deps.loadModel(name).then((result) => {
      if (disposed || mine !== epoch) return;
      switch (result.kind) {
        case "status":
          reduceStatus(result.status, true);
          return;
        case "guard-blocked":
          clearTimer();
          emit({
            phase: "needs-model",
            error: "Blocked by heim's cross-site guard",
            guardBlocked: true,
            loadStartedAt: null,
            loadDeadline: null,
          });
          return;
        case "conflict":
          clearTimer();
          emit({
            phase: "needs-model",
            error: "Server is locked or mid-generation",
            loadStartedAt: null,
            loadDeadline: null,
          });
          return;
        case "not-found":
          clearTimer();
          emit({ phase: "needs-model", error: "Unknown model", loadStartedAt: null, loadDeadline: null });
          return;
        case "unauthorized":
          clearTimer();
          emit({ phase: "needs-token", loadStartedAt: null, loadDeadline: null });
          return;
        case "network-error":
          clearTimer();
          emit({ phase: "disconnected", loadStartedAt: null, loadDeadline: null });
          schedule(connect, RETRY_MS);
          return;
        case "http-error":
          clearTimer();
          emit({
            phase: "error",
            error: `Load failed (HTTP ${result.code})`,
            loadStartedAt: null,
            loadDeadline: null,
          });
          return;
      }
    });
  }

  function retry(): void {
    connect();
  }

  function dispose(): void {
    disposed = true;
    clearTimer();
    listeners.clear();
  }

  return {
    subscribe(cb) {
      listeners.add(cb);
      cb(snap);
      return () => listeners.delete(cb);
    },
    getState: () => snap,
    connect,
    loadModel,
    retry,
    dispose,
  };
}

// --- Default deps wired to the shared HTTP client (used by the React app). ---

/**
 * Probe GET /api/status against an explicit base URL + token — the single status-probe contract.
 * Used by SettingsView's per-entry "test connection" (each backend, before it is the active one)
 * and, via {@link defaultProbeStatus}, the live connection state machine.
 */
export async function probeStatusAt(baseUrl: string, token: string | null): Promise<StatusProbe> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${baseUrl}/api/status`, { headers });
  } catch {
    return { kind: "network-error" };
  }
  if (res.status === 401) return { kind: "unauthorized" };
  if (!res.ok) return { kind: "http-error", code: res.status };
  const status = (await res.json()) as Status;
  return { kind: "status", status };
}

function defaultProbeStatus(): Promise<StatusProbe> {
  const { baseUrl, token } = apiConfig();
  return probeStatusAt(baseUrl, token);
}

async function defaultLoadModel(name: string): Promise<LoadResult> {
  const path = name.split("/").map(encodeURIComponent).join("/");
  let res: Response;
  try {
    res = await apiFetch(`/api/load/${path}`, { method: "POST" });
  } catch {
    return { kind: "network-error" };
  }
  if (res.status === 401) return { kind: "unauthorized" };
  if (res.status === 403) return { kind: "guard-blocked" };
  if (res.status === 409) return { kind: "conflict" };
  if (res.status === 404) return { kind: "not-found" };
  if (!res.ok) return { kind: "http-error", code: res.status };
  const status = (await res.json()) as Status;
  return { kind: "status", status };
}

/** Default dependency bundle backed by the shared HTTP client + real timers. */
export function defaultConnectionDeps(): ConnectionDeps {
  return {
    probeStatus: defaultProbeStatus,
    loadModel: defaultLoadModel,
    setTimer: (fn, ms) => setTimeout(fn, ms),
    clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    now: () => Date.now(),
  };
}
