"""The results document: a CLOSED schema, zero-sum scores, and the fault
document that a lost episode still writes."""

from __future__ import annotations

import json

import pytest
from cogame_cogolf import contract
from cogame_cogolf.results import (REASONS, RESULT_KEYS, EpisodeResult,
                                   SeatOutcome, fault_results_doc, results_doc,
                                   zero_fallback_causes)
from tests.conftest import make_config


def outcome(hole_scores, **fields) -> SeatOutcome:
    seat = SeatOutcome(hole_scores=list(hole_scores))
    for key, value in fields.items():
        setattr(seat, key, value)
    return seat


def doc(**overrides) -> dict:
    config = make_config()
    seats = (outcome([3, -1], breaches=4, par_fails=1, tests_fired=9),
             outcome([-3, 1], breaches=2, breaches_taken=4, illegal_tests=3))
    result = EpisodeResult(seats=seats, reason="complete",
                           wall_clock_seconds=231.5, holes_played=2, seed=42,
                           deck_version="core-1", killer_test=None)
    for key, value in overrides.items():
        result = EpisodeResult(**{**result.__dict__, key: value})
    return results_doc(config, result)


def test_the_key_set_is_closed_and_matches_the_contract():
    assert set(doc()) == RESULT_KEYS == set(contract.RESULT_KEYS)
    assert REASONS == contract.REASONS == ("complete", "deadline",
                                           "harness_fault")


def test_the_document_is_json_and_carries_both_name_spaces():
    d = doc()
    assert json.loads(json.dumps(d)) == d
    assert d["names"] == ["bot-0", "bot-1"]
    assert d["aliases"] == list(contract.ALIASES)


def test_scores_are_the_sum_of_the_hole_scores_and_zero_sum():
    d = doc()
    assert d["scores"] == [2, -2]
    assert d["scores"][0] + d["scores"][1] == 0
    assert d["hole_scores"] == [[3, -1], [-3, 1]]
    assert d["holes_played"] == 2


def test_the_tallies_are_per_seat_integers():
    d = doc()
    assert d["breaches"] == [4, 2]
    assert d["breaches_taken"] == [0, 4]
    assert d["par_fails"] == [1, 0]
    assert d["tests_fired"] == [9, 0]
    assert d["illegal_tests"] == [0, 3]
    assert d["fallbacks"] == [0, 0]
    assert d["fallback_causes"] == [zero_fallback_causes()] * 2
    assert set(d["fallback_causes"][0]) == set(contract.FALLBACK_CAUSES)


def test_the_reproducibility_fields_are_recorded():
    d = doc()
    assert d["seed"] == 42 and d["deck_version"] == "core-1"
    assert d["reason"] == "complete"
    assert d["wall_clock_seconds"] == 231.5


def test_a_killer_test_round_trips():
    killer = {"hole": 2, "slot": 1, "target_slot": 0, "name": "empty list",
              "why": "the spec says an empty list is not an error"}
    d = doc(killer_test=killer)
    assert d["killer_test"] == killer
    assert set(killer) == set(contract.KILLER_TEST_KEYS)


def test_the_fault_document_is_schema_complete():
    """A harness fault still writes a full results doc — zeros when nothing
    was played — and the process still exits 0."""
    config = make_config()
    d = fault_results_doc(config, 12.5, 7, "core-1")
    assert set(d) == RESULT_KEYS
    assert d["reason"] == "harness_fault"
    assert d["scores"] == [0, 0] and d["holes_played"] == 0
    assert d["killer_test"] is None
    partial = fault_results_doc(config, 12.5, 7, "core-1",
                               (outcome([2]), outcome([-2])))
    assert partial["scores"] == [2, -2] and partial["holes_played"] == 1


@pytest.mark.parametrize("reason", REASONS)
def test_every_reason_in_the_enum_is_writable(reason):
    assert doc(reason=reason)["reason"] == reason
