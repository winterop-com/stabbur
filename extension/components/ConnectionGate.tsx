import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Plug, RefreshCw, Settings } from "lucide-react";
import type { ConnectionSnapshot } from "../lib/connection";
import { isDhis2Flavor } from "../lib/flavor";
import { CopyLine } from "./CopyLine";

// Where the "in a DHIS2 project" hint links (the dhis2 flavor only). Absolute so it works
// from a chrome-extension:// page; opens in a new tab.
const DHIS2_PROJECT_DOCS_URL = "https://github.com/winterop-com/stabbur/blob/main/docs/guides/dhis2-project.md";

interface ConnectionGateProps {
  snapshot: ConnectionSnapshot;
  baseUrl: string;
  extensionId: string;
  onRetry: () => void;
  onLoadModel: (name: string) => void;
  onSaveToken: (token: string) => void;
  onOpenSettings: () => void;
}

function portOf(baseUrl: string): string {
  try {
    const u = new URL(baseUrl);
    return u.port || (u.protocol === "https:" ? "443" : "80");
  } catch {
    return "8000";
  }
}

function CorsHint({ extensionId }: { extensionId: string }) {
  return (
    <div className="space-y-2 text-sm">
      <p className="text-[var(--muted-foreground)]">
        stabbur blocked a request from this extension (cross-site guard). Add this line to your stabbur config, then
        restart <code className="font-mono">stabbur serve</code>:
      </p>
      <CopyLine text={`cors_origins = ["chrome-extension://${extensionId}"]`} />
    </div>
  );
}

/** Full-panel connection lifecycle UI: renders per-phase guidance and actions. */
export function ConnectionGate({
  snapshot,
  baseUrl,
  extensionId,
  onRetry,
  onLoadModel,
  onSaveToken,
  onOpenSettings,
}: ConnectionGateProps) {
  const [now, setNow] = useState(() => Date.now());
  const [token, setToken] = useState("");

  // Tick for the loading elapsed/timeout display.
  useEffect(() => {
    if (snapshot.phase !== "loading") return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [snapshot.phase]);

  const settingsLink = (
    <button
      type="button"
      onClick={onOpenSettings}
      className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
    >
      <Settings className="h-3.5 w-3.5" />
      Settings
    </button>
  );

  function body() {
    switch (snapshot.phase) {
      case "connecting":
        return (
          <Centered>
            <Loader2 className="h-6 w-6 animate-spin text-[var(--muted-foreground)]" />
            <p className="text-sm text-[var(--muted-foreground)]">Connecting to stabbur...</p>
          </Centered>
        );

      case "disconnected":
        return (
          <Centered>
            <Plug className="h-6 w-6 text-[var(--muted-foreground)]" />
            <p className="text-sm">
              stabbur is not reachable at <code className="font-mono">{baseUrl}</code> - is{" "}
              <code className="font-mono">stabbur serve --port {portOf(baseUrl)}</code> running?
            </p>
            {isDhis2Flavor() ? (
              <p className="text-xs text-[var(--muted-foreground)]">
                Run <code className="font-mono">stabbur serve</code> in your DHIS2 project (a{" "}
                <code className="font-mono">stabbur.toml</code> with a <code className="font-mono">[assistant]</code>{" "}
                block).{" "}
                <a
                  href={DHIS2_PROJECT_DOCS_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="underline hover:text-[var(--foreground)]"
                >
                  DHIS2 project guide
                </a>
              </p>
            ) : null}
            <p className="text-xs text-[var(--muted-foreground)]">Retrying automatically every 3s.</p>
            <div className="flex items-center gap-3">
              <RetryButton onRetry={onRetry} />
              {settingsLink}
            </div>
          </Centered>
        );

      case "needs-token":
        return (
          <Centered>
            <p className="text-sm">This stabbur server requires an access token.</p>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Bearer token"
              className="w-full rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-sm"
            />
            <button
              type="button"
              onClick={() => onSaveToken(token.trim())}
              disabled={!token.trim()}
              className="rounded bg-[var(--primary)] px-3 py-1.5 text-sm text-[var(--primary-foreground)] disabled:opacity-50"
            >
              Save token and connect
            </button>
            {settingsLink}
          </Centered>
        );

      case "needs-model": {
        const projectModel = snapshot.status?.project_model ?? null;
        return (
          <Centered>
            {snapshot.guardBlocked ? (
              <CorsHint extensionId={extensionId} />
            ) : projectModel ? (
              <>
                <p className="text-sm">
                  No model is loaded. This project is bound to{" "}
                  <code className="font-mono">{projectModel}</code>.
                </p>
                <button
                  type="button"
                  onClick={() => onLoadModel(projectModel)}
                  className="rounded bg-[var(--primary)] px-3 py-1.5 text-sm text-[var(--primary-foreground)]"
                >
                  Load {projectModel}
                </button>
              </>
            ) : (
              <p className="text-sm text-[var(--muted-foreground)]">
                No model is loaded and this project has no bound model. Load one from the stabbur web UI, then
                retry.
              </p>
            )}
            {snapshot.error && !snapshot.guardBlocked ? (
              <p className="text-xs text-[var(--destructive)]">{snapshot.error}</p>
            ) : null}
            <div className="flex items-center gap-3">
              <RetryButton onRetry={onRetry} />
              {settingsLink}
            </div>
          </Centered>
        );
      }

      case "loading": {
        const started = snapshot.loadStartedAt ?? now;
        const elapsed = Math.max(0, Math.round((now - started) / 1000));
        const timeout = snapshot.status?.runtime_load_timeout ?? null;
        const pct =
          snapshot.loadDeadline && snapshot.loadStartedAt
            ? Math.min(100, ((now - snapshot.loadStartedAt) / (snapshot.loadDeadline - snapshot.loadStartedAt)) * 100)
            : null;
        return (
          <Centered>
            <Loader2 className="h-6 w-6 animate-spin text-[var(--muted-foreground)]" />
            <p className="text-sm">Loading model...</p>
            <p className="text-xs text-[var(--muted-foreground)]">
              {elapsed}s elapsed{timeout != null ? ` (timeout ${timeout}s)` : ""}
            </p>
            {pct != null ? (
              <div className="h-1.5 w-full overflow-hidden rounded bg-[var(--muted)]">
                <div className="h-full bg-[var(--primary)] transition-all" style={{ width: `${pct}%` }} />
              </div>
            ) : null}
          </Centered>
        );
      }

      case "error":
        return (
          <Centered>
            <AlertTriangle className="h-6 w-6 text-[var(--destructive)]" />
            <p className="text-sm">Something went wrong.</p>
            {snapshot.error ? (
              <pre className="max-h-40 w-full overflow-auto rounded border border-[var(--border)] bg-[var(--muted)] p-2 text-xs whitespace-pre-wrap">
                {snapshot.error}
              </pre>
            ) : null}
            <div className="flex items-center gap-3">
              <RetryButton onRetry={onRetry} />
              {settingsLink}
            </div>
          </Centered>
        );

      case "ready":
        return null;
    }
  }

  return <div className="flex h-full items-center justify-center p-4">{body()}</div>;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex w-full max-w-xs flex-col items-center gap-3 text-center">{children}</div>;
}

function RetryButton({ onRetry }: { onRetry: () => void }) {
  return (
    <button
      type="button"
      onClick={onRetry}
      className="inline-flex items-center gap-1.5 rounded border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--accent)]"
    >
      <RefreshCw className="h-3.5 w-3.5" />
      Retry
    </button>
  );
}
