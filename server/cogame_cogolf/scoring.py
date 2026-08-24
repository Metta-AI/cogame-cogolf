"""Scoring — pure functions, no I/O.

For hole ``h``, seat ``i``, opponent ``j``::

    hole_score[i][h] = (breaches[i][h] + par_fails[j][h])
                     - (breaches[j][h] + par_fails[i][h])

``breaches[i][h]`` counts seat i's LEGAL tests that made seat j's
implementation fail (0..5); ``par_fails[i][h]`` counts the hidden par tests
seat i's own implementation failed (0..4). Illegal tests contribute
nothing. The pair is exactly antisymmetric, so
``hole_score[0][h] == -hole_score[1][h]`` and a match is zero-sum;
``tests/test_scoring.py`` asserts that over randomised outcome matrices.

Higher is better: a positive score means you breached more than you were
breached and your code survived the audit better than theirs.
"""

from __future__ import annotations

SEATS = 2


def hole_score(breaches: list[int], par_fails: list[int]) -> list[int]:
    """The per-seat score of one hole (a 2-list, summing to zero)."""
    if len(breaches) != SEATS or len(par_fails) != SEATS:
        raise ValueError("cogolf scores exactly two seats")
    delta = (int(breaches[0]) + int(par_fails[1])) \
        - (int(breaches[1]) + int(par_fails[0]))
    return [delta, -delta]


def match_scores(per_hole: list[list[int]]) -> list[int]:
    """Sum per-hole scores (``[[s0, s1], ...]``) into the match score."""
    totals = [0, 0]
    for scores in per_hole:
        if len(scores) != SEATS:
            raise ValueError("every hole scores exactly two seats")
        totals[0] += int(scores[0])
        totals[1] += int(scores[1])
    return totals


def cumulative(per_hole: list[list[int]]) -> list[list[int]]:
    """Running totals after each hole (same shape as ``per_hole``)."""
    running = [0, 0]
    out = []
    for scores in per_hole:
        running = [running[0] + int(scores[0]), running[1] + int(scores[1])]
        out.append(list(running))
    return out


def winner(scores: list[int]) -> int | None:
    """The winning slot, or None for a draw."""
    if scores[0] > scores[1]:
        return 0
    if scores[1] > scores[0]:
        return 1
    return None


def killer_test(shots: list[dict], per_hole: list[list[int]],
                scores: list[int]) -> dict | None:
    """The endcard's single killer test.

    Among all ``breach`` shots fired by the WINNING seat, the one in the
    hole with the largest score swing for that seat, tie-broken by earliest
    hole then lowest test index. ``None`` for a draw or when nobody
    breached — the endcard then reads NO BREACH — DRAWN MATCH.

    ``shots`` are ``{"hole", "slot", "target_slot", "idx", "name", "why",
    "outcome"}`` records in chronological order; ``per_hole[h]`` is that
    hole's ``[s0, s1]``.
    """
    champion = winner(scores)
    if champion is None:
        return None
    best = None
    best_key = None
    for shot in shots:
        if shot.get("outcome") != "breach" or shot.get("slot") != champion:
            continue
        hole = int(shot.get("hole", 0))
        index = hole - 1
        swing = per_hole[index][champion] if 0 <= index < len(per_hole) else 0
        key = (-swing, hole, int(shot.get("idx", 0)))
        if best_key is None or key < best_key:
            best_key = key
            best = shot
    if best is None:
        return None
    return {
        "hole": int(best.get("hole", 0)),
        "slot": int(best.get("slot", 0)),
        "target_slot": int(best.get("target_slot", 0)),
        "name": str(best.get("name") or ""),
        "why": str(best.get("why") or ""),
    }
