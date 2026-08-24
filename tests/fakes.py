"""Test doubles: a scripted submission source and a fake sandbox.

The engine is transport-free — it talks to ``SubmissionSource`` objects and a
``Sandbox``. Both are faked here so the hole loop can be driven without a
websocket and, where the test is about ordering rather than execution,
without subprocesses.
"""

from __future__ import annotations

import asyncio
import time

from cogame_cogolf.baseline import baseline
from cogame_cogolf.sandbox import BatchResult, CallResult
from cogame_cogolf.specs import load_deck


class ScriptedSource:
    """Answers every hole with a scripted baseline's submission."""

    def __init__(self, name: str = "literalist", *, delay: float = 0.0,
                 silent_holes: tuple = (), reply=None):
        self.name = name
        self.delay = delay
        self.silent_holes = set(silent_holes)
        self.reply = reply
        self.wrong_hole_count = 0
        self.sent: list[tuple[int, bool, float]] = []
        self.connected = True

    async def wait_connected(self, timeout_seconds: float) -> bool:
        return self.connected

    async def get_submission(self, hole: int, payload: dict, deadline_at: float):
        self.sent.append((hole, bool(payload.get("retry")), time.monotonic()))
        if self.delay:
            await asyncio.sleep(self.delay)
        if hole in self.silent_holes:
            # never answers: the engine must retry, then fall back
            await asyncio.sleep(max(0.0, deadline_at - time.monotonic()))
            return None, "timeout"
        if self.reply is not None:
            return self.reply(hole, payload), None
        spec_key = payload["observation"]["spec"]["key"]
        spec = load_deck("core")[spec_key]
        return baseline(self.name, spec, hole, 5), None


class FakeSandbox:
    """A sandbox that answers from a callable instead of a subprocess."""

    def __init__(self, answer=None, *, broken: str | None = None):
        self.answer = answer
        self.broken = broken
        self.calls: list[tuple[str, int]] = []

    def _run(self, source: str, calls: list[dict]) -> BatchResult:
        self.calls.append((source, len(calls)))
        batch = BatchResult(broken=self.broken)
        if self.broken:
            return batch
        for call in calls:
            value = self.answer(source, call["args"]) if self.answer else None
            batch.results[call["id"]] = CallResult(ok=True, value=value)
        return batch

    def run(self, source: str, calls: list[dict], *, cpu_seconds=None):
        return self._run(source, calls)

    def run_reference(self, source: str, calls: list[dict]):
        return self._run(source, calls)
