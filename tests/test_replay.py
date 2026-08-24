"""The replay document: strict UTF-8, the structural contract, and at least
one event of every kind in the vocabulary."""

from __future__ import annotations

import asyncio
import json

import pytest
from cogame_cogolf import contract
from cogame_cogolf.engine import Engine
from cogame_cogolf.replay import (FORMAT, VERSION, Replay, ReplayError,
                                  ReplayWriter)
from cogame_cogolf.results import EpisodeResult, SeatOutcome, results_doc
from cogame_cogolf.sandbox import Sandbox
from tests.conftest import make_config
from tests.fakes import ScriptedSource

# Every hostile string the wire can carry: emoji (4-byte), CJK, a lone
# surrogate, and strings sitting exactly on each cap.
EMOJI = "🏌️‍♀️"
CJK = "測試用例"
LONE_SURROGATE = "\ud800"


def nasty_reply(hole, payload):
    return {
        "type": contract.MSG_SUBMISSION,
        "hole": hole,
        "impl": f"# {EMOJI} {CJK}\ndef solve(*args):\n    return None\n",
        "tests": [
            {"name": "n" * (contract.MAX_TEST_NAME_CHARS - 1) + EMOJI,
             "args": [[]], "expect": None,
             "why": CJK * 40 + LONE_SURROGATE},
            {"name": LONE_SURROGATE + CJK, "args": [[1]], "expect": 1,
             "why": EMOJI * 30},
        ],
        "note": ("z" * (contract.MAX_NOTE_CHARS - 1)) + EMOJI + LONE_SURROGATE,
    }


def build_replay(**overrides):
    config = make_config(holes=2, seed=5, **overrides)
    writer = ReplayWriter(config, config.seed)
    engine = Engine(config,
                    [ScriptedSource("literalist", reply=nasty_reply),
                     ScriptedSource("pedant")],
                    Sandbox(call_cpu_seconds=1.0, batch_seconds=8.0),
                    seed=config.seed,
                    on_event=writer.append_event, on_hole=writer.append_hole)
    result = asyncio.run(engine.run())
    doc = results_doc(config, result)
    return writer.finalize(doc), doc


def test_the_bytes_parse_under_a_strict_utf8_json_reader():
    """No error handler, no surrogatepass: the bytes must be clean UTF-8.
    A byte-boundary truncation anywhere in the writer fails right here."""
    blob, _doc = build_replay()
    parsed = json.loads(blob.decode("utf-8"))     # strict on both sides
    assert parsed["format"] == FORMAT
    text = blob.decode("utf-8")
    assert LONE_SURROGATE not in text
    assert "\ufffd" in text, "the lone surrogate became U+FFFD"


def test_every_recorded_string_is_within_its_cap():
    blob, _doc = build_replay()
    doc = json.loads(blob.decode("utf-8"))
    for hole in doc["holes"]:
        for seat in hole["seats"]:
            assert len(seat["note"]) <= contract.MAX_NOTE_CHARS
            assert len(seat["impl"]) <= contract.MAX_IMPL_CHARS
            for test in seat["tests"]:
                assert len(test["name"]) <= contract.MAX_TEST_NAME_CHARS
                assert len(test["why"]) <= contract.MAX_WHY_CHARS
                assert len(test["observed"]) <= contract.MAX_OBSERVED_CHARS


def test_the_structural_contract():
    blob, results = build_replay()
    doc = json.loads(blob.decode("utf-8"))
    assert doc["version"] == VERSION
    assert doc["game_version"] and doc["protocol"] == contract.PROTOCOL
    assert doc["names"] == ["bot-0", "bot-1"]
    assert doc["aliases"] == list(contract.ALIASES)
    assert doc["seed"] == 5 and doc["deck"] == "core"
    assert doc["deck_version"] == "core-1"
    assert "tokens" not in doc["config"]
    assert doc["config"]["num_agents"] == 2
    assert doc["result"] == results
    assert len(doc["holes"]) == 2
    for hole in doc["holes"]:
        assert hole["spec"]["prompt"] and hole["spec"]["ambiguity"]
        assert len(hole["seats"]) == 2
        assert hole["hole_score"][0] == -hole["hole_score"][1]
        for slot, seat in enumerate(hole["seats"]):
            assert seat["slot"] == slot
            assert seat["par_total"] == 4
            assert isinstance(seat["broken"], bool)


def test_at_least_one_event_of_every_kind():
    blob, _doc = build_replay()
    doc = json.loads(blob.decode("utf-8"))
    kinds = {event["kind"] for event in doc["events"]}
    assert kinds == set(contract.EVENT_KINDS)
    outcomes = {e["outcome"] for e in doc["events"]
                if e["kind"] == "test_verdict"}
    assert outcomes <= set(contract.SHOT_OUTCOMES)
    assert "breach" in outcomes


def test_the_writer_refuses_an_unknown_event_kind():
    writer = ReplayWriter(make_config(), 1)
    with pytest.raises(ValueError):
        writer.append_event({"kind": "explosion"})
    with pytest.raises(ValueError):
        writer.append_hole({"hole": 1})


def test_the_parser_rejects_corrupt_documents():
    blob, _doc = build_replay()
    good = json.loads(blob.decode("utf-8"))

    def broken(mutate):
        d = json.loads(json.dumps(good))
        mutate(d)
        return json.dumps(d).encode("utf-8")

    Replay.parse(blob)
    with pytest.raises(ReplayError):
        Replay.parse(b"{not json")
    with pytest.raises(ReplayError):
        Replay.parse(broken(lambda d: d.update(format="cogame-other-replay")))
    with pytest.raises(ReplayError):
        Replay.parse(broken(lambda d: d.update(version=99)))
    with pytest.raises(ReplayError):
        Replay.parse(broken(lambda d: d.pop("aliases")))
    with pytest.raises(ReplayError):
        Replay.parse(broken(lambda d: d["events"].append({"kind": "boom"})))
    with pytest.raises(ReplayError):
        Replay.parse(broken(lambda d: d["holes"][0].pop("cumulative")))


def test_a_replay_with_no_holes_still_writes():
    """A harness fault before the first hole still produces a parseable
    document (the viewer shows the endcard, not a broken page)."""
    config = make_config()
    writer = ReplayWriter(config, 1)
    seats = (SeatOutcome(), SeatOutcome())
    doc = results_doc(config, EpisodeResult(
        seats=seats, reason="harness_fault", wall_clock_seconds=0.5,
        holes_played=0, seed=1, deck_version="core-1"))
    blob = writer.finalize(doc)
    parsed = Replay.parse(blob)
    assert parsed.holes == [] and parsed.result["reason"] == "harness_fault"
