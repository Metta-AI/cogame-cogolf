"""Engine actions, failed ownership and native evidence remain private and distinct."""

import json

import pytest
from cogame_cogolf.engine import Engine
from cogame_cogolf.journal import Complete, Journal, SourceIdentity
from cogame_cogolf.private_window import Admission
from cogame_cogolf.results import results_doc
from cogame_cogolf.version import GAME_VERSION

from tests.conftest import make_config
from tests.fakes import FakeSandbox
from tools.export_posttrain import RecordingSource


async def test_teacher_actual_engine_parser_effects_and_private_complete(tmp_path):
    config = make_config()
    path = tmp_path / "complete.jsonl"
    journal = Journal(
        path,
        SourceIdentity(
            episode_id="teacher-test",
            source_revision="a" * 40,
            game_version=GAME_VERSION,
        ),
        6,
        [Admission(0), Admission(1)],
        ["literalist", "pedant"],
        config,
    )
    engine = Engine(
        config,
        [
            RecordingSource(name, slot, journal)
            for slot, name in enumerate(("literalist", "pedant"))
        ],
        FakeSandbox(),
        seed=6,
        on_window=journal.window,
        on_applied=journal.applied,
    )
    result = await engine.run()
    complete = journal.finish(results_doc(config, result), owners_joined=True)
    readback = Complete.model_validate_json(path.read_text())
    assert readback == complete
    assert len(complete.decisions) == config.holes * 2
    assert complete.episode.seed_family == "cogolf:6"
    assert sum(complete.episode.outcome["results"]["scores"]) == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert journal.spool_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="already finalized"):
        journal.finish(results_doc(config, result), owners_joined=True)
    for decision in complete.decisions:
        selected = next(
            a for a in decision.attempts if a.attempt_id == decision.selected_attempt_id
        )
        assert selected.origin == "teacher" and selected.accepted
        assert selected.parsed_action == decision.executed_action
        assert selected.prompt == decision.prompt
        assert (
            selected.request is None
            and selected.model is None
            and selected.response_headers is None
        )
        assert (
            decision.observation["engine_effects"]["impl"]
            == decision.executed_action["impl"]
        )
    assert "train" not in {p.stem for p in tmp_path.iterdir()}


def test_unjoined_writer_preserves_writable_partial_without_final(tmp_path):
    config = make_config()
    path = tmp_path / "complete.jsonl"
    journal = Journal(
        path,
        SourceIdentity(
            episode_id="unjoined", source_revision="a" * 40, game_version=GAME_VERSION
        ),
        6,
        [Admission(0), Admission(1)],
        ["literalist", "pedant"],
        config,
    )
    journal.append(
        "received_partial",
        {"response_body_b64": "/w==", "response_reader_joined": False},
    )
    with pytest.raises(ValueError, match="actual writer/player joins"):
        journal.finish({}, owners_joined=False)
    assert not path.exists() and not journal.spool.closed and not journal.sealed
    journal.append("late_owned_bytes", {"response_body_b64": "/wAB"})
    rows = [json.loads(line) for line in journal.spool_path.read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["received_partial", "late_owned_bytes"]
    journal.spool.close()  # Test owns the otherwise intentionally retained live writer.


async def test_harness_fault_keeps_joined_private_terminal_and_hides_error(
    monkeypatch, tmp_path, capsys
):
    from cogame_cogolf import server as server_module
    from cogame_cogolf.sandbox import SandboxError

    path = tmp_path / "fault.jsonl"
    monkeypatch.setenv("COGAME_SAVE_TRAJECTORY_URI", path.as_uri())
    monkeypatch.setenv("COWORLD_EPISODE_ID", "fault-test")
    monkeypatch.setenv("COWORLD_SOURCE_REVISION", "a" * 40)
    monkeypatch.setenv("COWORLD_GAME_VERSION", "1.2.3")
    monkeypatch.setattr(server_module, "SHUTDOWN_GRACE_SECONDS", 0)

    async def fault(engine):
        raise SandboxError("private-source-sentinel")

    monkeypatch.setattr(Engine, "run", fault)
    game = server_module.GameServer(make_config())
    result = await game.run_episode()
    complete = Complete.model_validate_json(path.read_text())
    assert result.reason == "harness_fault"
    assert complete.episode.status == "truncated"
    assert complete.episode.game_version == "1.2.3"
    assert complete.episode.outcome["results"]["reason"] == "harness_fault"
    assert complete.decisions == []
    assert all(seat.sealed for seat in game.seats)
    assert game.journal.sealed and game.journal.spool.closed
    assert "private-source-sentinel" not in capsys.readouterr().err
