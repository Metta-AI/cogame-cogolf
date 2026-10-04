"""Both real WS seats must join before the ordinary sandbox game publishes."""

import asyncio

from aiohttp.test_utils import TestServer
from cogame_cogolf import server as server_module
from cogame_cogolf.server import GameServer

from players.client import play_episode
from players.scripted import ScriptedPolicy
from tests.conftest import make_config


async def test_two_actual_ws_baselines_install_score_and_join_before_done(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(server_module, "SHUTDOWN_GRACE_SECONDS", 0)
    config = make_config(hole_deadline_seconds=5, retry_deadline_seconds=4)
    path = tmp_path / "private.jsonl"
    monkeypatch.setenv("COGAME_SAVE_TRAJECTORY_URI", path.as_uri())
    monkeypatch.setenv("COWORLD_EPISODE_ID", "actual-ws-game")
    monkeypatch.setenv("COWORLD_SOURCE_REVISION", "a" * 40)
    monkeypatch.setenv("COWORLD_GAME_VERSION", "1.2.3")
    game = GameServer(config)
    async with TestServer(game.make_app()) as server:
        players = [
            asyncio.create_task(
                play_episode(
                    ScriptedPolicy(name),
                    str(
                        server.make_url(f"/player?slot={slot}&token=token-{slot}")
                    ).replace("127.0.0.1", "localhost"),
                )
            )
            for slot, name in enumerate(("literalist", "pedant"))
        ]
        engine = asyncio.create_task(game.run_episode())
        tasks = players + [engine]
        try:
            _done, pending = await asyncio.wait(tasks, timeout=30)
            assert not pending
            result = engine.result()
            assert result.holes_played == config.holes
            assert result.reason == "complete"
            assert sum(seat.score for seat in result.seats) == 0
            assert all(task.result() == game.results_doc for task in players)
            for seat in game.seats:
                assert seat.sealed and seat.stopped.is_set()
                assert len(seat.admission.accepted) == config.holes
                assert not seat.admission.has_unsettled_readers()
            assert game.engine.outcomes == result.seats
            assert game.journal.sealed and game.journal.spool.closed
            assert game.journal.identity.game_version == "1.2.3"
            assert (
                game.journal.configuration["rules_version"]
                == server_module.GAME_VERSION
            )
            assert path.stat().st_mode & 0o777 == 0o600
            assert len(game.journal.decisions) == config.holes * 2
            assert all(
                attempt.origin == "unknown"
                for decision in game.journal.decisions
                for attempt in decision.attempts
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            _, pending = await asyncio.wait(tasks, timeout=2)
            assert not pending


async def test_native_http_attempt_joins_exact_installed_control_and_private_journal(
    monkeypatch, tmp_path
):
    import base64
    import json
    from uuid import uuid4

    from aiohttp import web
    from cogame_cogolf.journal import Complete

    from players.llm_player import LLMPolicy
    from players.scripted import scripted_submission

    monkeypatch.setattr(server_module, "SHUTDOWN_GRACE_SECONDS", 0)
    config = make_config(hole_deadline_seconds=8, retry_deadline_seconds=4)
    path = tmp_path / "private.jsonl"
    replay = tmp_path / "replay.json"
    monkeypatch.setenv("COGAME_SAVE_TRAJECTORY_URI", path.as_uri())
    monkeypatch.setenv("COWORLD_EPISODE_ID", "native-http-engine")
    monkeypatch.setenv("COWORLD_SOURCE_REVISION", "a" * 40)
    monkeypatch.setenv("COWORLD_GAME_VERSION", "1.2.3")
    game = GameServer(config, save_replay_uri=replay.as_uri())
    calls = []
    bodies = []

    async def native(request):
        actual = await request.json()
        admission = game.seats[0].admission
        window = admission.windows[admission.active]
        assert actual == window.native_request().model_dump(
            mode="json", exclude_none=True
        )
        assert request.headers["X-Coworld-Player-Slot"] == "0"
        text = json.dumps(
            scripted_submission(
                "literalist", window.hole, window.observation.model_dump()
            )
        )
        body = (
            " \n"
            + json.dumps(
                {
                    "model": actual["model"],
                    "content": [
                        {"type": "thinking", "thinking": "private-thinking-sentinel"},
                        {"type": "text", "text": text},
                    ],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            )
            + "\n "
        )
        calls.append(actual)
        bodies.append(body)
        return web.Response(
            body=body.encode(),
            content_type="application/json",
            headers={"X-Softmax-LLM-Call-ID": str(uuid4())},
        )

    app = web.Application()
    app.router.add_post("/v1/messages", native)
    async with TestServer(app) as native_server, TestServer(game.make_app()) as server:
        monkeypatch.setenv("COWORLD_LLM_ENDPOINT", str(native_server.make_url("/")))
        monkeypatch.setenv(
            "COWORLD_PLAYER_WS_URL",
            str(server.make_url("/player?slot=0&token=token-0")),
        )
        tasks = [
            asyncio.create_task(
                play_episode(
                    policy,
                    str(server.make_url(f"/player?slot={slot}&token=token-{slot}")),
                )
            )
            for slot, policy in enumerate(
                (
                    LLMPolicy(
                        model="checkpoint:fixture",
                        timeout_seconds=3,
                        strategy="  private-strategy-sentinel\n",
                    ),
                    ScriptedPolicy("pedant"),
                )
            )
        ]
        tasks.append(asyncio.create_task(game.run_episode()))
        try:
            _done, pending = await asyncio.wait(tasks, timeout=30)
            assert not pending
            for task in tasks:
                task.result()
            complete = Complete.model_validate_json(path.read_text())
            assert len(calls) == config.holes
            selected = [
                next(a for a in d.attempts if a.attempt_id == d.selected_attempt_id)
                for d in complete.decisions
                if d.seat == "0"
            ]
            assert len(selected) == config.holes
            for decision, attempt, body in zip(
                [d for d in complete.decisions if d.seat == "0"], selected, bodies
            ):
                assert attempt.origin == "model" and attempt.accepted
                assert (
                    attempt.raw_response == body
                    and base64.b64decode(attempt.response_body_b64) == body.encode()
                )
                assert (
                    attempt.response_complete is True
                    and attempt.response_reader_joined is True
                )
                assert attempt.parsed_action == decision.executed_action
                assert attempt.platform_call_id is not None
                assert (
                    attempt.behavior_logprobs is None
                    and attempt.sampled_token_ids is None
                )
                assert "private-thinking-sentinel" not in attempt.response
            assert "private-thinking-sentinel" not in replay.read_text()
            assert "private-strategy-sentinel" not in replay.read_text()
            assert (
                complete.episode.outcome["runtime_configuration"]["native_profiles"][0][
                    "strategy"
                ]
                == "  private-strategy-sentinel\n"
            )
            assert all(call["model"] == "checkpoint:fixture" for call in calls)
            assert all(seat.sealed and seat.stopped.is_set() for seat in game.seats)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            _done, pending = await asyncio.wait(tasks, timeout=2)
            assert not pending
