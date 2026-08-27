// Browser-executed tool channel: the CLIENT half of WEBMCP.md 5b. The server streams a
// `page_action` frame mid-turn; the panel runs it in the target tab and POSTs the outcome to
// /api/chat/page-action, which unblocks the agent loop. Same shape as the `confirm` gate — emit,
// block, wait for the client — so this is a second consumer of a mechanism already load-bearing.
//
// THE SAFETY MODEL LIVES IN THIS FILE (WEBMCP.md 5b), so read it before adding an action:
//
//   1. TYPED ACTIONS ONLY. The wire carries an action NAME plus arguments, never JavaScript. Every
//      implementation is in HANDLERS below, fixed at extension-build time and reviewable. Nothing
//      off the wire is ever passed to eval/Function or injected as code: the `func` handed to
//      chrome.scripting.executeScript is always a literal function in this file, and `args` may
//      only ever be serializable data. An unknown name is refused, never executed.
//   2. HANDLERS is a Map, not an object literal, precisely so a wire name like "constructor" or
//      "__proto__" resolves to nothing instead of a prototype function.
//   3. THE CALLER OWNS THE TAB. `tabId` is a parameter, resolved by the panel from the tracked/
//      matched tab; `args` is never read for anything tab-shaped. A tab id from the model would
//      turn a page action into a way to reach any tab the browser has open.
//   4. FAILURE IS NEVER SUCCESS. Every path out of here is either {ok:true} or {ok:false, error};
//      a refused injection (no host grant, a restricted page) is a clean {ok:false}, not a throw.
//
// `page_read` is the only action so far. Navigation and the mutating actions (click/fill) are
// separate work; a mutating one must additionally ride the existing confirm gate (rule 2 of 5b).
//
// Reporting the outcome is NOT here: `reportPageAction` lives in the shared api client next to
// `confirmAction`, because both resolve a held agent loop over the same transport.

/** The outcome reported back to the server. Deliberately has no third "unknown" state. */
export type PageActionOutcome = { ok: true; result: unknown } | { ok: false; error: string };

/** One element the read returned, as the model will refer to it later. */
export interface PageElementRef {
  /** Opaque handle, valid for this read of this document (see REF SCHEME below). */
  ref: string;
  /** Accessible name — the words a person would use to point at it. */
  name: string;
}

/** A document-outline entry. */
export interface PageHeading extends PageElementRef {
  level: number;
}

/** A link. `href` is present only for http(s) targets (see readPage). */
export interface PageLink extends PageElementRef {
  href?: string;
}

/** A clickable control. */
export interface PageButton extends PageElementRef {
  tag: string;
  disabled?: boolean;
}

/** A form control the model may later fill. Never carries a password's value. */
export interface PageField extends PageElementRef {
  tag: string;
  type: string;
  value?: string;
  placeholder?: string;
  checked?: boolean;
  disabled?: boolean;
  required?: boolean;
  options?: string[];
}

/** What the caps cut, so a partial read is never mistaken for a small page. */
export interface PageReadTruncation {
  headings: number;
  links: number;
  buttons: number;
  fields: number;
  text: boolean;
}

/**
 * The structured page snapshot `page_read` returns.
 *
 * WHY THIS SHAPE. `lib/pageContext.ts` already pushes visible text into the user turn, so a
 * read that only returned more text would add nothing. What the model cannot get from text is
 * (a) STRUCTURE — the outline that says what this page is and how it is organized — and (b)
 * AFFORDANCES — what can be clicked or typed into, with the label a person would use for it.
 * So the result is a document outline plus three lists of addressable controls, not a blob.
 *
 * WHY GROUPED, not one flat element list. A page with 400 links must not crowd its 3 form
 * fields out of the budget; per-group caps keep each kind present, and `truncated` says which
 * group lost entries. Grouping also matches how the next actions will be typed: `page_click`
 * takes a button/link ref, `page_fill` takes a field ref, and a wrong-kind ref is then a
 * detectable error rather than a click on a heading.
 *
 * WHY `ref` IS AN OPAQUE ORDINAL, not a CSS selector. The ref is `e<i>`, where `i` is the index
 * into the fixed, document-order `querySelectorAll(ELEMENT_SELECTOR)` this read performed. A
 * later `page_click`/`page_fill` re-runs the IDENTICAL query and resolves the same index, then
 * checks the element it found still has the `name` (and kind) the read reported — so a page that
 * changed underneath fails loudly instead of clicking the wrong thing. Two properties fall out
 * that a selector cannot give: the model can only ever name an element THIS read returned (it
 * cannot synthesize a reach into the page), and the handle carries no page structure back to
 * the model. Indices are assigned over the UNFILTERED match list, so a menu merely showing or
 * hiding between read and click does not renumber anything; a DOM insertion does, which is what
 * the name check is there to catch.
 */
export interface PageReadResult {
  url: string;
  title: string;
  headings: PageHeading[];
  links: PageLink[];
  buttons: PageButton[];
  fields: PageField[];
  /** Whitespace-collapsed visible text, capped — the prose the structure hangs off. */
  text: string;
  truncated: PageReadTruncation;
}

// Per-group caps. Sized so a big application page still fits a small model's context: the outline
// and the controls are the point, the prose is the tail that gets cut first.
const MAX_HEADINGS = 100;
const MAX_LINKS = 150;
const MAX_BUTTONS = 100;
const MAX_FIELDS = 100;
const MAX_TEXT = 8000;
const MAX_NAME = 160;

/**
 * THE QUERY THAT DEFINES THE REF SPACE. A ref is the index of an element in
 * `document.querySelectorAll(ELEMENT_SELECTOR)`, so a later click/fill action must resolve its
 * ref against this exact string or it will address a different element than the read named.
 *
 * It lives at module scope and is PASSED IN to the injected function for precisely that reason:
 * an injected function cannot close over module scope, so the alternative is a copy inside each
 * action's injected body — two strings that silently drift into two ref spaces. One definition,
 * handed to whichever action needs it, makes that impossible rather than merely discouraged.
 */
const ELEMENT_SELECTOR =
  "h1,h2,h3,h4,h5,h6,[role='heading']," +
  "a[href],[role='link']," +
  "button,[role='button'],input[type='button'],input[type='submit'],input[type='reset'],summary," +
  "input,select,textarea,[contenteditable=''],[contenteditable='true']";

/**
 * Read the page's structure from the given tab.
 *
 * The injected function is a literal defined in this file and serialized by executeScript, so it
 * cannot import — everything it needs arrives as serializable args, all of them our own constants.
 */
async function readPage(tabId: number): Promise<PageActionOutcome> {
  let injected: chrome.scripting.InjectionResult<PageReadResult | null>[];
  try {
    injected = await chrome.scripting.executeScript({
      target: { tabId },
      func: collectStructure,
      args: [
        ELEMENT_SELECTOR,
        { headings: MAX_HEADINGS, links: MAX_LINKS, buttons: MAX_BUTTONS, fields: MAX_FIELDS },
        MAX_TEXT,
        MAX_NAME,
      ],
    });
  } catch {
    // Overwhelmingly a missing host grant for this origin, or a restricted page (chrome://, the
    // Web Store). Report it as the failure it is — the model can then say so instead of inventing
    // a page it never saw. See lib/hostAccess.ts for how a grant is obtained on a user gesture.
    return { ok: false, error: "no page access: stabbur cannot read this tab (grant access to the site, or open a normal web page)" };
  }
  const value = injected[0]?.result ?? null;
  if (!value) return { ok: false, error: "page read returned nothing (the tab may have navigated away)" };
  return { ok: true, result: value };
}

/**
 * Runs IN THE PAGE (isolated world). Self-contained by necessity: executeScript serializes this
 * function, so it can neither import nor close over module scope.
 */
function collectStructure(
  elementSelector: string,
  caps: { headings: number; links: number; buttons: number; fields: number },
  textLimit: number,
  nameLimit: number,
): PageReadResult | null {
  // Input types that are really buttons, so they are classified as clickables, not fields.
  const BUTTON_INPUT_TYPES = new Set(["button", "submit", "reset", "image"]);
  // Never reported, even as a length: a value here is a secret the page holds on the user's
  // behalf, and the model has no business with it.
  const SECRET_INPUT_TYPES = new Set(["password"]);

  function clip(s: string, limit: number): string {
    const t = s.replace(/\s+/g, " ").trim();
    return t.length > limit ? `${t.slice(0, limit)}...` : t;
  }

  // Rendered at all? Deliberately permissive (an element that is merely transparent or
  // visibility:hidden still counts): dropping a real control is worse than listing a faint one.
  function rendered(el: Element): boolean {
    const withCheck = el as Element & { checkVisibility?: () => boolean };
    if (typeof withCheck.checkVisibility === "function") return withCheck.checkVisibility();
    return el.getClientRects().length > 0;
  }

  // The words a person would use to point at this element, in the order a screen reader would
  // resolve them. Not a full accname implementation — it is the practical subset.
  function accessibleName(el: Element): string {
    const aria = el.getAttribute("aria-label");
    if (aria?.trim()) return clip(aria, nameLimit);
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const parts = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent ?? "")
        .filter(Boolean);
      if (parts.length) return clip(parts.join(" "), nameLimit);
    }
    const labelled = el as Element & { labels?: NodeListOf<HTMLLabelElement> };
    if (labelled.labels?.length) {
      const text = Array.from(labelled.labels)
        .map((l) => l.textContent ?? "")
        .join(" ");
      if (text.trim()) return clip(text, nameLimit);
    }
    const alt = el.getAttribute("alt");
    if (alt?.trim()) return clip(alt, nameLimit);
    // input[type=submit|button|reset] carries its label in `value`, not in text content.
    if (el.tagName === "INPUT" && BUTTON_INPUT_TYPES.has((el as HTMLInputElement).type)) {
      const v = (el as HTMLInputElement).value;
      if (v.trim()) return clip(v, nameLimit);
    }
    const text = (el as HTMLElement).innerText ?? el.textContent ?? "";
    if (text.trim()) return clip(text, nameLimit);
    const placeholder = el.getAttribute("placeholder");
    if (placeholder?.trim()) return clip(placeholder, nameLimit);
    const title = el.getAttribute("title");
    if (title?.trim()) return clip(title, nameLimit);
    return "";
  }

  function headingLevel(el: Element): number {
    const m = /^H([1-6])$/.exec(el.tagName);
    if (m) return Number(m[1]);
    const aria = Number(el.getAttribute("aria-level"));
    return Number.isInteger(aria) && aria >= 1 && aria <= 6 ? aria : 2;
  }

  function isHeading(el: Element): boolean {
    return /^H[1-6]$/.test(el.tagName) || el.getAttribute("role") === "heading";
  }

  function isLink(el: Element): boolean {
    return (el.tagName === "A" && el.hasAttribute("href")) || el.getAttribute("role") === "link";
  }

  function isButton(el: Element): boolean {
    if (el.tagName === "BUTTON" || el.tagName === "SUMMARY") return true;
    if (el.getAttribute("role") === "button") return true;
    return el.tagName === "INPUT" && BUTTON_INPUT_TYPES.has((el as HTMLInputElement).type);
  }

  function isField(el: Element): boolean {
    if (el.tagName === "SELECT" || el.tagName === "TEXTAREA") return true;
    if (el.tagName === "INPUT") return !BUTTON_INPUT_TYPES.has((el as HTMLInputElement).type);
    const ce = el.getAttribute("contenteditable");
    return ce === "" || ce === "true";
  }

  let body: HTMLElement | null;
  try {
    body = document.body;
  } catch {
    return null;
  }
  if (!body) return null;

  const headings: PageHeading[] = [];
  const links: PageLink[] = [];
  const buttons: PageButton[] = [];
  const fields: PageField[] = [];
  const dropped = { headings: 0, links: 0, buttons: 0, fields: 0 };

  const all = document.querySelectorAll(elementSelector);
  all.forEach((el, index) => {
    // The ref is the index into the UNFILTERED list, so show/hide churn between this read and a
    // later click does not renumber anything. Assign it before any filtering.
    const ref = `e${index}`;
    if (!rendered(el)) return;
    const name = accessibleName(el);
    const tag = el.tagName.toLowerCase();

    // Order matters: a heading is checked first (a heading is never clickable on its own), then
    // link, then button, then field — mirroring how the next actions will be typed.
    if (isHeading(el)) {
      if (!name) return;
      if (headings.length >= caps.headings) dropped.headings += 1;
      else headings.push({ ref, name, level: headingLevel(el) });
      return;
    }
    if (isLink(el)) {
      // A link with neither a name nor a usable href is noise (icon-only wrappers, spacers).
      const raw = (el as HTMLAnchorElement).href ?? "";
      // ONLY http(s). A javascript:/data:/blob: href is not a destination worth handing back —
      // it would read as somewhere the model could ask to navigate. The link stays listed and
      // clickable; only the destination is withheld.
      const href = /^https?:\/\//i.test(raw) ? raw : "";
      if (!name && !href) return;
      if (links.length >= caps.links) dropped.links += 1;
      else links.push(href ? { ref, name, href } : { ref, name });
      return;
    }
    if (isButton(el)) {
      if (!name) return;
      const disabled = (el as HTMLButtonElement).disabled === true;
      if (buttons.length >= caps.buttons) dropped.buttons += 1;
      else buttons.push(disabled ? { ref, name, tag, disabled } : { ref, name, tag });
      return;
    }
    if (!isField(el)) return;
    if (fields.length >= caps.fields) {
      dropped.fields += 1;
      return;
    }
    const input = el as HTMLInputElement;
    const type = el.tagName === "INPUT" ? input.type || "text" : tag === "select" ? "select" : tag;
    const field: PageField = { ref, name, tag, type };
    const toggle = type === "checkbox" || type === "radio";
    // A toggle's `value` is the HTML default "on" unless the page set one — noise the model would
    // have to learn to ignore. Its real state is `checked`, reported below.
    if (!SECRET_INPUT_TYPES.has(type) && !toggle) {
      const value = typeof input.value === "string" ? input.value : (el as HTMLElement).innerText;
      if (value?.trim()) field.value = clip(value, nameLimit);
    }
    const placeholder = el.getAttribute("placeholder");
    if (placeholder?.trim()) field.placeholder = clip(placeholder, nameLimit);
    if (toggle) field.checked = input.checked === true;
    if (input.disabled === true) field.disabled = true;
    if (input.required === true) field.required = true;
    if (el.tagName === "SELECT") {
      field.options = Array.from((el as unknown as HTMLSelectElement).options)
        .slice(0, 40)
        .map((o) => clip(o.label || o.value, nameLimit));
    }
    fields.push(field);
  });

  // Same collapse as lib/pageContext.ts: runs of spaces to one space, runs of blank lines to one
  // newline — keeps list structure while spending the budget on content, not whitespace.
  const rawText = body.innerText ?? "";
  const collapsed = rawText
    .replace(/[^\S\n]+/g, " ")
    .replace(/\s*\n\s*/g, "\n")
    .trim();

  return {
    url: location.href,
    title: document.title,
    headings,
    links,
    buttons,
    fields,
    text: collapsed.slice(0, textLimit),
    truncated: { ...dropped, text: collapsed.length > textLimit },
  };
}

/**
 * Every action this build can perform, by wire name. A Map (not an object literal) so a name off
 * the wire can only ever hit an entry put here on purpose — "constructor", "__proto__" and friends
 * resolve to undefined and are refused like any other unknown name.
 */
const HANDLERS = new Map<string, (tabId: number) => Promise<PageActionOutcome>>([["page_read", readPage]]);

/** The action names this build implements (for UI copy and tests). */
export function knownPageActions(): string[] {
  return [...HANDLERS.keys()];
}

/**
 * Execute one page action in `tabId` and return its outcome.
 *
 * `tabId` is resolved by the caller from the tracked/matched tab — never from the message (5b
 * rule 3). An unknown action is refused here, before any injection happens.
 *
 * Args:
 *   tabId: The tab to act in.
 *   action: The action name off the wire; untrusted.
 *
 * Returns:
 *   The outcome to POST back. Never throws.
 */
export async function executePageAction(tabId: number, action: string): Promise<PageActionOutcome> {
  const handler = HANDLERS.get(action);
  // Refused, not executed: the set of things a model can do in a tab is fixed at build time.
  if (!handler) return { ok: false, error: `unknown page action: ${action}` };
  try {
    return await handler(tabId);
  } catch (err) {
    // A handler is not allowed to fail as anything other than {ok:false} — a throw escaping here
    // would leave the agent loop blocked until its fail-safe timeout.
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}
