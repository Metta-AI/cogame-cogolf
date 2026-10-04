"""Real socket stop keeps one deadline when a policy suppresses cancellation."""

import asyncio
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cogame_cogolf import contract, lifecycle
from cogame_cogolf.engine import Engine
from cogame_cogolf.private_window import ObservationPacket, Stop

from players.client import Policy, play_episode
from tests.conftest import make_config
from tests.fakes import FakeSandbox, ScriptedSource


async def test_uncooperative_policy_never_acks_or_restarts_cleanup_budget():
    class RefusesCancellation(asyncio.Future):
        def cancel(self, msg=None):
            return False

    blocked = RefusesCancellation()
    entered = asyncio.Event()
    client_packets = []
    clients = set()
    cfg = make_config()
    engine = Engine(cfg, [ScriptedSource(), ScriptedSource()], FakeSandbox())
    window = ObservationPacket.model_validate(
        engine._observation_message(
            1,
            engine.deck["median"],
            0,
            retry=False,
        )
    )

    class SuppressesStop(Policy):
        async def submission(self, hole, observation):
            entered.set()
            await blocked
            return {"impl": "def solve(xs): return 0", "tests": [], "note": ""}

    async def seat(request):
        ws = web.WebSocketResponse(max_msg_size=contract.MAX_PRIVATE_FRAME_BYTES)
        await ws.prepare(request)
        clients.add(ws)
        await ws.send_json(
            {
                "type": "welcome",
                "protocol": contract.PROTOCOL,
                "slot": 0,
                "native_profile": window.profile.model_dump(),
            }
        )
        registration = (await ws.receive()).json()
        assert registration["type"] == "ready" and registration["slot"] == 0
        await ws.send_str(window.model_dump_json())
        await entered.wait()
        await ws.send_str(
            Stop(stop_id=uuid4(), deadline_seconds=0.05).model_dump_json()
        )
        async for message in ws:
            if message.type is web.WSMsgType.TEXT:
                client_packets.append(message.json())
        return ws

    app = web.Application()
    app.router.add_get("/player", seat)
    before = set(asyncio.all_tasks())
    async with TestServer(app) as server:
        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(lifecycle.OwnershipUnsettled):
                await play_episode(SuppressesStop(), str(server.make_url("/player")))
            assert asyncio.get_running_loop().time() - started < 0.3
            assert client_packets == []
            pending = set(asyncio.all_tasks()) - before
            assert any(not task.done() for task in pending)
        finally:
            if not blocked.done():
                blocked.set_result(None)
            for ws in clients:
                await ws.close()
            pending = set(asyncio.all_tasks()) - before
            if pending:
                _done, unresolved = await asyncio.wait(pending, timeout=1)
                assert not unresolved
                for task in pending:
                    if not task.cancelled():
                        error = task.exception()
                        assert error is None or isinstance(
                            error, lifecycle.OwnershipUnsettled
                        )
        assert (
            client_packets == []
        )  # Released late work never installs an action or ACK.
