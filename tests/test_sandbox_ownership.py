"""Actual child-process ownership and retained partial sandbox output."""

import asyncio
from pathlib import Path

import pytest
from cogame_cogolf import lifecycle
from cogame_cogolf.sandbox import Sandbox


async def test_cancel_joins_actual_child_before_removing_workdir(monkeypatch):
    spawned = asyncio.Event()
    children = []
    workdirs = []
    create = asyncio.create_subprocess_exec

    async def observe(*args, **kwargs):
        process = await create(*args, **kwargs)
        children.append(process)
        workdirs.append(Path(kwargs["cwd"]))
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    sandbox = Sandbox(call_cpu_seconds=5, batch_seconds=5)
    task = asyncio.create_task(
        sandbox.run(
            "def solve(x):\n    while True:\n        pass\n",
            [{"id": 0, "args": [1]}],
        )
    )
    await spawned.wait()
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None
    assert not workdirs[0].exists()


async def test_unsettled_reader_retains_private_spool_until_actual_join(monkeypatch):
    class RefusesCancellation(asyncio.Future):
        def cancel(self, msg=None):
            return False

    blocked = RefusesCancellation()
    spawned = asyncio.Event()
    readers = []
    workdirs = []
    create = asyncio.create_subprocess_exec

    class UnsettledReader:
        async def read(self, size):
            readers.append(asyncio.current_task())
            return await blocked

    async def observe(*args, **kwargs):
        process = await create(*args, **kwargs)
        process.stdout = UnsettledReader()
        workdirs.append(Path(kwargs["cwd"]))
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    monkeypatch.setattr(lifecycle, "CLEANUP_SECONDS", 0.05)
    task = asyncio.create_task(
        Sandbox(batch_seconds=0.1).run(
            "def solve(x):\n    return x\n",
            [{"id": 0, "args": [1]}],
        )
    )
    await spawned.wait()
    with pytest.raises(lifecycle.OwnershipUnsettled):
        await task
    assert workdirs[0].exists()
    assert (workdirs[0] / "stdout.received").read_bytes() == b""
    assert readers and not readers[0].done()
    blocked.set_result(b"")
    await asyncio.wait(set(readers), timeout=0.5)
    assert all(reader.done() for reader in readers)
    assert workdirs[0].exists()  # Unresolved evidence is never erased retrospectively.


async def test_output_ceiling_joins_child_and_keeps_prior_actual_results(monkeypatch):
    from cogame_cogolf import sandbox

    children = []
    create = asyncio.create_subprocess_exec

    async def observe(*args, **kwargs):
        process = await create(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    result = await Sandbox(batch_seconds=5).run(
        "def solve(x):\n"
        "    if x == 0: return 'first'\n"
        f"    print('x' * {sandbox.MAX_RECEIVED_STREAM_BYTES + 1})\n"
        "    while True: pass\n",
        [{"id": 0, "args": [0]}, {"id": 1, "args": [1]}],
    )
    assert result.get(0).ok and result.get(0).value == "first"
    assert result.broken == "sandbox received stream exceeds 4 MiB limit"
    assert not result.get(1).ok
    assert children[0].returncode is not None
