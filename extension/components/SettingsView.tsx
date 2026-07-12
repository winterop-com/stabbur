import { useState } from "react";
import { ArrowLeft, Loader2, Plus, Trash2 } from "lucide-react";
import { normalizeBaseUrl, validateBaseUrl, type Backend, type Settings } from "../lib/settings";
import { probeStatusAt } from "../lib/connection";
import { CopyLine } from "./CopyLine";

interface SettingsViewProps {
  settings: Settings;
  extensionId: string;
  onSave: (patch: Partial<Settings>) => void;
  onClose: () => void;
}

type TestState =
  | { kind: "idle" }
  | { kind: "testing" }
  | { kind: "ok"; detail: string }
  | { kind: "fail"; detail: string };

function newBackend(): Backend {
  return { id: crypto.randomUUID(), name: "", baseUrl: "http://127.0.0.1:8000", token: "" };
}

/** Editable list of named backends (each its own base URL + token, one active), plus
 *  the page-context toggles, the cors_origins hint, and a per-entry "test connection". */
export function SettingsView({ settings, extensionId, onSave, onClose }: SettingsViewProps) {
  const [backends, setBackends] = useState<Backend[]>(() => settings.backends.map((b) => ({ ...b })));
  const [activeId, setActiveId] = useState(settings.activeBackendId);
  const [pageContextEnabled, setPageContextEnabled] = useState(settings.pageContextEnabled);
  const [pageTextEnabled, setPageTextEnabled] = useState(settings.pageTextEnabled);
  // Per-backend URL errors and test state, keyed by backend id.
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tests, setTests] = useState<Record<string, TestState>>({});

  function patchBackend(id: string, patch: Partial<Backend>): void {
    setBackends((prev) => prev.map((b) => (b.id === id ? { ...b, ...patch } : b)));
    if (errors[id]) setErrors((prev) => ({ ...prev, [id]: "" }));
  }

  function addBackend(): void {
    setBackends((prev) => [...prev, newBackend()]);
  }

  function removeBackend(id: string): void {
    setBackends((prev) => {
      const next = prev.filter((b) => b.id !== id);
      if (id === activeId && next.length > 0) setActiveId(next[0].id);
      return next;
    });
  }

  function save(): void {
    const nextErrors: Record<string, string> = {};
    for (const b of backends) {
      const err = validateBaseUrl(b.baseUrl);
      if (err) nextErrors[b.id] = err;
    }
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }
    setErrors({});
    // Persist the canonical base URL for each entry: a pasted trailing slash would make
    // apiFetch build "//api/..." paths that 404 on every endpoint.
    const normalized = backends.map((b) => ({
      ...b,
      name: b.name.trim() || b.baseUrl,
      baseUrl: normalizeBaseUrl(b.baseUrl),
      token: b.token.trim(),
    }));
    const activeBackendId = normalized.some((b) => b.id === activeId) ? activeId : (normalized[0]?.id ?? activeId);
    onSave({ backends: normalized, activeBackendId, pageContextEnabled, pageTextEnabled });
  }

  async function testConnection(b: Backend): Promise<void> {
    const err = validateBaseUrl(b.baseUrl);
    if (err) {
      setErrors((prev) => ({ ...prev, [b.id]: err }));
      setTests((prev) => ({ ...prev, [b.id]: { kind: "idle" } }));
      return;
    }
    setErrors((prev) => ({ ...prev, [b.id]: "" }));
    setTests((prev) => ({ ...prev, [b.id]: { kind: "testing" } }));
    const probe = await probeStatusAt(normalizeBaseUrl(b.baseUrl), b.token.trim() || null);
    const result: TestState =
      probe.kind === "status"
        ? {
            kind: "ok",
            detail: `state: ${probe.status.state}${probe.status.model ? `, model: ${probe.status.model}` : ""}`,
          }
        : probe.kind === "unauthorized"
          ? { kind: "fail", detail: "401 - a token is required" }
          : probe.kind === "http-error"
            ? { kind: "fail", detail: `HTTP ${probe.code}` }
            : { kind: "fail", detail: "not reachable" };
    setTests((prev) => ({ ...prev, [b.id]: result }));
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-2 border-b border-[var(--border)] px-3 py-2">
        <button
          type="button"
          onClick={onClose}
          aria-label="Back"
          className="rounded p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <h1 className="text-sm font-semibold">Settings</h1>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto p-3">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-[var(--muted-foreground)]">Backends</span>
            <button
              type="button"
              onClick={addBackend}
              className="inline-flex items-center gap-1 rounded border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--accent)]"
            >
              <Plus className="h-3 w-3" /> Add backend
            </button>
          </div>

          {backends.map((b) => {
            const test = tests[b.id] ?? { kind: "idle" };
            const err = errors[b.id];
            return (
              <div
                key={b.id}
                data-testid="backend-entry"
                className="space-y-2 rounded border border-[var(--border)] p-2"
              >
                <div className="flex items-center gap-2">
                  <label className="inline-flex items-center gap-1.5 text-xs">
                    <input
                      type="radio"
                      name="active-backend"
                      checked={activeId === b.id}
                      onChange={() => setActiveId(b.id)}
                      className="h-3.5 w-3.5"
                    />
                    Active
                  </label>
                  <input
                    type="text"
                    value={b.name}
                    onChange={(e) => patchBackend(b.id, { name: e.target.value })}
                    placeholder="Name (e.g. generic)"
                    className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => removeBackend(b.id)}
                    disabled={backends.length <= 1}
                    aria-label="Remove backend"
                    className="rounded p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--destructive)] disabled:opacity-40"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>

                <input
                  type="text"
                  value={b.baseUrl}
                  onChange={(e) => patchBackend(b.id, { baseUrl: e.target.value })}
                  placeholder="http://127.0.0.1:8000"
                  className="w-full rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-sm"
                />
                {err ? <span className="block text-xs text-[var(--destructive)]">{err}</span> : null}

                <input
                  type="password"
                  value={b.token}
                  onChange={(e) => patchBackend(b.id, { token: e.target.value })}
                  placeholder="Access token (optional)"
                  className="w-full rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-sm"
                />

                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => void testConnection(b)}
                    className="inline-flex items-center gap-1.5 rounded border border-[var(--border)] px-2.5 py-1 text-xs hover:bg-[var(--accent)]"
                  >
                    {test.kind === "testing" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                    Test connection
                  </button>
                  {test.kind === "ok" ? (
                    <span className="text-xs text-emerald-600">connected ({test.detail})</span>
                  ) : null}
                  {test.kind === "fail" ? (
                    <span className="text-xs text-[var(--destructive)]">{test.detail}</span>
                  ) : null}
                </div>
              </div>
            );
          })}
          <span className="block text-[11px] text-[var(--muted-foreground)]">
            Loopback (127.0.0.1/localhost) may use http; a remote host requires https.
          </span>
        </div>

        <label className="flex items-center justify-between gap-2">
          <span className="text-sm">Send page context</span>
          <input
            type="checkbox"
            checked={pageContextEnabled}
            onChange={(e) => setPageContextEnabled(e.target.checked)}
            className="h-4 w-4"
          />
        </label>

        <label className="flex items-center justify-between gap-2">
          <span className="text-sm">
            Include full page text
            <span className="block text-[11px] text-[var(--muted-foreground)]">
              Attach the page's visible text (up to 8000 chars). Requires page context.
            </span>
          </span>
          <input
            type="checkbox"
            checked={pageTextEnabled}
            disabled={!pageContextEnabled}
            onChange={(e) => setPageTextEnabled(e.target.checked)}
            className="h-4 w-4 disabled:opacity-40"
          />
        </label>

        <div className="space-y-1.5">
          <span className="text-xs font-medium text-[var(--muted-foreground)]">Extension ID</span>
          <CopyLine text={extensionId} />
          <p className="text-xs text-[var(--muted-foreground)]">
            Allow this extension in your heim config (add to <code className="font-mono">cors_origins</code>):
          </p>
          <CopyLine text={`cors_origins = ["chrome-extension://${extensionId}"]`} />
        </div>
      </div>

      <footer className="border-t border-[var(--border)] p-3">
        <button
          type="button"
          onClick={save}
          className="w-full rounded bg-[var(--primary)] px-3 py-2 text-sm text-[var(--primary-foreground)]"
        >
          Save
        </button>
      </footer>
    </div>
  );
}
