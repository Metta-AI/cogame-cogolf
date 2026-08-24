"""The sandbox: what a submitted implementation can and cannot do.

These tests spawn the real subprocess runner — that is the point. They are
the only place the audit hook, the rlimits and the CPU timer are exercised.
"""

from __future__ import annotations

import math

import pytest
from cogame_cogolf.sandbox import Sandbox, SandboxError, describe
from cogame_cogolf.values import BadValue, canon, equal, fingerprint

SB = Sandbox(call_cpu_seconds=1.0, batch_seconds=8.0)


def one(source: str, args=(1,)):
    batch = SB.run(source, [{"id": 0, "args": list(args)}])
    return batch, batch.get(0)


def test_a_plain_implementation_runs():
    batch, result = one("def solve(x):\n    return x * 2\n", (21,))
    assert batch.broken is None
    assert result.ok and result.value == 42


def test_an_infinite_loop_is_killed_at_the_cpu_budget():
    batch, result = one("def solve(x):\n    while True:\n        pass\n")
    assert not result.ok and result.kind == "timeout"
    assert describe(result) == "timed out"


def test_results_before_a_batch_kill_are_kept_and_missing_ids_time_out():
    """A batch that runs out of wall clock still yields every NDJSON line it
    flushed; the ids that never arrived are recorded as timeouts."""
    sandbox = Sandbox(call_cpu_seconds=5.0, batch_seconds=2.0)
    source = ("def solve(x):\n"
              "    if x == 1:\n"
              "        while True:\n"
              "            pass\n"
              "    return x\n")
    batch = sandbox.run(source, [{"id": 0, "args": [0]},
                                 {"id": 1, "args": [1]},
                                 {"id": 2, "args": [2]}])
    assert batch.get(0).ok and batch.get(0).value == 0
    assert not batch.get(2).ok and batch.get(2).kind == "timeout"


@pytest.mark.parametrize("source,needle", [
    ("def solve(x):\n    import socket\n    return 1\n", "socket"),
    ("def solve(x):\n    import subprocess\n    return 1\n", "subprocess"),
    ("def solve(x):\n    import ctypes\n    return 1\n", "ctypes"),
    ("def solve(x):\n    import multiprocessing\n    return 1\n", "multiprocessing"),
])
def test_blocked_imports_are_denied(source, needle):
    _batch, result = one(source)
    assert not result.ok and result.kind == "error"
    assert needle in result.text


def test_a_file_write_fails():
    _batch, result = one(
        "def solve(x):\n    open('/tmp/cogolf-should-not-exist','w')\n    return 1\n")
    assert not result.ok and result.kind == "error"


def test_a_giant_allocation_raises_instead_of_killing_the_container():
    _batch, result = one("def solve(x):\n    return bytearray(1024*1024*1024)\n")
    assert not result.ok
    assert result.kind in ("error", "timeout")
    assert "MemoryError" in result.text or result.kind == "timeout"


def test_a_syntax_error_is_broken_with_a_reason():
    batch, result = one("def solve(x)\n  return 1\n")
    assert batch.broken and "SyntaxError" in batch.broken
    assert not result.ok and result.kind == "broken"


def test_no_callable_solve_is_broken():
    batch, _ = one("solve = 3\n")
    assert batch.broken and "solve" in batch.broken


def test_a_non_json_return_is_a_bad_value():
    _batch, result = one("def solve(x):\n    return {1: 2}\n")
    assert not result.ok and result.kind == "bad_value"
    _batch, result = one("def solve(x):\n    return float('nan')\n")
    assert not result.ok and result.kind == "bad_value"


def test_the_reference_runs_through_the_same_runner():
    batch = SB.run_reference("def solve(xs):\n    return sorted(xs)\n",
                             [{"id": 0, "args": [[3, 1, 2]]}])
    assert batch.get(0).value == [1, 2, 3]


def test_a_missing_interpreter_is_a_harness_fault():
    with pytest.raises(SandboxError):
        Sandbox(python="/nonexistent/python").run("def solve():\n    return 1\n",
                                                  [{"id": 0, "args": []}])


# -- canon / equality (one rule, used everywhere) ----------------------------

def test_canon_and_equality():
    assert equal(canon(1), canon(1.0))          # numbers compare by value
    assert not equal(canon(True), canon(1))     # bools are type-tagged
    assert not equal(canon(1), canon(True))
    assert equal(canon(True), canon(True))
    assert equal(canon((1, 2)), canon([1, 2]))  # tuples canonicalise to lists
    assert equal(canon({"a": 1, "b": 2}), canon({"b": 2, "a": 1}))
    assert not equal(canon("1"), canon(1))
    assert not equal(canon(None), canon(0))
    assert equal(canon("é"), canon("é"))
    for bad in (float("nan"), float("inf"), {1: 2}, {"a"}, object()):
        with pytest.raises(BadValue):
            canon(bad)


def test_fingerprint_distinguishes_types_and_ignores_key_order():
    assert fingerprint([1]) != fingerprint([True])
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert fingerprint(1) == fingerprint(1.0)
    assert fingerprint([1, 2]) != fingerprint([2, 1])


def test_describe_is_bounded():
    from cogame_cogolf.sandbox import CallResult
    long_text = "x" * 5000
    assert len(describe(CallResult(ok=False, kind="error", text=long_text))) <= 300
    assert len(describe(CallResult(ok=True, value=[long_text]))) <= 300
    assert not math.isnan(0.0)  # sanity: the module imported cleanly
