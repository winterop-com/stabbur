// Lazy mermaid loader + render helper, shared by the live chat renderer
// (MermaidDiagram) and PDF export. mermaid is heavy (~renders to its own chunk),
// so it is only imported the first time a diagram is actually encountered.

type MermaidModule = typeof import("mermaid")["default"];

let loader: Promise<MermaidModule> | null = null;

/** Import mermaid once; subsequent calls reuse the same promise/module. */
export function loadMermaid(): Promise<MermaidModule> {
  if (!loader) loader = import("mermaid").then((m) => m.default);
  return loader;
}

// Unique, deterministic-per-session ids for mermaid's internal render target.
let counter = 0;

/**
 * Render mermaid `code` to an SVG string. `dark` selects the built-in dark theme
 * (else the light "default"). Throws on invalid diagram syntax — callers decide
 * whether to fall back to showing the source.
 */
export async function renderMermaid(code: string, dark: boolean): Promise<string> {
  const mermaid = await loadMermaid();
  mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default", securityLevel: "strict" });
  const id = `heim-mermaid-${counter++}`;
  const { svg } = await mermaid.render(id, code);
  return svg;
}
