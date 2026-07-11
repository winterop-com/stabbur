// The real public sites the prompt catalog is verified against. Each entry is
// captured once (url/title/page-text and, for selection-based prompts, the text
// of the first substantial element matching `selectionSelectors`).

export interface Site {
  /** Stable key used by prompts and the results file. */
  key: string;
  /** Human label for the docs. */
  label: string;
  url: string;
  /** CSS selectors tried in order; the first element with >= 60 chars of visible
   *  text becomes the "selection" (mirrors a user selecting a paragraph/block). */
  selectionSelectors: string[];
  /** Consent/cookie buttons to click before capture (best-effort). */
  consentSelectors?: string[];
}

export const SITES: Site[] = [
  {
    key: "hn",
    label: "Hacker News front page",
    url: "https://news.ycombinator.com/",
    selectionSelectors: [".titleline"],
  },
  {
    key: "wikipedia",
    label: "Wikipedia: SQLite",
    url: "https://en.wikipedia.org/wiki/SQLite",
    selectionSelectors: ["#mw-content-text p", ".mw-parser-output > p"],
  },
  {
    key: "github",
    label: "GitHub: fastapi/fastapi",
    url: "https://github.com/fastapi/fastapi",
    selectionSelectors: ["article.markdown-body > p", ".markdown-body p"],
  },
  {
    key: "mdn",
    label: "MDN: Array.prototype.map()",
    url: "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/map",
    selectionSelectors: [".section-content > p", "main p"],
    consentSelectors: ["button.glean-search-close", ".gleanpanel button"],
  },
  {
    key: "arxiv",
    label: "arXiv: Attention Is All You Need (1706.03762)",
    url: "https://arxiv.org/abs/1706.03762",
    selectionSelectors: ["blockquote.abstract"],
  },
  {
    key: "pydocs",
    label: "Python docs: json module",
    url: "https://docs.python.org/3/library/json.html",
    selectionSelectors: ["#module-json > p", "section > p"],
  },
  {
    key: "stackoverflow",
    label: "Stack Overflow: canonical question",
    url: "https://stackoverflow.com/questions/292357/what-is-the-difference-between-git-pull-and-git-fetch",
    selectionSelectors: [".s-prose.js-post-body", ".s-prose"],
    consentSelectors: ["button#onetrust-accept-btn-handler", ".js-accept-cookies"],
  },
  {
    key: "dhis2",
    label: "DHIS2 play demo (dev-2-42) landing",
    url: "https://play.im.dhis2.org/dev-2-42",
    selectionSelectors: ["form", "main"],
  },
];

export function siteByKey(key: string): Site {
  const s = SITES.find((x) => x.key === key);
  if (!s) throw new Error(`unknown site: ${key}`);
  return s;
}
