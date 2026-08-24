"""The scripted baselines: bounded, schema-valid, legal orders.

This is the bounded-orders assertion for cogolf. For every deck spec and
both baselines (and for the unknown-key path), the submission the baseline
plays must be a legal move a policy could have sent: within every cap, JSON
representable, and never a sandbox timeout. On top of that, EVERY literalist
shot must pass the reference legality gate — its tests are legal by
construction — and no baseline ever fires the same arguments twice in a hole.
"""

from __future__ import annotations

import json

import pytest
from cogame_cogolf import contract
from cogame_cogolf.baseline import (BASELINE_NAMES, UnknownBaseline, baseline,
                                    literalist, pedant)
from cogame_cogolf.engine import (compact, sanitize_submission,
                                  validate_submission_message)
from cogame_cogolf.sandbox import Sandbox
from cogame_cogolf.specs import load_deck
from cogame_cogolf.values import BadValue, canon, equal, fingerprint

DECK = load_deck("core")
CASES = [pytest.param(key, name, id=f"{key}-{name}")
         for key in sorted(DECK) for name in BASELINE_NAMES]
SANDBOX = Sandbox(call_cpu_seconds=1.0, batch_seconds=8.0)


@pytest.mark.parametrize("key,name", CASES)
def test_the_submission_validates_against_the_wire_schema(key, name):
    spec = DECK[key]
    message = baseline(name, spec, 3)
    assert message["type"] == contract.MSG_SUBMISSION and message["hole"] == 3
    payload, cause = validate_submission_message(message, 3)
    assert cause is None, cause
    assert len(json.dumps(message).encode("utf-8")) <= contract.MAX_MESSAGE_BYTES


@pytest.mark.parametrize("key,name", CASES)
def test_every_order_is_bounded(key, name):
    spec = DECK[key]
    message = baseline(name, spec, 1)
    assert 0 < len(message["impl"]) <= contract.MAX_IMPL_CHARS
    assert len(message["tests"]) <= contract.MAX_TESTS_PER_HOLE
    assert len(message["note"]) <= contract.MAX_NOTE_CHARS
    for test in message["tests"]:
        assert len(test["name"]) <= contract.MAX_TEST_NAME_CHARS
        assert len(test["why"]) <= contract.MAX_WHY_CHARS
        assert len(compact(canon(test["args"]))) <= contract.MAX_ARGS_CHARS
        assert len(compact(canon(test["expect"]))) <= contract.MAX_EXPECT_CHARS
        assert isinstance(test["args"], list)


@pytest.mark.parametrize("key,name", CASES)
def test_the_max_tests_cap_is_honoured(key, name):
    message = baseline(name, DECK[key], 1, 2)
    assert len(message["tests"]) == 2


@pytest.mark.parametrize("key,name", CASES)
def test_no_baseline_ever_duplicates_a_test_within_a_hole(key, name):
    message = baseline(name, DECK[key], 1)
    seen = {fingerprint(t["args"]) for t in message["tests"]}
    assert len(seen) == len(message["tests"])


@pytest.mark.parametrize("key", sorted(DECK))
def test_every_literalist_shot_passes_the_reference_legality_gate(key):
    spec = DECK[key]
    message = literalist(spec, 1)
    arity = len(spec.SIGNATURE["params"])
    for test in message["tests"]:
        assert len(test["args"]) == arity
        answer = spec.reference(*test["args"])          # must not raise
        assert equal(canon(answer), canon(test["expect"])), test


@pytest.mark.parametrize("key", sorted(DECK))
def test_the_pedant_fires_shots_the_reference_rejects(key):
    """The illegal verdicts are the lesson the baseline exists to teach."""
    spec = DECK[key]
    illegal = 0
    for test in pedant(spec, 1)["tests"]:
        try:
            answer = spec.reference(*test["args"])
        except Exception:  # noqa: BLE001
            illegal += 1
            continue
        try:
            if not equal(canon(answer), canon(test["expect"])):
                illegal += 1
        except BadValue:
            illegal += 1
    assert illegal >= 1


@pytest.mark.parametrize("key,name", CASES)
def test_no_baseline_submission_ever_hits_the_sandbox_timeout(key, name):
    """Every call a baseline can provoke — its own tests, the other
    baseline's tests and the par suite — resolves well inside the budget."""
    spec = DECK[key]
    source = baseline(name, spec, 1)["impl"]
    calls = []
    for i, test in enumerate(literalist(spec, 1)["tests"]
                             + pedant(spec, 1)["tests"]):
        calls.append({"id": i, "args": test["args"]})
    for j, par in enumerate(spec.PAR_TESTS):
        calls.append({"id": 900 + j, "args": par["args"]})
    batch = SANDBOX.run(source, calls)
    assert batch.broken is None, batch.broken
    for call in calls:
        result = batch.get(call["id"])
        assert result.kind != "timeout", (key, name, call, result)


@pytest.mark.parametrize("name", BASELINE_NAMES)
def test_the_unknown_spec_path_still_plays_a_bounded_legal_move(name):
    """A deck extended without updating a baseline still submits: a stub
    that echoes the worked example, and the spec's own examples as tests."""

    class Unknown:
        KEY = "mystery"
        EXAMPLES = [{"args": [[1, 2]], "expect": 3},
                    {"args": [[]], "expect": 0}]

    message = baseline(name, Unknown(), 2)
    payload, cause = validate_submission_message(message, 2)
    assert cause is None
    clean = sanitize_submission(payload, 2, 5)
    assert len(clean["tests"]) == 2
    assert len(clean["impl"]) <= contract.MAX_IMPL_CHARS
    batch = SANDBOX.run(message["impl"], [{"id": 0, "args": [[1, 2]]},
                                          {"id": 1, "args": [["x"]]}])
    assert batch.broken is None
    assert batch.get(0).ok and batch.get(0).value == 3
    assert batch.get(1).ok and batch.get(1).value is None


def test_an_unknown_baseline_name_is_an_error():
    with pytest.raises(UnknownBaseline):
        baseline("sniper", DECK["median"], 1)


def test_the_baselines_are_deterministic():
    for key in sorted(DECK):
        for name in BASELINE_NAMES:
            assert baseline(name, DECK[key], 4) == baseline(name, DECK[key], 4)
