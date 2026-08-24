"""The hole loop: order, the parallel batch, the retry, the fallback, and
the two early endings."""

from __future__ import annotations

import asyncio
import time

import pytest
from cogame_cogolf import contract
from cogame_cogolf.engine import Engine
from cogame_cogolf.sandbox import Sandbox, SandboxError
from cogame_cogolf.specs import load_deck
from tests.conftest import make_config
from tests.fakes import ScriptedSource

SANDBOX = Sandbox(call_cpu_seconds=1.0, batch_seconds=8.0)


def build(sources, sandbox=None, **overrides):
    config = make_config(**overrides)
    events, holes = [], []
    engine = Engine(config, sources, sandbox or SANDBOX, seed=config.seed,
                    on_event=events.append, on_hole=holes.append)
    return engine, events, holes


def test_one_hole_resolves_in_the_numbered_order():
    engine, events, holes = build(
        [ScriptedSource("literalist"), ScriptedSource("pedant")], holes=1)
    result = asyncio.run(engine.run())
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "hole_start"
    assert kinds.count("submission") == 2
    assert kinds.index("submission") < kinds.index("test_verdict")
    assert kinds.index("test_verdict") < kinds.index("par_result")
    assert kinds.index("par_result") < kinds.index("hole_score")
    assert kinds[-1] == "episode_end"
    assert set(kinds) == set(contract.EVENT_KINDS)
    assert result.reason == "complete" and result.holes_played == 1
    assert len(holes) == 1 and holes[0]["hole"] == 1
    assert result.seats[0].score == -result.seats[1].score


def test_both_observations_go_out_before_either_reply_is_awaited():
    """Cogolf is a simultaneous-decision game: ONE parallel batch per hole,
    concurrent awaits, never seat-by-seat. A sequential engine would send
    the second observation only after the first seat answered."""
    slow = ScriptedSource("literalist", delay=0.3)
    fast = ScriptedSource("pedant")
    engine, _events, _holes = build([slow, fast], holes=1)
    asyncio.run(engine.run())
    # both sends are recorded at (near) the same instant, well inside the
    # 0.3 s the first seat spends thinking
    assert len(slow.sent) == 1 and len(fast.sent) == 1
    assert abs(slow.sent[0][2] - fast.sent[0][2]) < 0.2


def test_a_missing_reply_gets_exactly_one_retry_and_then_the_fallback():
    silent = ScriptedSource("literalist", silent_holes=(1,))
    engine, events, holes = build([silent, ScriptedSource("pedant")], holes=1)
    result = asyncio.run(engine.run())
    assert [retry for _hole, retry, _t in silent.sent] == [False, True]
    assert result.seats[0].fallbacks == 1
    assert result.seats[0].fallback_causes["timeout"] == 1
    assert result.seats[1].fallbacks == 0
    fallback = [e for e in events if e["kind"] == "submission"
                and e["slot"] == 0][0]["fallback"]
    assert fallback == {"cause": "timeout", "baseline": "literalist"}
    # the seat is never removed: the hole is resolved for both
    assert holes[0]["seats"][0]["tests"], "the fallback plays a real move"
    assert result.holes_played == 1


def test_the_wall_guard_ends_the_episode_with_deadline():
    """With less than hole_reserve_seconds left the engine refuses to start
    another hole and settles on the last fully resolved one."""
    engine, events, holes = build(
        [ScriptedSource("literalist"), ScriptedSource("pedant")],
        holes=4, wall_clock_budget_seconds=0.6, hole_reserve_seconds=0.5)
    result = asyncio.run(engine.run())
    assert result.reason == "deadline"
    assert result.holes_played < 4
    # the in-flight hole is discarded, never half-scored
    assert len(holes) == result.holes_played
    assert all(len(s.hole_scores) == result.holes_played for s in result.seats)
    assert events[-1]["kind"] == "episode_end"
    assert events[-1]["reason"] == "deadline"


def test_a_source_that_raises_is_a_host_error_not_a_crash():
    class Exploding(ScriptedSource):
        async def get_submission(self, hole, payload, deadline_at):
            raise RuntimeError("transport went away")

    engine, _events, _holes = build(
        [Exploding(), ScriptedSource("pedant")], holes=1)
    result = asyncio.run(engine.run())
    assert result.seats[0].fallback_causes["host_error"] == 1
    assert result.reason == "complete"


def test_a_never_connected_seat_plays_the_fallback_and_is_reported_once():
    reported = []

    async def on_never_connected(slot):
        reported.append(slot)

    config = make_config(holes=1)
    silent = ScriptedSource("literalist", silent_holes=(1,))
    silent.connected = False
    engine = Engine(config, [silent, ScriptedSource("pedant")], SANDBOX,
                    seed=config.seed, on_never_connected=on_never_connected)
    result = asyncio.run(engine.run())
    assert reported == [0]
    assert result.seats[0].fallbacks == 1
    assert result.reason == "complete"


def test_a_harness_fault_propagates_so_artifacts_can_still_be_written():
    """The reference failing to load is a harness fault, not a game outcome:
    the server catches it, writes partial artifacts and exits 0."""

    class DeadSandbox:
        def run(self, source, calls, *, cpu_seconds=None):
            raise SandboxError("cannot spawn the sandbox runner")

        def run_reference(self, source, calls):
            raise SandboxError("cannot spawn the sandbox runner")

    engine, _events, _holes = build(
        [ScriptedSource("literalist"), ScriptedSource("pedant")],
        sandbox=DeadSandbox(), holes=1)
    with pytest.raises(SandboxError):
        asyncio.run(engine.run())


def test_holes_are_drawn_without_replacement_from_the_seeded_sample():
    config = make_config(holes=5, seed=99)
    engine = Engine(config, [ScriptedSource(), ScriptedSource()], SANDBOX,
                    seed=99)
    again = Engine(config, [ScriptedSource(), ScriptedSource()], SANDBOX,
                   seed=99)
    assert engine.spec_keys == again.spec_keys
    assert len(set(engine.spec_keys)) == 5
    assert set(engine.spec_keys) <= set(load_deck("core"))


def test_more_holes_than_the_deck_is_refused():
    config = make_config(holes=12)
    Engine(config, [ScriptedSource(), ScriptedSource()], SANDBOX)  # exactly 12 is fine
    object.__setattr__(config, "holes", 13)
    with pytest.raises(ValueError):
        Engine(config, [ScriptedSource(), ScriptedSource()], SANDBOX)


def test_the_observation_hides_the_reference_and_the_par_tests():
    engine, _events, _holes = build(
        [ScriptedSource("literalist"), ScriptedSource("pedant")], holes=1)
    spec = load_deck("core")[engine.spec_keys[0]]
    message = engine._observation_message(1, spec, 0, retry=False)
    blob = repr(message)
    assert spec.REFERENCE_IMPL not in blob
    assert spec.AMBIGUITY not in blob
    for par in spec.PAR_TESTS:
        assert repr(par["expect"]) not in blob or par["name"] not in blob
    assert message["observation"]["you"]["alias"] == "Ash"
    assert message["observation"]["opponent"]["alias"] == "Basil"
    # no real player name ever reaches a seat
    assert "bot-0" not in blob and "bot-1" not in blob


def test_history_is_capped_at_four_holes_and_bounded_in_size():
    engine, _events, _holes = build(
        [ScriptedSource("literalist"), ScriptedSource("pedant")], holes=6)
    asyncio.run(engine.run())
    for slot in (0, 1):
        history = engine._history[slot]
        assert len(history) == 4
        assert [h["hole"] for h in history] == [3, 4, 5, 6]


def test_holes_are_spaced_so_the_llm_sidecar_cannot_be_burst():
    engine, _events, _holes = build(
        [ScriptedSource("literalist"), ScriptedSource("pedant")],
        holes=3, min_hole_spacing_seconds=0.3)
    started = time.monotonic()
    asyncio.run(engine.run())
    assert time.monotonic() - started >= 0.6
