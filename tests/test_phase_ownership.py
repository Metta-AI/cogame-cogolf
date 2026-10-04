"""Nested policy phases share the process's single termination deadline."""

import asyncio

import pytest
from cogame_cogolf import lifecycle


async def test_nested_phase_does_not_extend_stop_or_forget_unsettled_worker():
    class RefusesCancellation(asyncio.Future):
        def cancel(self, msg=None):
            return False

    blocked = RefusesCancellation()
    entered = asyncio.Event()
    parent = lifecycle.Owner()
    token = lifecycle.current_owner.set(parent)
    worker = None

    async def callback():
        entered.set()
        return await blocked

    try:
        with pytest.raises(lifecycle.OwnershipUnsettled):
            async with lifecycle.own_until(asyncio.get_running_loop().time() + 100):
                worker = lifecycle.owned_task(callback())
                await entered.wait()
                parent.deadline = asyncio.get_running_loop().time()
                assert lifecycle.cleanup_deadline() == parent.deadline
        assert worker in parent.tasks and not worker.done()
        blocked.set_result(None)
        await asyncio.wait({worker}, timeout=1)
        assert worker.done()
    finally:
        lifecycle.current_owner.reset(token)
