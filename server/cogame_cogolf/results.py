"""The results document (``COGAME_RESULTS_URI``) — a CLOSED schema.

Triple-sync rule: the key set produced here == the manifest
``results_schema`` == ``tools/ci/docker_smoke.sh`` expectations, and the
``reason`` values == the schema enum. ``tests/test_manifest.py`` is the
tripwire.

The platform ranks by ``scores``: one scalar per seat, higher wins,
``[0, 0]`` is a draw. ``scores`` is zero-sum by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args

from . import contract
from .config import GameConfig

# Per-seat fallback cause taxonomy (results `fallback_causes`):
#   timeout       the hole deadline (and its one retry) elapsed with no valid
#                 submission while the seat was connected at some point
#   malformed     a reply arrived but failed shape validation
#   oversize      the reply, or its impl, blew a cap
#   disconnected  the seat had no connection for the whole hole
#   host_error    the transport raised
FALLBACK_CAUSES = contract.FALLBACK_CAUSES

Reason = Literal["complete", "deadline", "harness_fault"]
REASONS: tuple[str, ...] = get_args(Reason)
assert REASONS == contract.REASONS
REASON_COMPLETE: Reason = "complete"
REASON_DEADLINE: Reason = "deadline"
REASON_HARNESS_FAULT: Reason = "harness_fault"

RESULT_KEYS = frozenset(contract.RESULT_KEYS)


def zero_fallback_causes() -> dict:
    return dict.fromkeys(FALLBACK_CAUSES, 0)


@dataclass
class SeatOutcome:
    """Per-seat tallies the engine accumulates while a seat plays."""
    breaches: int = 0            # legal tests of ours that broke their impl
    breaches_taken: int = 0      # legal tests of theirs that broke ours
    par_fails: int = 0           # hidden audit cases our impl failed
    tests_fired: int = 0         # legal tests we fired
    illegal_tests: int = 0       # tests of ours the reference rejected
    fallbacks: int = 0
    fallback_causes: dict = field(default_factory=zero_fallback_causes)
    hole_scores: list = field(default_factory=list)

    @property
    def score(self) -> int:
        return int(sum(self.hole_scores))


@dataclass(frozen=True)
class EpisodeResult:
    seats: tuple[SeatOutcome, ...]
    reason: Reason
    wall_clock_seconds: float
    holes_played: int
    seed: int
    deck_version: str
    killer_test: dict | None = None


def results_doc(config: GameConfig, result: EpisodeResult) -> dict:
    seats = result.seats
    assert len(seats) == config.num_seats
    return {
        "names": [p.name for p in config.players],
        "aliases": list(contract.ALIASES[:config.num_seats]),
        "scores": [int(s.score) for s in seats],
        "hole_scores": [[int(v) for v in s.hole_scores] for s in seats],
        "breaches": [int(s.breaches) for s in seats],
        "breaches_taken": [int(s.breaches_taken) for s in seats],
        "par_fails": [int(s.par_fails) for s in seats],
        "tests_fired": [int(s.tests_fired) for s in seats],
        "illegal_tests": [int(s.illegal_tests) for s in seats],
        "holes_played": int(result.holes_played),
        "fallbacks": [int(s.fallbacks) for s in seats],
        "fallback_causes": [dict(s.fallback_causes) for s in seats],
        "reason": result.reason,
        "wall_clock_seconds": float(result.wall_clock_seconds),
        "seed": int(result.seed),
        "deck_version": str(result.deck_version),
        "killer_test": result.killer_test,
    }


def fault_results_doc(config: GameConfig, wall_clock_seconds: float,
                      seed: int, deck_version: str,
                      seats: tuple[SeatOutcome, ...] | None = None) -> dict:
    """A schema-complete results doc for an episode the harness lost.

    ``reason: harness_fault`` with whatever per-seat tallies exist (zeros
    when nothing was played); the process still exits 0 and the artifacts
    are still written.
    """
    if seats is None:
        seats = tuple(SeatOutcome() for _ in range(config.num_seats))
    holes = min(len(s.hole_scores) for s in seats) if seats else 0
    return results_doc(config, EpisodeResult(
        seats=seats, reason=REASON_HARNESS_FAULT,
        wall_clock_seconds=wall_clock_seconds, holes_played=holes,
        seed=seed, deck_version=deck_version, killer_test=None))
