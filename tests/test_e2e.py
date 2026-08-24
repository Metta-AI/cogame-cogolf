"""End to end, in process: two scripted seats play a real match and both
artifacts land on disk."""

from __future__ import annotations

import asyncio
import json

from cogame_cogolf.engine import Engine
from cogame_cogolf.replay import Replay, ReplayWriter
from cogame_cogolf.results import results_doc
from cogame_cogolf.sandbox import Sandbox
from tests.conftest import make_config
from tests.fakes import ScriptedSource


def play(tmp_path, **overrides):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = make_config(holes=3, seed=7, **overrides)
    writer = ReplayWriter(config, config.seed)
    engine = Engine(config, [ScriptedSource("literalist"),
                             ScriptedSource("pedant")],
                    Sandbox(call_cpu_seconds=1.0, batch_seconds=8.0),
                    seed=config.seed,
                    on_event=writer.append_event, on_hole=writer.append_hole)
    result = asyncio.run(engine.run())
    doc = results_doc(config, result)
    results_path = tmp_path / "results.json"
    replay_path = tmp_path / "replay.json"
    results_path.write_text(json.dumps(doc, indent=2) + "\n")
    replay_path.write_bytes(writer.finalize(doc))
    return doc, results_path, replay_path


def test_a_three_hole_match_writes_both_artifacts(tmp_path):
    doc, results_path, replay_path = play(tmp_path)

    assert doc["reason"] == "complete"
    assert doc["holes_played"] == 3
    assert doc["scores"][0] + doc["scores"][1] == 0
    assert len(doc["hole_scores"][0]) == len(doc["hole_scores"][1]) == 3
    assert doc["seed"] == 7 and doc["deck_version"] == "core-1"

    assert results_path.exists() and replay_path.exists()
    replay = json.loads(replay_path.read_bytes().decode("utf-8"))
    assert replay["result"] == doc
    assert replay["names"] == ["bot-0", "bot-1"]
    assert replay["aliases"] == ["Ash", "Basil"]
    assert len(replay["holes"]) == 3
    assert "tokens" not in replay["config"]

    parsed = Replay.parse(replay_path.read_bytes())
    assert parsed.result["scores"] == doc["scores"]
    assert len(parsed.events) > 3 * 10


def test_the_match_is_a_real_contest_not_a_null_match(tmp_path):
    """The two baselines diverge from the reference on different clauses, so
    a scripted-vs-scripted episode really does breach in both directions and
    really does collect illegal shots — which is what the certification
    fixture and the CI smoke depend on."""
    doc, _results, _replay = play(tmp_path)
    assert sum(doc["breaches"]) > 0
    assert doc["breaches"][0] > 0 and doc["breaches"][1] > 0
    assert sum(doc["illegal_tests"]) > 0, "the pedant fires illegal shots"
    assert doc["illegal_tests"][0] == 0, "every literalist shot is legal"
    assert sum(doc["par_fails"]) > 0, "the hidden audit bites"
    assert doc["fallbacks"] == [0, 0]
    assert doc["tests_fired"][0] == 15


def test_the_same_seed_replays_the_same_match(tmp_path):
    first, _r, _p = play(tmp_path / "a")
    second, _r2, _p2 = play(tmp_path / "b")
    assert first["scores"] == second["scores"]
    assert first["hole_scores"] == second["hole_scores"]


def test_a_fallback_seat_still_finishes_the_match(tmp_path):
    config = make_config(holes=2, seed=3)
    writer = ReplayWriter(config, config.seed)
    silent = ScriptedSource("literalist", silent_holes=(1, 2))
    engine = Engine(config, [silent, ScriptedSource("pedant")],
                    Sandbox(call_cpu_seconds=1.0, batch_seconds=8.0),
                    seed=config.seed,
                    on_event=writer.append_event, on_hole=writer.append_hole)
    result = asyncio.run(engine.run())
    doc = results_doc(config, result)
    assert doc["reason"] == "complete" and doc["holes_played"] == 2
    assert doc["fallbacks"] == [2, 0]
    assert doc["scores"][0] + doc["scores"][1] == 0
    replay = json.loads(writer.finalize(doc).decode("utf-8"))
    fallbacks = [seat["fallback"] for hole in replay["holes"]
                 for seat in hole["seats"] if seat["fallback"]]
    assert len(fallbacks) == 2
    assert all(f == {"cause": "timeout", "baseline": "literalist"}
               for f in fallbacks)
