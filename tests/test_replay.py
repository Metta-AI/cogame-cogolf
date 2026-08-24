"""The replay document: strict UTF-8, the structural contract, at least one
event of every kind in the vocabulary, and — the checklist-2 gate — that
folding `events[]` re-derives the recorded per-hole state, beat by beat."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest
from cogame_cogolf import contract
from cogame_cogolf.engine import Engine
from cogame_cogolf.replay import (FORMAT, VERSION, Replay, ReplayError,
                                  ReplayWriter)
from cogame_cogolf.results import EpisodeResult, SeatOutcome, results_doc
from cogame_cogolf.sandbox import Sandbox
from tests.conftest import REPO_ROOT, make_config
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


# --------------------------------------------------------------------------
# The re-derivation: events[] folded == holes[] recorded, beat by beat
#
# The viewer NEVER reads a parallel recording of derivable state: the page
# derives everything it shows from RD.stateAt(replay, beat)
# (client/replay_broadcast.html:700) and the wasm board folds the same beats
# again (replay-viewer/cogolf_replay.nim sceneAt). The engine builds the
# beats (engine.py _play_hole) and the hole record (engine.py _hole_record)
# in two separate places, so drift between them would silently change what
# every viewer shows. These two tests are the tripwire: the fold below
# mirrors client/replay_doc.js stateAt() line for line, and the second test
# runs the viewer's own stateAt() under node over the same episode.
# --------------------------------------------------------------------------

# The keys the hole record keeps for each shot (engine.py _hole_record); a
# test_verdict event carries these plus hole/slot/target_slot/kind.
TEST_KEYS = ("idx", "name", "args", "expect", "why", "legal", "legal_reason",
             "outcome", "observed")


def fold(doc):
    """Replay the beats, yielding (index, event, state) after each one.

    A transcription of client/replay_doc.js stateAt(): hole_start resets the
    hole, submission records the fallback, test_verdict appends a shot,
    par_result sets the audit count, hole_score sets the running score and
    episode_end ends the match.
    """
    state = {"hole": 0, "hole_index": -1, "cumulative": [0, 0],
             "shots": [[], []], "par": [None, None],
             "fallback": [None, None], "done": False}
    for index, event in enumerate(doc["events"]):
        kind = event["kind"]
        if kind == "hole_start":
            state["hole"] = event["hole"]
            state["hole_index"] = next(
                (i for i, h in enumerate(doc["holes"])
                 if h["hole"] == event["hole"]), -1)
            state["shots"] = [[], []]
            state["par"] = [None, None]
            state["fallback"] = [None, None]
        elif kind == "submission":
            state["fallback"][event["slot"]] = event["fallback"]
        elif kind == "test_verdict":
            state["shots"][event["slot"]].append(event)
        elif kind == "par_result":
            state["par"][event["slot"]] = event["par_fails"]
        elif kind == "hole_score":
            state["cumulative"] = list(event["cumulative"])
        elif kind == "episode_end":
            state["done"] = True
        yield index, event, state


def test_folding_the_events_reproduces_the_recorded_per_hole_state():
    """Frame by frame: at every beat the folded state agrees with holes[],
    and at each hole's last beat it equals that hole's record exactly."""
    blob, results = build_replay()
    doc = json.loads(blob.decode("utf-8"))
    assert len(doc["holes"]) == 2, "two holes: the running score must carry"

    scored = 0
    previous_cumulative = [0, 0]
    for index, event, state in fold(doc):
        if state["hole_index"] < 0:
            continue                      # before the first hole_start
        record = doc["holes"][state["hole_index"]]
        assert record["hole"] == state["hole"]
        for slot, seat in enumerate(record["seats"]):
            fired = state["shots"][slot]
            # every shot so far is this seat's next recorded shot, in order
            assert len(fired) <= len(seat["tests"]), (index, slot)
            for shot, recorded in zip(fired, seat["tests"]):
                assert {k: shot[k] for k in TEST_KEYS} == recorded, \
                    (index, slot, shot["idx"])
            # not yet seen (None) or already agreeing with the record
            assert state["par"][slot] in (None, seat["par_fails"])
            assert state["fallback"][slot] in (None, seat["fallback"])
        # the running score is the PREVIOUS hole's until this hole is scored
        if event["kind"] == "hole_score":
            scored += 1
            assert event["hole"] == record["hole"]
            assert event["score"] == record["hole_score"]
            assert state["cumulative"] == record["cumulative"]
            # the hole is fully re-derived at its last beat
            for slot, seat in enumerate(record["seats"]):
                assert [{k: s[k] for k in TEST_KEYS}
                        for s in state["shots"][slot]] == seat["tests"]
                assert state["par"][slot] == seat["par_fails"]
                assert state["fallback"][slot] == seat["fallback"]
            previous_cumulative = list(record["cumulative"])
        else:
            assert state["cumulative"] == previous_cumulative, index

    assert scored == len(doc["holes"])
    assert state["done"], "the fold never saw episode_end"
    assert state["cumulative"] == results["scores"] == \
        doc["holes"][-1]["cumulative"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_viewers_own_fold_agrees_with_the_recorded_holes(tmp_path):
    """The same check through the code the page actually runs:
    client/replay_doc.js stateAt() at each hole's last beat."""
    blob, _results = build_replay()
    path = tmp_path / "episode.replay"
    path.write_bytes(blob)
    doc = json.loads(blob.decode("utf-8"))
    replay_doc_js = REPO_ROOT / "client" / "replay_doc.js"
    script = f"""
const RD = require({str(replay_doc_js)!r});
const fs = require('fs');
const doc = RD.parseReplay(fs.readFileSync({str(path)!r}, 'utf8'));
const KEYS = {json.dumps(list(TEST_KEYS))};
const pick = (ev) => {{ const o = {{}}; for (const k of KEYS) o[k] = ev[k]; return o; }};
const out = [];
doc.events.forEach((ev, i) => {{
  if (ev.kind !== 'hole_score') return;
  const st = RD.stateAt(doc, i);
  out.push({{
    hole: st.hole, hole_index: st.holeIndex, cumulative: st.cumulative,
    par: st.par, fallback: st.fallback,
    shots: st.shots.map((seat) => seat.map(pick)),
  }});
}});
const last = RD.stateAt(doc, doc.events.length - 1);
console.log(JSON.stringify({{holes: out, final_cumulative: last.cumulative,
                            done: last.done}}));
"""
    proc = subprocess.run([shutil.which("node"), "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    folded = json.loads(proc.stdout)
    states = folded["holes"]
    assert len(states) == len(doc["holes"])
    for k, (state, record) in enumerate(zip(states, doc["holes"])):
        assert state["hole"] == record["hole"] and state["hole_index"] == k
        assert state["cumulative"] == record["cumulative"]
        assert state["par"] == [s["par_fails"] for s in record["seats"]]
        assert state["fallback"] == [s["fallback"] for s in record["seats"]]
        assert state["shots"] == [s["tests"] for s in record["seats"]]
    assert folded["done"] is True, "the last beat is not the end"
    assert folded["final_cumulative"] == doc["result"]["scores"]
