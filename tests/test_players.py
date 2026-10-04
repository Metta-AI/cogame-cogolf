"""Source parser and explicit policy selection; native transport uses real fixtures."""

import json

import pytest
from cogame_cogolf.submission import balanced_span, normalize_submission, parse_reply

from players.main import choose_policy
from players.scripted import ScriptedPolicy, UnknownBaseline, scripted_submission

OBSERVATION = {
    "hole": 1,
    "holes": 9,
    "spec": {
        "key": "median",
        "title": "Median of a list",
        "prompt": "…",
        "signature": {
            "function": "solve",
            "params": [{"name": "xs"}],
            "returns": "int",
        },
        "examples": [{"args": [[1, 2, 3]], "expect": 2}],
    },
    "you": {"alias": "Ash", "slot": 0, "score": 0},
    "opponent": {"alias": "Basil", "slot": 1, "score": 0},
    "history": [],
    "rules": {"max_tests_per_hole": 5, "max_impl_chars": 4000},
}


def test_scripted_wins_over_prompt(monkeypatch):
    monkeypatch.setenv("PLAYER_SCRIPTED", "pedant")
    monkeypatch.setenv("PLAYER_PROMPT", "ignored")
    policy = choose_policy()
    assert isinstance(policy, ScriptedPolicy) and policy.name == "pedant"


def test_an_unknown_baseline_name_is_fatal(monkeypatch):
    monkeypatch.setenv("PLAYER_SCRIPTED", "literalis")  # a typo
    monkeypatch.delenv("PLAYER_PROMPT", raising=False)
    with pytest.raises(UnknownBaseline):
        choose_policy()
    from players.main import main

    assert main() == 1


def test_the_scripted_policy_plays_the_engine_s_own_baseline():
    from cogame_cogolf.baseline import literalist
    from cogame_cogolf.specs import load_deck

    played = scripted_submission("literalist", 1, OBSERVATION)
    expected = literalist(load_deck("core")["median"], 1, 5)
    assert played["impl"] == expected["impl"]
    assert played["tests"] == expected["tests"]


def test_strict_json_reply():
    payload = parse_reply(
        json.dumps({"impl": "def solve(x):\n    return x\n", "tests": [], "note": "n"})
    )
    assert payload["impl"].startswith("def solve")


def test_json_with_trailing_prose():
    text = (
        '{"impl": "def solve(x):\\n    return x\\n", "tests": [], '
        '"note": "n"}\n\nI chose the lower middle because …'
    )
    payload = parse_reply(text)
    assert payload["impl"].startswith("def solve") and payload["note"] == "n"


def test_fenced_python_plus_fenced_json():
    text = (
        "Here is my answer.\n\n```python\ndef solve(x):\n    return x\n```\n"
        'and the tests:\n```json\n{"tests": [{"name": "a", '
        '"args": [1], "expect": 1}], "note": "fenced"}\n```\n'
    )
    payload = parse_reply(text)
    assert payload["impl"].strip().startswith("def solve")
    assert payload["tests"][0]["name"] == "a" and payload["note"] == "fenced"


@pytest.mark.parametrize(
    "text", ["", "no code here at all", "```\nnot python\n", '{"tests": []}']
)
def test_an_unparseable_reply_yields_none(text):
    assert parse_reply(text) is None


def test_balanced_span_ignores_braces_inside_strings():
    text = 'prose {"impl": "def solve(x):\\n    return \\"}\\"\\n"} tail'
    span = balanced_span(text)
    assert span.endswith("}") and json.loads(span)["impl"].startswith("def")


def test_normalize_builds_a_strict_wire_message():
    message = normalize_submission(
        {
            "impl": "def solve(x):\n    return x\n",
            "tests": [
                {"name": "a" * 90, "args": [1], "expect": 1, "why": "w" * 300},
                {"name": "no args", "expect": 1},
                {"name": "b", "args": [2], "expect": 2},
            ],
            "note": "n" * 400,
        },
        4,
    )
    assert message["type"] == "submission" and message["hole"] == 4
    assert [t["name"][:2] for t in message["tests"]] == ["aa", "b"]
    assert len(message["tests"][0]["name"]) == 40
    assert len(message["tests"][0]["why"]) == 120
    assert len(message["note"]) == 200


@pytest.mark.parametrize(
    "payload", [None, {}, {"impl": 5}, {"impl": "  "}, {"impl": "x" * 5000}]
)
def test_normalize_refuses_an_unusable_answer(payload):
    assert normalize_submission(payload, 1) is None


def test_missing_native_endpoint_never_selects_a_baseline(monkeypatch):
    monkeypatch.delenv("PLAYER_SCRIPTED", raising=False)
    monkeypatch.delenv("COWORLD_LLM_ENDPOINT", raising=False)
    with pytest.raises(KeyError, match="COWORLD_LLM_ENDPOINT"):
        choose_policy()
