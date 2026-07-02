import { useEffect, useState } from "react";
import { Check, Code2, Copy, Image } from "lucide-react";

import { renderMermaid } from "@/lib/mermaid";
import { cn } from "@/lib/utils";

/** Track the app's dark mode by observing `class="dark"` on <html>. */
function useDarkMode(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const root = document.documentElement;
    const obs = new MutationObserver(() => setDark(root.classList.contains("dark")));
    obs.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

/**
 * Render a ```mermaid fenced block as a diagram (lazy-loaded mermaid.js),
 * theme-aware and re-rendered on theme change. A toggle flips between the
 * diagram and its source; invalid syntax falls back to the source with a note.
 * Not rendered while the message is still streaming (the source is incomplete).
 */
export function MermaidDiagram({ code, streaming }: { code: string; streaming?: boolean }) {
  const dark = useDarkMode();
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    // Don't try to render a half-streamed diagram — wait for the complete source.
    if (streaming) return;
    let cancelled = false;
    setFailed(false);
    renderMermaid(code, dark).then(
      (out) => !cancelled && setSvg(out),
      () => {
        if (cancelled) return;
        setFailed(true);
        setSvg(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [code, dark, streaming]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable — ignore
    }
  };

  // While streaming, or before the first render resolves, show the source so
  // there's never a blank gap.
  const asSource = streaming || failed || showSource || svg === null;

  return (
    <div className="group/mermaid relative my-3 rounded-lg border border-border/60 bg-card/40">
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1 opacity-0 transition-opacity group-hover/mermaid:opacity-100">
        {!failed && !streaming && (
          <button
            type="button"
            onClick={() => setShowSource((s) => !s)}
            className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-card/80 px-2 py-1 text-xs text-muted-foreground backdrop-blur hover:text-foreground"
            aria-label={showSource ? "Show diagram" : "Show source"}
          >
            {showSource ? <Image className="h-3.5 w-3.5" /> : <Code2 className="h-3.5 w-3.5" />}
            {showSource ? "Diagram" : "Source"}
          </button>
        )}
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-card/80 px-2 py-1 text-xs text-muted-foreground backdrop-blur hover:text-foreground"
          aria-label="Copy diagram source"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      {failed && (
        <div className="px-3 pt-2 text-xs text-muted-foreground">Could not render this mermaid diagram; showing source.</div>
      )}

      {asSource ? (
        <pre className="overflow-x-auto rounded-lg p-4 text-[0.85em] leading-relaxed">
          <code>{code}</code>
        </pre>
      ) : (
        <div
          className={cn("mermaid-diagram flex justify-center overflow-x-auto p-4")}
          // eslint-disable-next-line react/no-danger -- SVG is produced by mermaid with securityLevel:"strict"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
    </div>
  );
}
