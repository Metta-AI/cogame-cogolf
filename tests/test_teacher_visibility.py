"""Shipped baseline teacher observes no hidden oracle, audit or opponent source."""

from copy import deepcopy

import pytest
from cogame_cogolf.baseline import BASELINE_NAMES
from cogame_cogolf.engine import Engine
from cogame_cogolf.specs import load_deck

from players.scripted import scripted_submission
from tests.conftest import make_config
from tests.fakes import FakeSandbox, ScriptedSource


@pytest.mark.parametrize("name", BASELINE_NAMES)
@pytest.mark.parametrize("key", sorted(load_deck("core")))
def test_teacher_unchanged_under_hidden_oracle_audit_and_future_permutation(
    monkeypatch, name, key
):
    spec = load_deck("core")[key]
    config = make_config()
    engine = Engine(config, [ScriptedSource(), ScriptedSource()], FakeSandbox())
    observation = engine._observation_message(1, spec, 0, retry=False)["observation"]
    before = scripted_submission(name, 1, deepcopy(observation))
    monkeypatch.setattr(spec, "REFERENCE_IMPL", "private-oracle-sentinel")
    monkeypatch.setattr(
        spec, "PAR_TESTS", [{"args": ["private-audit-sentinel"], "expect": 914}]
    )
    monkeypatch.setattr(
        spec, "reference", lambda *args: pytest.fail("teacher read hidden reference")
    )
    hidden = load_deck("core")
    monkeypatch.setitem(hidden, "private-future-spec", object())
    after = scripted_submission(name, 1, deepcopy(observation))
    assert after == before
    assert "private-oracle-sentinel" not in str(after)
    assert "private-audit-sentinel" not in str(after)
