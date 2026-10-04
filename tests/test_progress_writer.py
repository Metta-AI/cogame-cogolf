"""A stalled native progress sink cannot create an unlimited queue or false ACK."""

import asyncio
from uuid import uuid4

from cogame_cogolf.engine import Engine
from cogame_cogolf.private_window import PrivateWindow

from players.native import Attempt
from players.progress import ProgressWriter
from tests.conftest import make_config
from tests.fakes import FakeSandbox, ScriptedSource


async def test_latest_snapshot_coalesces_and_unsettled_send_stays_owned():
    class RefusesCancellation(asyncio.Future):
        def cancel(self, msg=None):
            return False

    blocked = RefusesCancellation()
    entered = asyncio.Event()
    received = []

    async def send(packet):
        entered.set()
        await blocked
        received.append(packet)

    config = make_config()
    engine = Engine(config, [ScriptedSource(), ScriptedSource()], FakeSandbox())
    window = PrivateWindow(
        slot=0,
        hole=1,
        retry=False,
        profile=config.native_profiles[0],
        observation=engine._observation_message(
            1, engine.deck["median"], 0, retry=False
        )["observation"],
    )
    attempt = Attempt(slot=0, stage="submission", request=window.native_request())
    writer = ProgressWriter(uuid4(), send)
    await writer.update(attempt)
    await entered.wait()
    for length in range(1000):
        attempt.response_body_b64 = str(length)
        await writer.update(attempt)
    assert len(writer.latest) == 1
    blocked.set_result(None)
    assert await writer.finish(asyncio.get_running_loop().time() + 1)
    assert len(received) == 2
    assert received[-1].attempt.response_body_b64 == "999"

    blocked = RefusesCancellation()
    entered.clear()
    writer = ProgressWriter(uuid4(), send)
    await writer.update(attempt)
    await entered.wait()
    await writer.update(attempt)
    assert not await writer.finish(asyncio.get_running_loop().time())
    assert not writer.task.done()
    assert writer.latest[attempt.attempt_id].response_body_b64 == "999"
    blocked.set_result(None)
    await asyncio.wait({writer.task}, timeout=1)
    assert writer.task.cancelled()
    assert writer.latest[attempt.attempt_id].response_body_b64 == "999"
    assert writer.inflight is not None
