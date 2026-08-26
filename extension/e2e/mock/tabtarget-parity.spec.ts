// Parity pin for the URL-based target selection: `selectTarget` (extension) must agree with
// `stabbur.targets.select` (server) case-for-case. The fixtures below are COPIED VERBATIM from
// tests/test_targets.py's SELECT_CASES — origin equality, longest-base-path-prefix ranking, the "/"
// catch-all, and normalization of default ports / trailing slashes / host case. Change one side, change
// both. A pure-function spec (no browser/extension fixture), so it runs fast under the `mock` project.

import { test, expect } from "@playwright/test";
import { selectTarget } from "../../lib/tabTarget";
import { slugify, type AssistantTarget } from "../../lib/assistantApi";

/** Build targets from (name, base_url) pairs, in declaration order — the TS twin of the Python
 *  `_registry` helper. The id is the slugified name (all fixture names are already slug-safe). */
function targets(pairs: [string, string | null][]): AssistantTarget[] {
  return pairs.map(([name, base_url]) => ({
    id: slugify(name) || "target-0",
    name,
    base_url: base_url ?? undefined,
    mcp_servers: [],
    can_verify: false,
    verified: null,
  }));
}

// (case_name, tab_url, [(target_name, base_url)], expected_matches, expected_selected) — mirrored
// VERBATIM from tests/test_targets.py's SELECT_CASES.
//   expected_matches  -> selectTarget().matches:  every candidate id, most-specific first (ranked list).
//   expected_selected -> selectTarget().selected: the unique strictly-highest-rank id, else null (real
//                        tie / no match). Note longest_path_prefix_wins auto-picks the specific target
//                        even though the "/" catch-all also matches.
const SELECT_CASES: [string, string, [string, string][], string[], string | null][] = [
  [
    "exact_origin_and_path_match",
    "https://play.im.dhis2.org/dev-2-42/api/me",
    [["play42", "https://play.im.dhis2.org/dev-2-42"]],
    ["play42"],
    "play42",
  ],
  [
    "different_origin_no_match",
    "https://other.example.org/dev-2-42/api/me",
    [["play42", "https://play.im.dhis2.org/dev-2-42"]],
    [],
    null,
  ],
  [
    "longest_path_prefix_wins",
    "https://play.im.dhis2.org/dev-2-42/api/me",
    [
      ["root", "https://play.im.dhis2.org/"],
      ["play42", "https://play.im.dhis2.org/dev-2-42"],
    ],
    ["play42", "root"],
    "play42",
  ],
  [
    "nested_path_prefix_ranking",
    "https://play.im.dhis2.org/dev/2/42/api",
    [
      ["broad", "https://play.im.dhis2.org/dev"],
      ["deep", "https://play.im.dhis2.org/dev/2/42"],
    ],
    ["deep", "broad"],
    "deep",
  ],
  [
    "equal_length_tie_keeps_declaration_order",
    "https://play.im.dhis2.org/dev-2-42/api/me",
    [
      ["first", "https://play.im.dhis2.org/dev-2-42"],
      ["second", "https://play.im.dhis2.org/dev-2-42"],
    ],
    ["first", "second"],
    null,
  ],
  [
    "base_root_is_catch_all",
    "https://play.im.dhis2.org/anything/here",
    [["root", "https://play.im.dhis2.org/"]],
    ["root"],
    "root",
  ],
  [
    "path_boundary_not_substring",
    "https://play.im.dhis2.org/dev-2-42/api",
    [["dev", "https://play.im.dhis2.org/dev"]],
    [],
    null,
  ],
  [
    "default_port_and_host_case_normalized",
    "https://PLAY.im.dhis2.org:443/dev-2-42/api",
    [["play42", "https://play.im.dhis2.org/dev-2-42"]],
    ["play42"],
    "play42",
  ],
  [
    "trailing_slash_on_base_stripped",
    "https://play.im.dhis2.org/dev-2-42/api",
    [["play42", "https://play.im.dhis2.org/dev-2-42/"]],
    ["play42"],
    "play42",
  ],
  [
    "unparseable_url_returns_empty",
    "not a url",
    [["play42", "https://play.im.dhis2.org/dev-2-42"]],
    [],
    null,
  ],
  [
    "relative_url_returns_empty",
    "/dev-2-42/api",
    [["root", "https://play.im.dhis2.org/"]],
    [],
    null,
  ],
];

for (const [name, tabUrl, pairs, expectedMatches, expectedSelected] of SELECT_CASES) {
  test(`selectTarget parity: ${name}`, () => {
    const { matches, selected } = selectTarget(tabUrl, targets(pairs));
    expect(matches.map((t) => t.id)).toEqual(expectedMatches);
    // `selected` is the unique strictly-highest-rank pick, null on a real tie / no match (mirrors
    // stabbur.targets.selected — the explicit column above, NOT derived from matches.length).
    expect(selected?.id ?? null).toBe(expectedSelected);
  });
}

test("selectTarget skips targets without base_url", () => {
  const reg = targets([
    ["nobase", null],
    ["play42", "https://play.im.dhis2.org/dev-2-42"],
  ]);
  expect(selectTarget("https://play.im.dhis2.org/dev-2-42/api", reg).matches.map((t) => t.id)).toEqual(["play42"]);
});

test("selectTarget empty registry is empty", () => {
  expect(selectTarget("https://play.im.dhis2.org/x", []).matches).toEqual([]);
});
