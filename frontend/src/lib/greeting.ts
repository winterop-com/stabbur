/**
 * What the empty chat says before you have typed anything.
 *
 * A fixed line is the first thing you read every single time you open stabbur, and it goes stale
 * faster than anything else on the screen. These rotate — quietly. Nothing here is a joke: a joke
 * is funny once and then you meet it four hundred more times.
 *
 * Deliberately *not* model-generated. There is nothing to summarise before a conversation exists,
 * so a model would add a round trip, a failure mode, and a wait on the one screen where a wait is
 * most visible — to produce worse lines than ones written on purpose. Naming a conversation needs
 * the model because it reads content that only exists at runtime; a greeting does not.
 *
 * Some lines state a fact about this machine. Those are only offered when the fact is known and
 * true, because a greeting that decorates itself with a made-up number is worse than a plain one.
 */

/** What the greeting may draw on. Everything is optional: a fresh install knows none of it. */
export interface GreetingFacts {
  /** Models in the library. Omitted while it is still loading. */
  models?: number;
  /** The host stabbur is fronting, when it is not running the model itself. */
  upstream?: string | null;
}

/** Lines that are true anywhere, any time. */
const ALWAYS: string[] = [
  "What can I help with?",
  "Your models, at home.",
  "Ask it anything.",
  "Welcome home.",
  "Nothing here leaves the house.",
  "What are we working on?",
];

/**
 * Lines that need a fact, and are only offered once it is known.
 *
 * The plural is spelled out per line rather than assembled, so no line can produce "1 models" —
 * cheaper than a pluralisation helper for a handful of strings, and impossible to get wrong.
 */
function situational(facts: GreetingFacts): string[] {
  const lines: string[] = [];
  const { models, upstream } = facts;
  if (models === 1) lines.push("One model on the shelf.");
  if (models !== undefined && models > 1) lines.push(`${models} models on the shelf.`);
  if (upstream) lines.push(`Running on ${upstream.replace(/^https?:\/\//, "")}.`);
  else if (upstream === null) lines.push("Running on this machine.");
  return lines;
}

/**
 * Pick a greeting.
 *
 * ``seed`` keeps it stable for as long as the caller wants it stable — pass the conversation's id
 * and the line holds still while you read it, rather than changing on every re-render, which would
 * be the most distracting possible version of this idea.
 */
export function greetingFor(facts: GreetingFacts, seed: string): string {
  const pool = [...ALWAYS, ...situational(facts)];
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  return pool[Math.abs(hash) % pool.length];
}
