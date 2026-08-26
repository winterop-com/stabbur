// Two things in lib/title are worth pinning, and they are worth it for opposite reasons.
//
// `sanitizeTitle` is a pure function over adversarial input — every model answers "name this chat"
// in a different shape, and the ways it can be wrong (a refusal accepted as a name, a title cut
// mid-word, the word "Title" as the title) are all silent. A browser click-through proves it
// against whichever model happened to be loaded that afternoon; this proves it against the dozen
// shapes at once.
//
// `applyModelTitle` is the rule the whole feature is subordinate to: a name the user typed is never
// replaced by one the model produced. It is a background request landing seconds late, so getting
// it wrong is not a visible bug — it is a chat quietly renaming itself out from under someone.
//
// Nothing here touches the network. The request path is deliberately not mocked: what it does is
// send an HTTP call and read `choices[0].message.content` out of the answer, which a fake server
// would only re-assert. What matters about a *real* one — that thinking-off is set, that the
// loaded model is the one named — is verified against a live heim, not against a stub of one.

import { describe, expect, it } from "vitest";

import { DEFAULT_SETTINGS } from "@/lib/store";
import { applyModelTitle, sanitizeTitle } from "@/lib/title";
import type { Conversation, TitleSource } from "@/lib/types";

function conversation(title: string, titledBy: TitleSource): Conversation {
  return {
    id: "c1",
    title,
    titledBy,
    settings: { ...DEFAULT_SETTINGS },
    createdAt: 1000,
    updatedAt: 1000,
    messages: [],
  };
}

describe("sanitizeTitle", () => {
  it("keeps a title that arrived the way it was asked for", () => {
    expect(sanitizeTitle("Heim sidebar screenshots")).toBe("Heim sidebar screenshots");
  });

  it("strips the wrapping models put around an answer", () => {
    expect(sanitizeTitle('"Quarterly revenue review"')).toBe("Quarterly revenue review");
    expect(sanitizeTitle("“Quarterly revenue review”")).toBe("Quarterly revenue review");
    expect(sanitizeTitle("`Quarterly revenue review`")).toBe("Quarterly revenue review");
    expect(sanitizeTitle("**Quarterly revenue review**")).toBe("Quarterly revenue review");
    expect(sanitizeTitle("Title: Quarterly revenue review")).toBe("Quarterly revenue review");
    expect(sanitizeTitle("Chat title - Quarterly revenue review")).toBe("Quarterly revenue review");
    expect(sanitizeTitle("Quarterly revenue review.")).toBe("Quarterly revenue review");
    expect(sanitizeTitle("  \n Quarterly revenue review \n ")).toBe("Quarterly revenue review");
  });

  it("keeps an apostrophe, which is not a quote the model forgot to close", () => {
    expect(sanitizeTitle("Morten's travel plans")).toBe("Morten's travel plans");
  });

  it("keeps a question mark, because a title can honestly be a question", () => {
    expect(sanitizeTitle("Why is the build failing?")).toBe("Why is the build failing?");
  });

  it("takes the answer and drops the explanation that follows it", () => {
    expect(sanitizeTitle("Sourdough starter troubleshooting\n\nThis captures the main topic.")).toBe(
      "Sourdough starter troubleshooting",
    );
  });

  it("cuts an over-long answer at a word boundary rather than mid-word", () => {
    const source = "Analysing the screenshots of the sidebar and the photographs taken outdoors last weekend";
    const title = sanitizeTitle(source);
    expect(title).toBe("Analysing the screenshots of the sidebar and…");
    expect(title!.length).toBeLessThanOrEqual(49); // 48 characters plus the ellipsis
    // The complaint that started all of this: a name must not end in a severed word. The cut has to
    // land on a space in the original, never inside "sidebar".
    const stem = title!.slice(0, -1);
    expect(source.startsWith(stem)).toBe(true);
    expect(source[stem.length]).toBe(" ");
  });

  it("refuses a refusal", () => {
    expect(sanitizeTitle("I can't name this conversation.")).toBeNull();
    expect(sanitizeTitle("I'm sorry, but I need more context.")).toBeNull();
    expect(sanitizeTitle("As an AI, I do not have opinions")).toBeNull();
    expect(sanitizeTitle("Unfortunately there is nothing to name here")).toBeNull();
  });

  it("refuses a one-word answer that restates the question", () => {
    expect(sanitizeTitle("Title")).toBeNull();
    expect(sanitizeTitle("conversation")).toBeNull();
    expect(sanitizeTitle("Untitled")).toBeNull();
    expect(sanitizeTitle("Attachment")).toBeNull(); // the exact name this feature exists to stop
    expect(sanitizeTitle("Sure!")).toBeNull();
  });

  it("refuses an empty answer, which is what a thinking model returns on a small budget", () => {
    expect(sanitizeTitle("")).toBeNull();
    expect(sanitizeTitle("   \n  ")).toBeNull();
    expect(sanitizeTitle('""')).toBeNull();
    expect(sanitizeTitle("A")).toBeNull();
  });
});

describe("applyModelTitle", () => {
  it("names a conversation still carrying its derived placeholder", () => {
    const applied = applyModelTitle(conversation("analyze these images and te", "derived"), "Image analysis");
    expect(applied.title).toBe("Image analysis");
    expect(applied.titledBy).toBe("model");
  });

  it("NEVER replaces a title the user typed", () => {
    const named = conversation("Q3 numbers", "user");
    // Same object back, not a rebuilt one that happens to match: nothing about the conversation
    // changed, so nothing downstream should think it did.
    expect(applyModelTitle(named, "Quarterly revenue review")).toBe(named);
    expect(named.title).toBe("Q3 numbers");
  });

  it("does not care what the title says, only how it got there", () => {
    // A user who renames a chat to exactly what `deriveTitle` produced is still a user who renamed
    // it — which is why the guard is the recorded source and never a comparison of the strings.
    const named = conversation("analyze these images and te", "user");
    expect(applyModelTitle(named, "Image analysis")).toBe(named);
  });
});
