"""The scripted baselines as a player policy.

Both baselines are the engine's own (``cogame_cogolf.baseline``), so the
move a seat plays as ``PLAYER_SCRIPTED=literalist`` is byte-identical to
the move the engine synthesises for a seat that missed its deadline. The
spec is looked up in the public deck by the key the observation carries;
an unknown key falls through to the baseline's bounded stub.
"""

from __future__ import annotations

import sys

from players.client import Policy, main_for

try:  # pragma: no cover - the image always has the server package
    from cogame_cogolf.baseline import BASELINE_NAMES, baseline
    from cogame_cogolf.specs import load_deck
except Exception as exc:  # noqa: BLE001
    raise SystemExit(f"cogame_cogolf is not importable: {exc!r}") from exc


class UnknownBaseline(ValueError):
    """PLAYER_SCRIPTED named something that is not a baseline."""


class _MissingSpec:
    """Stand-in for a spec key this build does not know."""

    KEY = "unknown"
    EXAMPLES: list = []


def spec_for(observation: dict, deck: str = "core"):
    """The spec module the observation names, or a stand-in."""
    spec_view = observation.get("spec") or {}
    key = spec_view.get("key")
    try:
        modules = load_deck(deck)
    except Exception:  # noqa: BLE001
        modules = {}
    found = modules.get(key)
    if found is not None:
        return found
    stub = _MissingSpec()
    stub.EXAMPLES = list(spec_view.get("examples") or [])
    return stub


def scripted_submission(name: str, hole: int, observation: dict) -> dict:
    """The named baseline's submission for this hole (no wire envelope)."""
    rules = observation.get("rules") or {}
    max_tests = rules.get("max_tests_per_hole")
    if not isinstance(max_tests, int) or isinstance(max_tests, bool) \
            or max_tests < 1:
        max_tests = 5
    message = baseline(name, spec_for(observation), hole, max_tests)
    return {"impl": message["impl"], "tests": message["tests"],
            "note": message["note"]}


class ScriptedPolicy(Policy):
    """``PLAYER_SCRIPTED=<literalist|pedant>``."""

    def __init__(self, name: str):
        if name not in BASELINE_NAMES:
            raise UnknownBaseline(
                f"unknown scripted baseline {name!r}; known: "
                f"{list(BASELINE_NAMES)}")
        self.name = name
        print(f"scripted_player: playing the {name!r} baseline",
              file=sys.stderr, flush=True)

    def submission(self, hole: int, observation: dict) -> dict:
        return scripted_submission(self.name, hole, observation)


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    import os
    main_for(lambda: ScriptedPolicy(
        os.environ.get("PLAYER_SCRIPTED", "literalist")))
