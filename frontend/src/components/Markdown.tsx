import { memo } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { common } from "lowlight";

import { CodeBlock } from "@/components/CodeBlock";

// Limit highlight.js to the ~37 common grammars (keeps the bundle lean vs the
// full ~190-language set rehype-highlight would otherwise pull in).
const rehypePlugins: Options["rehypePlugins"] = [[rehypeHighlight, { languages: common }]];

const components: Components = {
  // <pre> wraps fenced code; render it with our copy-enabled CodeBlock.
  pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
  // Open links in a new tab.
  a: ({ children, ...props }) => (
    <a {...props} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
};

/** Render assistant content as GitHub-flavored Markdown with highlighted code. */
export const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <div className="prose-chat text-[0.95rem] text-foreground">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
