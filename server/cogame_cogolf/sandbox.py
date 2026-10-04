"""The code harness: run one submitted implementation out of process.

One subprocess per (implementation, hole) — never a thread, never the
server's own interpreter. The child is ``sandbox_runner.py``, launched with
``-I -S`` (isolated: no site, no user site, no environment-driven import
path), a scrubbed environment and a fresh empty working directory. The job
goes in on stdin as one JSON object; results come back as NDJSON, one line
per call, flushed before the next call starts, so a batch killed at the
wall cap still yields every result it produced. Ids that never arrived are
recorded ``timeout``.

The reference implementation of a spec runs through this SAME runner (with
a longer CPU budget, since its source is trusted), so the codebase has
exactly one execution path and one equality function.

Note on the launch line: the runner is addressed by FILE PATH rather than
``-m cogame_cogolf.sandbox_runner`` because ``-I`` implies ``-E`` and would
drop the ``PYTHONPATH`` a ``-m`` lookup needs; the child re-inserts the
server directory itself. ``PYTHONPATH`` is still exported for a runner
started without ``-I``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import lifecycle
from .contract import MAX_BROKEN_REASON_CHARS
from .values import BadValue, canon, equal  # noqa: F401  (re-exported)

RUNNER = Path(__file__).resolve().with_name("sandbox_runner.py")
SERVER_DIR = str(Path(__file__).resolve().parents[1])

DEFAULT_CALL_CPU_SECONDS = 1.0
DEFAULT_BATCH_SECONDS = 6.0
REFERENCE_CPU_SECONDS = 2.0
MAX_STDERR_CHARS = 2000
MAX_RECEIVED_STREAM_BYTES = 4 * 1024 * 1024


class SandboxError(RuntimeError):
    """The runner could not be spawned at all (a harness fault)."""


@dataclass
class CallResult:
    """One ``solve(*args)`` call."""

    ok: bool
    value: object = None
    kind: str = ""  # error | timeout | bad_value | broken
    text: str = ""


@dataclass
class BatchResult:
    """One (implementation, batch-of-calls) run."""

    broken: str | None = None  # reason when the impl never loaded
    results: dict[int, CallResult] = field(default_factory=dict)
    stderr: str = ""

    def get(self, call_id: int) -> CallResult:
        found = self.results.get(call_id)
        if found is not None:
            return found
        if self.broken:
            return CallResult(ok=False, kind="broken", text=self.broken)
        return CallResult(
            ok=False, kind="timeout", text="the sandbox batch ended before this call"
        )


def _clip(text: str, limit: int = MAX_BROKEN_REASON_CHARS) -> str:
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


class Sandbox:
    """Runs implementations. One instance per episode (config carrier)."""

    def __init__(
        self,
        call_cpu_seconds: float = DEFAULT_CALL_CPU_SECONDS,
        batch_seconds: float = DEFAULT_BATCH_SECONDS,
        python: str | None = None,
    ):
        self.call_cpu_seconds = float(call_cpu_seconds)
        self.batch_seconds = float(batch_seconds)
        self.python = python or sys.executable

    async def run(
        self, source: str, calls: list[dict], *, cpu_seconds: float | None = None
    ) -> BatchResult:
        """Own the sandbox process and streams until actual exit and EOF.

        Wall timeout kills this owned process. Received NDJSON prefixes survive.
        Unsettled owners retain their private working directory and capture.
        """
        job = json.dumps(
            {
                "source": source,
                "calls": [
                    {"id": int(c["id"]), "args": list(c.get("args") or [])}
                    for c in calls
                ],
                "cpu_seconds": float(
                    self.call_cpu_seconds if cpu_seconds is None else cpu_seconds
                ),
            }
        ).encode()
        env = {
            "PYTHONPATH": SERVER_DIR,
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "COGOLF_SANDBOX_UID": os.environ.get("COGOLF_SANDBOX_UID", "65534"),
        }
        workdir = Path(tempfile.mkdtemp(prefix="cogolf-sandbox-"))
        stdout, stderr = bytearray(), bytearray()
        deadline = asyncio.get_running_loop().time() + self.batch_seconds
        spawned = lifecycle.owned_task(
            asyncio.create_subprocess_exec(
                self.python,
                "-I",
                "-S",
                str(RUNNER),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=env,
                close_fds=True,
            )
        )
        stream_tasks: set[asyncio.Task] = set()
        proc = None
        joined = False
        output_limit = asyncio.Event()
        limit_waiter = lifecycle.owned_task(output_limit.wait())

        async def receive(
            stream: asyncio.StreamReader, capture: bytearray, path: Path
        ) -> None:
            with path.open("wb", buffering=0) as received:
                while chunk := await stream.read(65536):
                    prefix = chunk[
                        : max(0, MAX_RECEIVED_STREAM_BYTES + 1 - len(capture))
                    ]
                    capture.extend(prefix)
                    received.write(prefix)
                    if len(capture) > MAX_RECEIVED_STREAM_BYTES:
                        output_limit.set()
                    # Drain already-buffered overflow after the child is killed.
                    # Otherwise a paused pipe can prevent actual transport EOF.

        async def send(process: asyncio.subprocess.Process) -> None:
            assert process.stdin is not None
            process.stdin.write(job)
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

        try:
            done, _ = await asyncio.wait(
                {spawned}, timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if not done:
                raise lifecycle.OwnershipUnsettled(
                    "sandbox acquisition exceeded batch deadline"
                )
            error = spawned.exception()
            if error is not None:
                raise SandboxError("cannot spawn the sandbox runner") from error
            proc = spawned.result()
            assert proc.stdout is not None and proc.stderr is not None
            stream_tasks = {
                lifecycle.owned_task(
                    receive(proc.stdout, stdout, workdir / "stdout.received")
                ),
                lifecycle.owned_task(
                    receive(proc.stderr, stderr, workdir / "stderr.received")
                ),
                lifecycle.owned_task(send(proc)),
                lifecycle.owned_task(proc.wait()),
            }
            pending = set(stream_tasks)
            while pending and not output_limit.is_set():
                done, _ = await asyncio.wait(
                    pending | {limit_waiter},
                    timeout=max(0, deadline - asyncio.get_running_loop().time()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                pending -= done
        finally:
            drain_deadline = lifecycle.cleanup_deadline()
            waiter_joined = await lifecycle.settle(
                {limit_waiter}, drain_deadline, cancel=True
            )
            spawn_joined = await lifecycle.settle(
                {spawned}, drain_deadline, cancel=False
            )
            if spawn_joined and not spawned.cancelled() and spawned.exception() is None:
                proc = spawned.result()
                if proc.returncode is None:
                    proc.kill()
                if not stream_tasks:
                    assert proc.stdout is not None and proc.stderr is not None
                    stream_tasks = {
                        lifecycle.owned_task(
                            receive(proc.stdout, stdout, workdir / "stdout.received")
                        ),
                        lifecycle.owned_task(
                            receive(proc.stderr, stderr, workdir / "stderr.received")
                        ),
                        lifecycle.owned_task(proc.wait()),
                    }
                    assert proc.stdin is not None
                    proc.stdin.close()
                joined = await lifecycle.settle(
                    stream_tasks, drain_deadline, cancel=False
                )
                if not joined:
                    await lifecycle.settle(stream_tasks, drain_deadline, cancel=True)
            elif spawn_joined:
                joined = True  # Failed acquisition owns no child process or streams.
            if joined and waiter_joined:
                shutil.rmtree(workdir)
            else:
                raise lifecycle.OwnershipUnsettled(
                    f"sandbox process or readers unresolved; private capture retained at {workdir}"
                )
        for task in stream_tasks:
            task.result()
        result = _parse(
            stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
        )
        if output_limit.is_set():
            result.broken = "sandbox received stream exceeds 4 MiB limit"
        return result

    async def run_reference(self, source: str, calls: list[dict]) -> BatchResult:
        """Trusted source (a spec's reference) with the longer CPU budget."""
        return await self.run(source, calls, cpu_seconds=REFERENCE_CPU_SECONDS)


def _parse(stdout: str, stderr: str) -> BatchResult:
    batch = BatchResult(stderr=_clip(stderr, MAX_STDERR_CHARS))
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("kind") == "broken":
            batch.broken = _clip(record.get("text") or "implementation broken")
            continue
        call_id = record.get("id")
        if not isinstance(call_id, int):
            continue
        if record.get("ok"):
            batch.results[call_id] = CallResult(ok=True, value=record.get("value"))
        else:
            batch.results[call_id] = CallResult(
                ok=False,
                kind=str(record.get("kind") or "error"),
                text=_clip(record.get("text") or ""),
            )
    return batch


def describe(result: CallResult) -> str:
    """The ``observed`` string recorded for a shot (already clipped)."""
    if result.ok:
        try:
            return _clip(json.dumps(canon(result.value), ensure_ascii=False))
        except BadValue as exc:
            return _clip(f"bad value: {exc}")
    if result.kind == "timeout":
        return "timed out"
    if result.kind == "broken":
        return _clip(f"broken implementation: {result.text}")
    return _clip(result.text or result.kind or "error")
