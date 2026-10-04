"""Hostname resolution joins an actual process and never starts DNS threads."""

import asyncio
import socket

import pytest

from players.resolver import OwnedResolver


async def test_owned_dns_resolves_localhost_and_preserves_original_authority():
    before = set(asyncio.all_tasks())
    resolver = OwnedResolver()
    rows = await resolver.resolve("localhost", 3131, socket.AF_UNSPEC)
    assert rows
    assert all(row["hostname"] == "localhost" and row["port"] == 3131 for row in rows)
    assert {row["host"] for row in rows} <= {"127.0.0.1", "::1"}
    assert not (set(asyncio.all_tasks()) - before)
    await resolver.close()


async def test_owned_dns_error_is_loud_and_leaves_no_reader_or_process_owner():
    before = set(asyncio.all_tasks())
    resolver = OwnedResolver()
    with pytest.raises(RuntimeError, match="lookup process failed"):
        await resolver.resolve("invalid host with spaces", 3131)
    assert not (set(asyncio.all_tasks()) - before)
    await resolver.close()


async def test_cancelled_dns_wait_kills_and_joins_only_owned_lookup(monkeypatch):
    import os
    import threading

    from players import resolver as module

    monkeypatch.setattr(module, "LOOKUP", "import time; time.sleep(20)")
    create = asyncio.create_subprocess_exec
    children = []
    launched = asyncio.Event()

    async def observe(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        launched.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    before = set(threading.enumerate())
    lookup = asyncio.create_task(OwnedResolver().resolve("localhost", 3131))
    await launched.wait()
    assert os.getpriority(os.PRIO_PROCESS, children[0].pid) == os.getpriority(
        os.PRIO_PROCESS, 0
    )
    lookup.cancel()
    _done, pending = await asyncio.wait({lookup}, timeout=2)
    assert not pending and lookup.cancelled()
    assert children[0].returncode is not None
    assert set(threading.enumerate()) == before


async def test_cancel_during_process_acquisition_waits_for_actual_child_before_join(
    monkeypatch,
):
    from players import resolver as module

    monkeypatch.setattr(module, "LOOKUP", "import time; time.sleep(20)")
    create = asyncio.create_subprocess_exec
    child_created = asyncio.Event()
    release_acquisition = asyncio.Event()
    children = []

    async def held_creation(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        child_created.set()
        await release_acquisition.wait()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", held_creation)
    lookup = asyncio.create_task(OwnedResolver().resolve("localhost", 3131))
    await child_created.wait()
    lookup.cancel()
    await asyncio.sleep(0)
    assert not lookup.done() and children[0].returncode is None
    release_acquisition.set()
    _done, pending = await asyncio.wait({lookup}, timeout=2)
    assert not pending and lookup.cancelled()
    assert children[0].returncode is not None
