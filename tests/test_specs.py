"""The spec deck: every module declares the same attributes, its reference
is self-consistent, and its two baseline implementations diverge from the
reference on DIFFERENT clauses (so the baselines break each other)."""

from __future__ import annotations

import json

import pytest
from cogame_cogolf import contract
from cogame_cogolf.config import DEFAULT_HOLES
from cogame_cogolf.specs import DECK_VERSION, DECKS, DeckError, deck_keys, load_deck
from cogame_cogolf.specs._util import load_impl
from cogame_cogolf.values import BadValue, canon, equal

DECK = load_deck("core")
SPECS = [pytest.param(module, id=key) for key, module in sorted(DECK.items())]


def run(fn, args):
    try:
        return True, fn(*args)
    except Exception as exc:  # noqa: BLE001
        return False, exc


def agrees(fn, args, expect) -> bool:
    ok, value = run(fn, args)
    if not ok:
        return False
    try:
        return equal(canon(value), canon(expect))
    except BadValue:
        return False


def test_deck_registry():
    assert DECK_VERSION == "core-1"
    assert set(DECKS) == {"core"}
    assert len(DECK) == 12
    assert deck_keys("core") == sorted(DECK)
    assert len(DECK) >= DEFAULT_HOLES
    with pytest.raises(DeckError):
        load_deck("nope")


@pytest.mark.parametrize("spec", SPECS)
def test_spec_declares_every_attribute(spec):
    assert spec.KEY == spec.__name__.rsplit(".", 1)[-1]
    assert isinstance(spec.TITLE, str) and 0 < len(spec.TITLE) <= 48
    assert isinstance(spec.PROMPT, str) and 0 < len(spec.PROMPT) <= 1200
    assert isinstance(spec.AMBIGUITY, str) and 0 < len(spec.AMBIGUITY) <= 140
    assert spec.SIGNATURE["function"] == "solve"
    assert spec.SIGNATURE["params"] and spec.SIGNATURE["returns"]
    assert len(spec.EXAMPLES) == 2
    assert len(spec.PAR_TESTS) == contract.PAR_TESTS_PER_HOLE
    assert len(spec.SAFE_TESTS) == contract.MAX_TESTS_PER_HOLE
    assert len(spec.EDGE_TESTS) == contract.MAX_TESTS_PER_HOLE
    assert callable(spec.reference)
    assert "def solve" in spec.REFERENCE_IMPL


@pytest.mark.parametrize("spec", SPECS)
def test_reference_passes_its_own_examples_and_par_tests(spec):
    for case in list(spec.EXAMPLES) + list(spec.PAR_TESTS) + list(spec.SAFE_TESTS):
        assert agrees(spec.reference, case["args"], case["expect"]), \
            f"{spec.KEY}: reference disagrees with {case}"


@pytest.mark.parametrize("spec", SPECS)
def test_every_recorded_value_is_json_round_trippable(spec):
    for case in (list(spec.EXAMPLES) + list(spec.PAR_TESTS)
                 + list(spec.SAFE_TESTS) + list(spec.EDGE_TESTS)):
        for value in (case["args"], case["expect"]):
            assert json.loads(json.dumps(canon(value))) == canon(value)


@pytest.mark.parametrize("spec", SPECS)
def test_baselines_compile_and_define_solve(spec):
    for source in (spec.LITERAL_IMPL, spec.NAIVE_IMPL):
        assert isinstance(source, str)
        assert len(source) <= contract.MAX_IMPL_CHARS
        assert callable(load_impl(source))


@pytest.mark.parametrize("spec", SPECS)
def test_the_two_baselines_diverge_on_different_clauses(spec):
    """The literalist and the pedant must each be wrong where the other is
    right — that is what makes them break each other, and it is what keeps
    a scripted-vs-scripted certification episode from being a null match."""
    literal = load_impl(spec.LITERAL_IMPL)
    naive = load_impl(spec.NAIVE_IMPL)
    literal_only, naive_only = [], []
    cases = ([("par", c) for c in spec.PAR_TESTS]
             + [("safe", c) for c in spec.SAFE_TESTS]
             + [("edge", c) for c in spec.EDGE_TESTS]
             + [("example", c) for c in spec.EXAMPLES])
    for label, case in cases:
        ok, expected = run(spec.reference, case["args"])
        if not ok:
            continue  # the reference rejects it: not a clause, an illegal shot
        lit = agrees(literal, case["args"], expected)
        nai = agrees(naive, case["args"], expected)
        if lit and not nai:
            naive_only.append((label, case["name"] if "name" in case else label))
        if nai and not lit:
            literal_only.append((label, case.get("name", label)))
    assert literal_only, f"{spec.KEY}: the literalist never diverges"
    assert naive_only, f"{spec.KEY}: the pedant never diverges"


@pytest.mark.parametrize("spec", SPECS)
def test_safe_tests_are_legal_and_unique(spec):
    """Every literalist shot is reference-consistent by construction, and no
    two of them repeat the same arguments (which would be `duplicate`)."""
    seen = set()
    for case in spec.SAFE_TESTS:
        assert agrees(spec.reference, case["args"], case["expect"]), case
        key = json.dumps(canon(case["args"]), sort_keys=True)
        assert key not in seen, f"{spec.KEY}: duplicate safe test {case['name']}"
        seen.add(key)
        assert case["why"], case


@pytest.mark.parametrize("spec", SPECS)
def test_edge_tests_include_illegal_shots(spec):
    """The pedant's aggressive shots are the lesson: at least one of them is
    rejected by the reference (and the literalist is never that reckless)."""
    illegal = sum(1 for case in spec.EDGE_TESTS
                  if not agrees(spec.reference, case["args"], case["expect"]))
    assert illegal >= 1, f"{spec.KEY}: no edge test is illegal"
    assert illegal < len(spec.EDGE_TESTS), f"{spec.KEY}: every edge test illegal"


def test_keys_are_unique_and_match_the_registry():
    assert sorted(m.KEY for m in DECK.values()) == sorted(DECK)


@pytest.mark.parametrize("spec", SPECS)
def test_prompt_never_leaks_the_reference(spec):
    """The prompt is shown verbatim to both seats; the ambiguity note is
    replay-only and must not be quoted inside it."""
    assert spec.AMBIGUITY not in spec.PROMPT
