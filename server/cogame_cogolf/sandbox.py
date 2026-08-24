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

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .contract import MAX_BROKEN_REASON_CHARS
from .values import BadValue, canon, equal  # noqa: F401  (re-exported)

RUNNER = Path(__file__).resolve().with_name("sandbox_runner.py")
SERVER_DIR = str(Path(__file__).resolve().parents[1])

DEFAULT_CALL_CPU_SECONDS = 1.0
DEFAULT_BATCH_SECONDS = 6.0
REFERENCE_CPU_SECONDS = 2.0
MAX_STDERR_CHARS = 2000
# Belt and braces around subprocess.run's own timeout.
SPAWN_GRACE_SECONDS = 5.0


class SandboxError(RuntimeError):
    """The runner could not be spawned at all (a harness fault)."""


@dataclass
class CallResult:
    """One ``solve(*args)`` call."""
    ok: bool
    value: object = None
    kind: str = ""      # error | timeout | bad_value | broken
    text: str = ""


@dataclass
class BatchResult:
    """One (implementation, batch-of-calls) run."""
    broken: str | None = None          # reason when the impl never loaded
    results: dict[int, CallResult] = field(default_factory=dict)
    stderr: str = ""

    def get(self, call_id: int) -> CallResult:
        found = self.results.get(call_id)
        if found is not None:
            return found
        if self.broken:
            return CallResult(ok=False, kind="broken", text=self.broken)
        return CallResult(ok=False, kind="timeout",
                          text="the sandbox batch ended before this call")


def _clip(text: str, limit: int = MAX_BROKEN_REASON_CHARS) -> str:
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


class Sandbox:
    """Runs implementations. One instance per episode (config carrier)."""

    def __init__(self, call_cpu_seconds: float = DEFAULT_CALL_CPU_SECONDS,
                 batch_seconds: float = DEFAULT_BATCH_SECONDS,
                 python: str | None = None):
        self.call_cpu_seconds = float(call_cpu_seconds)
        self.batch_seconds = float(batch_seconds)
        self.python = python or sys.executable

    def run(self, source: str, calls: list[dict], *,
            cpu_seconds: float | None = None) -> BatchResult:
        """Run ``solve(*args)`` for every call against ``source``.

        ``calls`` is ``[{"id": int, "args": list}, ...]``. Never raises for
        anything the submitted code did; :class:`SandboxError` only when the
        interpreter itself could not be started.
        """
        job = json.dumps({
            "source": source,
            "calls": [{"id": int(c["id"]), "args": list(c.get("args") or [])}
                      for c in calls],
            "cpu_seconds": float(self.call_cpu_seconds
                                 if cpu_seconds is None else cpu_seconds),
        })
        env = {
            "PYTHONPATH": SERVER_DIR,
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "COGOLF_SANDBOX_UID": os.environ.get("COGOLF_SANDBOX_UID", "65534"),
        }
        with tempfile.TemporaryDirectory(prefix="cogolf-sandbox-") as workdir:
            try:
                proc = subprocess.run(
                    [self.python, "-I", "-S", str(RUNNER)],
                    input=job, capture_output=True, text=True, cwd=workdir,
                    env=env, timeout=self.batch_seconds, check=False)
                stdout, stderr = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as expired:
                stdout = _as_text(expired.stdout)
                stderr = _as_text(expired.stderr)
            except OSError as exc:
                raise SandboxError(
                    f"cannot spawn the sandbox runner: {exc}") from exc
        return _parse(stdout, stderr)

    def run_reference(self, source: str, calls: list[dict]) -> BatchResult:
        """Trusted source (a spec's reference) with the longer CPU budget."""
        return self.run(source, calls, cpu_seconds=REFERENCE_CPU_SECONDS)


def _as_text(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


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
            batch.results[call_id] = CallResult(ok=True,
                                                value=record.get("value"))
        else:
            batch.results[call_id] = CallResult(
                ok=False, kind=str(record.get("kind") or "error"),
                text=_clip(record.get("text") or ""))
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
