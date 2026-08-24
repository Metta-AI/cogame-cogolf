"""The aiohttp server: routes, tokens, the shutdown grace and the two
client pages the certification runner probes BEFORE the player pods start."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cogame_cogolf import contract, server as server_module
from cogame_cogolf.replay import ReplayWriter
from cogame_cogolf.results import EpisodeResult, SeatOutcome, results_doc
from cogame_cogolf.server import GameServer, make_replay_app
from tests.conftest import make_config


@pytest.fixture
async def game():
    config = make_config()
    game = GameServer(config)
    async with TestClient(TestServer(game.make_app())) as client:
        yield game, client


async def test_healthz(game):
    _game, client = game
    resp = await client.get("/healthz")
    assert resp.status == 200 and (await resp.json())["status"] == "ok"


async def test_client_pages_serve_without_opening_a_player_socket(game):
    """cogame-lantern 0.1.1: the episode runner probes /healthz,
    /client/player?slot=&token=, a bad-token websocket and /client/global
    BEFORE starting the player pods. All must be real pages, and neither
    may claim the seat."""
    g, client = game
    resp = await client.get("/client/global")
    assert resp.status == 200 and "text/html" in resp.headers["Content-Type"]
    assert "cogame-cogolf" in await resp.text()
    resp = await client.get("/client/player?slot=0&token=token-0")
    assert resp.status == 200 and "text/html" in resp.headers["Content-Type"]
    assert not g.seats[0].connected and not g.seats[0].ever_connected


async def test_a_bad_token_or_slot_is_403(game):
    _g, client = game
    for query in ("slot=0&token=nope", "slot=9&token=token-0", "slot=x",
                  "slot=0"):
        assert (await client.get(f"/client/player?{query}")).status == 403
    with pytest.raises(Exception):
        await client.ws_connect("/player?slot=0&token=wrong")


async def test_a_duplicate_live_socket_is_409(game):
    _g, client = game
    ws = await client.ws_connect("/player?slot=0&token=token-0")
    welcome = json.loads(await ws.receive_str())
    assert welcome["type"] == contract.MSG_WELCOME
    with pytest.raises(Exception):
        await client.ws_connect("/player?slot=0&token=token-0")
    await ws.close()


async def test_welcome_states_every_episode_parameter_and_no_real_name(game):
    g, client = game
    ws = await client.ws_connect("/player?slot=1&token=token-1")
    welcome = json.loads(await ws.receive_str())
    assert set(welcome) == set(contract.WELCOME_KEYS)
    assert welcome["protocol"] == contract.PROTOCOL
    assert welcome["slot"] == 1
    assert welcome["alias"] == "Basil" and welcome["opponent_alias"] == "Ash"
    assert set(welcome["rules"]) == set(contract.RULES_KEYS)
    assert set(welcome["episode"]) == set(contract.EPISODE_KEYS)
    assert welcome["episode"]["seed"] == g.seed
    assert welcome["episode"]["scoring"] == "zero_sum_v1"
    assert "solve" in welcome["api_docs"] and len(welcome["api_docs"]) > 2000
    blob = json.dumps(welcome)
    assert "bot-0" not in blob and "bot-1" not in blob
    await ws.close()


async def test_global_is_broadcast_only_and_opens_with_a_status_snapshot(game):
    g, client = game
    ws = await client.ws_connect("/global")
    status = json.loads(await ws.receive_str())
    assert set(status) == set(contract.STATUS_KEYS)
    assert status["aliases"] == list(contract.ALIASES)
    assert status["names"] == ["bot-0", "bot-1"]
    assert status["done"] is False
    g._on_progress(1, [2, -2], None)
    progress = json.loads(await ws.receive_str())
    assert set(progress) == set(contract.PROGRESS_KEYS)
    assert progress["hole"] == 1 and progress["scores"] == [2, -2]
    await ws.close()


async def test_an_oversize_or_malformed_frame_is_a_cause_not_a_crash(game):
    g, client = game
    seat = g.seats[0]
    ws = await client.ws_connect("/player?slot=0&token=token-0")
    await ws.receive_str()   # welcome

    async def send_and_collect(raw: str):
        task = asyncio.create_task(seat.get_submission(
            1, {"type": contract.MSG_OBSERVATION, "hole": 1, "observation": {}},
            __import__("time").monotonic() + 2.0))
        await asyncio.sleep(0.05)
        await ws.send_str(raw)
        return await task

    message, cause = await send_and_collect("not json at all")
    assert message is None and cause == "malformed"
    big = json.dumps({"type": "submission", "hole": 1,
                      "impl": "x" * (contract.MAX_MESSAGE_BYTES + 10)})
    message, cause = await send_and_collect(big)
    assert message is None and cause == "oversize"
    message, cause = await send_and_collect(json.dumps(
        {"type": "submission", "hole": 1, "impl": "def solve(x):\n    return x\n",
         "tests": [], "note": ""}))
    assert cause is None and message["impl"].startswith("def solve")
    await ws.close()


async def test_a_wrong_hole_reply_is_counted_and_the_hole_keeps_waiting(game):
    import time as _time
    g, client = game
    seat = g.seats[0]
    ws = await client.ws_connect("/player?slot=0&token=token-0")
    await ws.receive_str()
    task = asyncio.create_task(seat.get_submission(
        2, {"type": contract.MSG_OBSERVATION, "hole": 2, "observation": {}},
        _time.monotonic() + 1.0))
    await asyncio.sleep(0.05)
    await ws.send_str(json.dumps({"type": "submission", "hole": 7,
                                  "impl": "def solve(x):\n    return x\n"}))
    message, cause = await task
    assert message is None and cause == "timeout"
    assert seat.wrong_hole_count == 1
    await ws.close()


async def test_healthz_and_global_answer_during_the_shutdown_grace(game):
    """The certification runner pings /global AFTER the player pods start,
    so a short episode must keep answering for a bounded grace."""
    g, client = game
    assert server_module.SHUTDOWN_GRACE_SECONDS >= 20.0
    seats = (SeatOutcome(hole_scores=[1]), SeatOutcome(hole_scores=[-1]))
    doc = results_doc(g.config, EpisodeResult(
        seats=seats, reason="complete", wall_clock_seconds=1.0,
        holes_played=1, seed=g.seed, deck_version="core-1"))
    g.results_doc = doc
    await g._broadcast_done(doc)
    assert (await client.get("/healthz")).status == 200
    ws = await client.ws_connect("/global")
    status = json.loads(await ws.receive_str())
    assert status["done"] is True and status["result"]["scores"] == [1, -1]
    await ws.close()


async def test_replay_mode_serves_the_document_and_a_page():
    config = make_config()
    writer = ReplayWriter(config, 7)
    writer.append_event({"kind": "hole_start", "hole": 1, "spec_key": "median",
                         "title": "Median", "prompt_head": "…"})
    seats = (SeatOutcome(hole_scores=[0]), SeatOutcome(hole_scores=[0]))
    doc = results_doc(config, EpisodeResult(
        seats=seats, reason="complete", wall_clock_seconds=1.0, holes_played=1,
        seed=7, deck_version="core-1"))
    blob = writer.finalize(doc)
    app = make_replay_app(blob, viewer_dist=None)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/healthz")).status == 200
        resp = await client.get("/replay-data")
        assert resp.status == 200
        assert (await resp.json())["format"] == "cogame-cogolf-replay"
        page = await client.get("/client/replay")
        assert page.status == 200


def test_the_module_entry_point_exists():
    assert callable(server_module.main)
    assert "THE LEGALITY GATE" in server_module.API_DOCS
    assert "THE SCORE" in server_module.API_DOCS
    assert web is not None
