"""Scoring: the hole formula, zero-sum, and the killer test."""

from __future__ import annotations

import random

import pytest
from cogame_cogolf import scoring


def test_the_hole_formula():
    # you breached 3, they failed 2 audits; they breached 1, you failed 0
    assert scoring.hole_score([3, 1], [0, 2]) == [4, -4]
    assert scoring.hole_score([0, 0], [0, 0]) == [0, 0]
    # the extremes: five breaches of mine plus four audit failures of theirs
    assert scoring.hole_score([5, 0], [0, 4]) == [9, -9]
    assert scoring.hole_score([0, 5], [4, 0]) == [-9, 9]


def test_scores_are_zero_sum_over_a_thousand_random_outcome_matrices():
    rng = random.Random(20260824)
    for _ in range(1000):
        holes = rng.randint(0, 9)
        per_hole = []
        for _ in range(holes):
            breaches = [rng.randint(0, 5), rng.randint(0, 5)]
            par_fails = [rng.randint(0, 4), rng.randint(0, 4)]
            score = scoring.hole_score(breaches, par_fails)
            assert score[0] == -score[1]
            assert -9 <= score[0] <= 9
            per_hole.append(score)
        totals = scoring.match_scores(per_hole)
        assert totals[0] + totals[1] == 0
        assert -81 <= totals[0] <= 81
        running = scoring.cumulative(per_hole)
        assert not running or running[-1] == totals


def test_illegal_tests_score_nothing():
    """Illegal tests never reach the formula: only the breach count does."""
    fired_but_illegal = scoring.hole_score([0, 0], [0, 0])
    assert fired_but_illegal == [0, 0]


def test_wrong_seat_count_is_an_error():
    with pytest.raises(ValueError):
        scoring.hole_score([1], [0, 0])
    with pytest.raises(ValueError):
        scoring.match_scores([[1, -1], [2]])


def test_winner():
    assert scoring.winner([3, -3]) == 0
    assert scoring.winner([-3, 3]) == 1
    assert scoring.winner([0, 0]) is None


def shot(hole, slot, idx, outcome="breach", name=None):
    return {"hole": hole, "slot": slot, "target_slot": 1 - slot, "idx": idx,
            "name": name or f"t{hole}-{idx}", "why": "because",
            "outcome": outcome}


def test_killer_test_takes_the_biggest_swing():
    per_hole = [[1, -1], [4, -4], [2, -2]]
    shots = [shot(1, 0, 0), shot(2, 0, 3), shot(3, 0, 1), shot(2, 1, 0)]
    killer = scoring.killer_test(shots, per_hole, scoring.match_scores(per_hole))
    assert killer["hole"] == 2 and killer["slot"] == 0 and killer["name"] == "t2-3"
    assert killer["target_slot"] == 1 and killer["why"] == "because"


def test_killer_test_tie_breaks_by_earliest_hole_then_lowest_index():
    per_hole = [[3, -3], [3, -3]]
    shots = [shot(2, 0, 0), shot(1, 0, 4), shot(1, 0, 2)]
    killer = scoring.killer_test(shots, per_hole, [6, -6])
    assert killer["hole"] == 1 and killer["name"] == "t1-2"


def test_killer_test_only_considers_the_winners_breaches():
    per_hole = [[-5, 5]]
    shots = [shot(1, 0, 0), shot(1, 1, 2), shot(1, 1, 0, outcome="held")]
    killer = scoring.killer_test(shots, per_hole, [-5, 5])
    assert killer["slot"] == 1 and killer["name"] == "t1-2"


def test_a_draw_has_no_killer_test():
    assert scoring.killer_test([shot(1, 0, 0)], [[0, 0]], [0, 0]) is None


def test_no_breach_has_no_killer_test():
    shots = [shot(1, 0, 0, outcome="held"), shot(1, 0, 1, outcome="illegal")]
    assert scoring.killer_test(shots, [[2, -2]], [2, -2]) is None
