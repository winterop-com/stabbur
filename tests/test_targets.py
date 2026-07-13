"""Tests for the multi-target registry and URL-based selection (:mod:`heim.targets`).

The ``select`` cases below are the **parity fixtures**: they mirror the extension's ``tabTarget.match`` /
``selectTarget`` semantics one-for-one (origin equality, longest-base-path-prefix ranking, ``/`` catch-all,
normalization of default ports / trailing slashes / host case). The future TS parity test copies
``SELECT_CASES`` verbatim — each case is a named ``(tab_url, [(name, base_url)], expected_ids)`` tuple, so
keep the two sides in lockstep: change one, change both.
"""

import pytest

from heim.project import AssistantInfo
from heim.targets import AssistantRegistry, select


def _registry(*targets: tuple[str | None, str | None]) -> AssistantRegistry:
    """Build a registry from ``(name, base_url)`` pairs, in declaration order."""
    return AssistantRegistry(targets=[AssistantInfo(name=name, base_url=base_url) for name, base_url in targets])


# --- id derivation ---------------------------------------------------------------------------


def test_id_is_slugified_name() -> None:
    assert AssistantInfo(name="Play 42").id == "play-42"
    assert AssistantInfo(name="play42").id == "play42"


def test_id_falls_back_to_positional_when_unnamed() -> None:
    assert AssistantInfo().id == "target-0"
    assert AssistantInfo(name="!!!").id == "target-0"  # nothing alphanumeric survives the slug


def test_registry_ids_are_collision_safe() -> None:
    # Two targets sharing a name get disambiguated with a -2 suffix so by_id stays unambiguous.
    reg = _registry(("play", "https://h/a"), ("play", "https://h/b"), ("play", "https://h/c"))
    assert reg.ids == ["play", "play-2", "play-3"]
    assert reg.by_id("play-2") is reg.targets[1]
    assert reg.by_id("nope") is None


def test_registry_unnamed_ids_are_positional() -> None:
    reg = _registry((None, "https://h/a"), (None, "https://h/b"))
    assert reg.ids == ["target-0", "target-1"]


def test_registry_primary_is_first_or_none() -> None:
    assert AssistantRegistry().primary is None
    reg = _registry(("a", "https://h/a"), ("b", "https://h/b"))
    assert reg.primary is reg.targets[0]


# --- selection: parity fixtures mirrored from tabTarget.match ---------------------------------

# (case_name, tab_url, [(target_name, base_url)], expected_ids)
SELECT_CASES: list[tuple[str, str, list[tuple[str, str]], list[str]]] = [
    (
        "exact_origin_and_path_match",
        "https://play.im.dhis2.org/dev-2-42/api/me",
        [("play42", "https://play.im.dhis2.org/dev-2-42")],
        ["play42"],
    ),
    (
        "different_origin_no_match",
        "https://other.example.org/dev-2-42/api/me",
        [("play42", "https://play.im.dhis2.org/dev-2-42")],
        [],
    ),
    (
        "longest_path_prefix_wins",
        "https://play.im.dhis2.org/dev-2-42/api/me",
        # broad "/" catch-all vs the specific "/dev-2-42": the specific (longer) base wins, ordered first.
        [("root", "https://play.im.dhis2.org/"), ("play42", "https://play.im.dhis2.org/dev-2-42")],
        ["play42", "root"],
    ),
    (
        "nested_path_prefix_ranking",
        "https://play.im.dhis2.org/dev/2/42/api",
        [("broad", "https://play.im.dhis2.org/dev"), ("deep", "https://play.im.dhis2.org/dev/2/42")],
        ["deep", "broad"],
    ),
    (
        "equal_length_tie_keeps_declaration_order",
        "https://play.im.dhis2.org/dev-2-42/api/me",
        [("first", "https://play.im.dhis2.org/dev-2-42"), ("second", "https://play.im.dhis2.org/dev-2-42")],
        ["first", "second"],
    ),
    (
        "base_root_is_catch_all",
        "https://play.im.dhis2.org/anything/here",
        [("root", "https://play.im.dhis2.org/")],
        ["root"],
    ),
    (
        "path_boundary_not_substring",
        # "/dev" must NOT match a "/dev-2-42" tab (the match is slash-bounded, not a raw substring).
        "https://play.im.dhis2.org/dev-2-42/api",
        [("dev", "https://play.im.dhis2.org/dev")],
        [],
    ),
    (
        "default_port_and_host_case_normalized",
        "https://PLAY.im.dhis2.org:443/dev-2-42/api",
        [("play42", "https://play.im.dhis2.org/dev-2-42")],
        ["play42"],
    ),
    (
        "trailing_slash_on_base_stripped",
        "https://play.im.dhis2.org/dev-2-42/api",
        [("play42", "https://play.im.dhis2.org/dev-2-42/")],
        ["play42"],
    ),
    (
        "unparseable_url_returns_empty",
        "not a url",
        [("play42", "https://play.im.dhis2.org/dev-2-42")],
        [],
    ),
    (
        "relative_url_returns_empty",
        "/dev-2-42/api",
        [("root", "https://play.im.dhis2.org/")],
        [],
    ),
]


@pytest.mark.parametrize(
    "tab_url,targets,expected", [(c[1], c[2], c[3]) for c in SELECT_CASES], ids=[c[0] for c in SELECT_CASES]
)
def test_select_matches_tabtarget_semantics(tab_url: str, targets: list[tuple[str, str]], expected: list[str]) -> None:
    reg = AssistantRegistry(targets=[AssistantInfo(name=name, base_url=base) for name, base in targets])
    assert select(tab_url, reg) == expected


def test_select_skips_targets_without_base_url() -> None:
    # A target with no base_url can never match a tab (mirrors match's null-base "unknown").
    reg = _registry(("nobase", None), ("play42", "https://play.im.dhis2.org/dev-2-42"))
    assert select("https://play.im.dhis2.org/dev-2-42/api", reg) == ["play42"]


def test_select_empty_registry_is_empty() -> None:
    assert select("https://play.im.dhis2.org/x", AssistantRegistry()) == []
