"""Submission validation and the cap table (docs/PROTOCOL.md).

Rune-boundary truncation is the load-bearing rule here: Python ``str``
slicing is code-point based, so the rule is decode once at the websocket
edge, cap the ``str``, and only then re-encode. A byte-boundary truncation
is what makes replay bytes fail a strict JSON reader.
"""

from __future__ import annotations

import json

import pytest
from cogame_cogolf import contract
from cogame_cogolf.engine import (clean_text, compact, sanitize_submission,
                                  validate_submission_message)

HOLE = 3


def message(**overrides) -> dict:
    data = {"type": "submission", "hole": HOLE,
            "impl": "def solve(x):\n    return x\n",
            "tests": [{"name": "a", "args": [1], "expect": 1, "why": "w"}],
            "note": "n"}
    data.update(overrides)
    return data


def test_a_good_submission_validates():
    payload, cause = validate_submission_message(message(), HOLE)
    assert cause is None and payload["impl"].startswith("def solve")


@pytest.mark.parametrize("bad", [
    "not a dict", 42, None,
    {"type": "program", "hole": HOLE, "impl": "x"},
    {"type": "submission", "hole": HOLE},
    {"type": "submission", "hole": HOLE, "impl": 5},
    {"type": "submission", "hole": HOLE, "impl": "   "},
    {"type": "submission", "hole": True, "impl": "x"},
    {"type": "submission", "hole": HOLE, "impl": "x", "tests": "nope"},
    {"type": "submission", "hole": HOLE, "impl": "x", "note": 7},
])
def test_malformed_replies_are_malformed(bad):
    payload, cause = validate_submission_message(bad, HOLE)
    assert payload is None and cause == "malformed"


def test_over_cap_impl_is_oversize_and_never_truncated():
    payload, cause = validate_submission_message(
        message(impl="x" * (contract.MAX_IMPL_CHARS + 1)), HOLE)
    assert payload is None and cause == "oversize"


def test_a_reply_for_another_hole_is_counted_and_dropped_not_fatal():
    payload, cause = validate_submission_message(message(hole=HOLE + 1), HOLE)
    assert payload is None and cause == "wrong_hole"


def test_the_sixth_test_is_dropped():
    tests = [{"name": f"t{i}", "args": [i], "expect": i, "why": ""}
             for i in range(7)]
    payload, cause = validate_submission_message(message(tests=tests), HOLE)
    assert cause is None
    clean = sanitize_submission(payload, HOLE, contract.MAX_TESTS_PER_HOLE)
    assert len(clean["tests"]) == contract.MAX_TESTS_PER_HOLE
    assert clean["dropped_tests"] == 2
    assert [t["idx"] for t in clean["tests"]] == [0, 1, 2, 3, 4]


def test_name_why_and_note_truncate_on_rune_boundaries():
    """A 4-byte emoji sitting on the cap is not split, and the result
    re-encodes as strict UTF-8."""
    emoji = "🏌"                      # one code point, four UTF-8 bytes
    name = "a" * (contract.MAX_TEST_NAME_CHARS - 1) + emoji + "tail"
    why = "b" * (contract.MAX_WHY_CHARS - 1) + emoji + "tail"
    note = "c" * (contract.MAX_NOTE_CHARS - 1) + emoji + "tail"
    payload, _ = validate_submission_message(
        message(note=note,
                tests=[{"name": name, "args": [1], "expect": 1, "why": why}]),
        HOLE)
    clean = sanitize_submission(payload, HOLE, 5)
    assert len(clean["tests"][0]["name"]) == contract.MAX_TEST_NAME_CHARS
    assert len(clean["tests"][0]["why"]) == contract.MAX_WHY_CHARS
    assert len(clean["note"]) == contract.MAX_NOTE_CHARS
    for text in (clean["tests"][0]["name"], clean["tests"][0]["why"],
                 clean["note"]):
        # no surrogate half, no partial code point: strict UTF-8 round trip
        assert text.encode("utf-8").decode("utf-8") == text
        assert json.loads(json.dumps(text)) == text


def test_a_lone_surrogate_becomes_the_replacement_character():
    lone = "ok" + "\ud800" + "after"
    cleaned = clean_text(lone)
    assert "\ud800" not in cleaned and "\ufffd" in cleaned
    assert cleaned.encode("utf-8").decode("utf-8") == cleaned


def test_control_characters_other_than_newline_and_tab_are_stripped():
    cleaned = clean_text("a\x00b\x07c\nd\te")
    assert cleaned == "abc\nd\te"


def test_impl_keeps_its_newlines_and_tabs():
    source = "def solve(x):\n\treturn x\n"
    clean = sanitize_submission(message(impl=source), HOLE, 5)
    assert clean["impl"] == source


def test_over_cap_args_or_expect_are_within_the_gate_not_the_sanitiser():
    """The sanitiser keeps oversize args/expect verbatim; the legality gate
    marks them `illegal: oversize` (they are data, not free text)."""
    big = list(range(500))
    clean = sanitize_submission(
        message(tests=[{"name": "big", "args": [big], "expect": big, "why": ""}]),
        HOLE, 5)
    assert clean["tests"][0]["args"] == [big]
    assert len(compact([big])) > contract.MAX_ARGS_CHARS


def test_a_missing_tests_array_is_an_empty_one():
    payload, cause = validate_submission_message(message(tests=None), HOLE)
    assert cause is None
    assert sanitize_submission(payload, HOLE, 5)["tests"] == []


def test_non_object_test_entries_are_dropped():
    clean = sanitize_submission(
        message(tests=["nope", {"name": "ok", "args": [1], "expect": 1}]),
        HOLE, 5)
    assert [t["name"] for t in clean["tests"]] == ["ok"]
