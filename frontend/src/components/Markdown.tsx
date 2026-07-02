import { memo, useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { common } from "lowlight";

import { CodeBlock } from "@/components/CodeBlock";
import { MermaidDiagram } from "@/components/MermaidDiagram";

// Limit highlight.js to the ~37 common grammars (keeps the bundle lean vs the
// full ~190-language set rehype-highlight would otherwise pull in).
const rehypePlugins: Options["rehypePlugins"] = [[rehypeHighlight, { languages: common }]];

/** Concatenate all text within a React node (unwraps rehype-highlight spans). */
function nodeText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (typeof node === "object" && "props" in node) {
    return nodeText((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

/** The fenced language (from the child <code>'s `language-*` class) + raw text. */
function fenceInfo(children: ReactNode): { lang: string | null; text: string } {
  const code = Array.isArray(children) ? children[0] : children;
  const className =
    typeof code === "object" && code && "props" in code
      ? (code as { props?: { className?: string } }).props?.className ?? ""
      : "";
  return { lang: /language-(\w+)/.exec(className)?.[1] ?? null, text: nodeText(children) };
}

const baseComponents: Components = {
  // Open links in a new tab.
  a: ({ children, ...props }) => (
    <a {...props} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
};

/**
 * Render assistant content as GitHub-flavored Markdown with highlighted code.
 * ```mermaid fences render as diagrams; all other fences use the copy-enabled
 * code block. `streaming` defers mermaid rendering until the source is complete.
 */
export const Markdown = memo(function Markdown({ content, streaming }: { content: string; streaming?: boolean }) {
  const components = useMemo<Components>(
    () => ({
      ...baseComponents,
      // <pre> wraps a fenced block; route mermaid to a diagram, else a code block.
      pre: ({ children }) => {
        const { lang, text } = fenceInfo(children);
        if (lang === "mermaid") return <MermaidDiagram code={text} streaming={streaming} />;
        return <CodeBlock>{children}</CodeBlock>;
      },
    }),
    [streaming],
  );

  return (
    <div className="prose-chat text-[0.95rem] text-foreground">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
