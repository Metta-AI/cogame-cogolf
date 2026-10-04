"""The scripted baselines as a player policy.

Both baselines are the engine's own (``cogame_cogolf.baseline``), so the
move a seat plays as ``PLAYER_SCRIPTED=literalist`` is byte-identical to
the move the engine synthesises for a seat that missed its deadline. A public spec is reconstructed from the actual observation; unchanged static
policy tables are selected by its key. No hidden reference or audit is imported.
"""

from __future__ import annotations

import sys

from cogame_cogolf.baseline import BASELINE_NAMES, baseline
from pydantic import BaseModel, ConfigDict, Field

from players.client import Policy, main_for


class UnknownBaseline(ValueError):
    """PLAYER_SCRIPTED named something that is not a baseline."""


class PublicSpec(BaseModel):
    """Only the specification fields actually present in a private observation."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)
    key: str
    examples: list[dict] = Field(default_factory=list)

    @property
    def KEY(self) -> str:
        return self.key

    @property
    def EXAMPLES(self) -> list[dict]:
        return self.examples


def spec_for(observation: dict) -> PublicSpec:
    return PublicSpec.model_validate(observation["spec"])


def scripted_submission(name: str, hole: int, observation: dict) -> dict:
    """The named baseline's submission for this hole (no wire envelope)."""
    rules = observation.get("rules") or {}
    max_tests = rules.get("max_tests_per_hole")
    if not isinstance(max_tests, int) or isinstance(max_tests, bool) or max_tests < 1:
        max_tests = 5
    message = baseline(name, spec_for(observation), hole, max_tests)
    return {"impl": message["impl"], "tests": message["tests"], "note": message["note"]}


class ScriptedPolicy(Policy):
    """``PLAYER_SCRIPTED=<literalist|pedant>``."""

    def __init__(self, name: str):
        super().__init__()
        if name not in BASELINE_NAMES:
            raise UnknownBaseline(
                f"unknown scripted baseline {name!r}; known: {list(BASELINE_NAMES)}"
            )
        self.name = name
        print(
            f"scripted_player: playing the {name!r} baseline",
            file=sys.stderr,
            flush=True,
        )

    async def submission(self, hole: int, observation: dict) -> dict:
        return scripted_submission(self.name, hole, observation)


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    import os

    main_for(lambda: ScriptedPolicy(os.environ.get("PLAYER_SCRIPTED", "literalist")))
