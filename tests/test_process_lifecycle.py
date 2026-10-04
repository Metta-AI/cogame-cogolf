"""The actual replay executable joins its listener under repeated process signals."""

import asyncio
import os
import signal
import sys

from cogame_cogolf.engine import Engine
from cogame_cogolf.replay import ReplayWriter
from cogame_cogolf.results import results_doc

from tests.conftest import REPO_ROOT, make_config
from tests.fakes import FakeSandbox, ScriptedSource


async def test_replay_process_repeated_signals_join_listener_and_exit(tmp_path):
    config = make_config()
    writer = ReplayWriter(config, config.seed)
    result = await Engine(
        config,
        [ScriptedSource("literalist"), ScriptedSource("pedant")],
        FakeSandbox(),
        seed=config.seed,
        on_event=writer.append_event,
        on_hole=writer.append_hole,
    ).run()
    replay = tmp_path / "replay.json"
    replay.write_bytes(writer.finalize(results_doc(config, result)))
    environment = os.environ | {
        "PYTHONPATH": str(REPO_ROOT / "server") + os.pathsep + str(REPO_ROOT),
        "COGAME_LOAD_REPLAY_URI": replay.as_uri(),
        "COGAME_HOST": "127.0.0.1",
        "COGAME_PORT": "0",
        "PYTHONUNBUFFERED": "1",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cogame_cogolf.server",
        cwd=REPO_ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(5):
            while b"replay mode on" not in await process.stderr.readline():
                assert process.returncode is None
        start = asyncio.get_running_loop().time()
        process.send_signal(signal.SIGTERM)
        process.send_signal(signal.SIGINT)
        async with asyncio.timeout(3):
            stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        assert asyncio.get_running_loop().time() - start < 2.5
        assert not stdout and b"Traceback" not in stderr
    finally:
        if process.returncode is None:
            process.kill()
        async with asyncio.timeout(2):
            await process.wait()
