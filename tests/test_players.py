"""The player side: the env switch, the three reply-parsing paths, and the
rule that a player container always exits 0."""

from __future__ import annotations

import asyncio
import json

import pytest
from players import client as client_module
from players.client import Policy, normalize_submission, play_episode
from players.llm_player import LLMPolicy, balanced_span, parse_reply
from players.main import choose_policy
from players.scripted import ScriptedPolicy, UnknownBaseline, scripted_submission

OBSERVATION = {
    "hole": 1, "holes": 9,
    "spec": {"key": "median", "title": "Median of a list",
             "prompt": "…", "signature": {"function": "solve",
                                          "params": [{"name": "xs"}],
                                          "returns": "int"},
             "examples": [{"args": [[1, 2, 3]], "expect": 2}]},
    "you": {"alias": "Ash", "slot": 0, "score": 0},
    "opponent": {"alias": "Basil", "slot": 1, "score": 0},
    "history": [], "rules": {"max_tests_per_hole": 5, "max_impl_chars": 4000},
}


# -- the env switch -----------------------------------------------------------

def test_scripted_wins_over_prompt(monkeypatch):
    monkeypatch.setenv("PLAYER_SCRIPTED", "pedant")
    monkeypatch.setenv("PLAYER_PROMPT", "ignored")
    policy = choose_policy()
    assert isinstance(policy, ScriptedPolicy) and policy.name == "pedant"


def test_an_unknown_baseline_name_is_fatal(monkeypatch):
    monkeypatch.setenv("PLAYER_SCRIPTED", "literalis")   # a typo
    monkeypatch.delenv("PLAYER_PROMPT", raising=False)
    with pytest.raises(UnknownBaseline):
        choose_policy()
    from players.main import main
    assert main() == 1


def test_a_prompt_selects_the_llm_policy(monkeypatch):
    monkeypatch.delenv("PLAYER_SCRIPTED", raising=False)
    monkeypatch.setenv("PLAYER_PROMPT", "play the boundaries")
    monkeypatch.setenv("COGAME_LLM_PROVIDER", "none")
    policy = choose_policy()
    assert isinstance(policy, LLMPolicy)
    assert policy.strategy == "play the boundaries"


def test_no_env_at_all_plays_the_literalist(monkeypatch):
    for name in ("PLAYER_SCRIPTED", "PLAYER_PROMPT", "ANTHROPIC_API_KEY",
                 "ANTHROPIC_AUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK",
                 "AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "AWS_ACCESS_KEY_ID",
                 "AWS_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
                 "USE_BEDROCK", "COGAME_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    policy = choose_policy()
    assert isinstance(policy, ScriptedPolicy) and policy.name == "literalist"


# -- the scripted policy ------------------------------------------------------

def test_the_scripted_policy_plays_the_engine_s_own_baseline():
    from cogame_cogolf.baseline import literalist
    from cogame_cogolf.specs import load_deck
    played = scripted_submission("literalist", 1, OBSERVATION)
    expected = literalist(load_deck("core")["median"], 1, 5)
    assert played["impl"] == expected["impl"]
    assert played["tests"] == expected["tests"]


def test_an_unknown_spec_key_still_submits():
    observation = json.loads(json.dumps(OBSERVATION))
    observation["spec"]["key"] = "not_in_this_build"
    played = scripted_submission("pedant", 2, observation)
    assert "def solve" in played["impl"] and played["tests"]


# -- the LLM reply paths ------------------------------------------------------

def test_strict_json_reply():
    payload = parse_reply(json.dumps(
        {"impl": "def solve(x):\n    return x\n", "tests": [], "note": "n"}))
    assert payload["impl"].startswith("def solve")


def test_json_with_trailing_prose():
    text = ('{"impl": "def solve(x):\\n    return x\\n", "tests": [], '
            '"note": "n"}\n\nI chose the lower middle because …')
    payload = parse_reply(text)
    assert payload["impl"].startswith("def solve") and payload["note"] == "n"


def test_fenced_python_plus_fenced_json():
    text = ("Here is my answer.\n\n```python\ndef solve(x):\n    return x\n```\n"
            "and the tests:\n```json\n{\"tests\": [{\"name\": \"a\", "
            "\"args\": [1], \"expect\": 1}], \"note\": \"fenced\"}\n```\n")
    payload = parse_reply(text)
    assert payload["impl"].strip().startswith("def solve")
    assert payload["tests"][0]["name"] == "a" and payload["note"] == "fenced"


@pytest.mark.parametrize("text", ["", "no code here at all", "```\nnot python\n",
                                  '{"tests": []}'])
def test_an_unparseable_reply_yields_none(text):
    assert parse_reply(text) is None


def test_balanced_span_ignores_braces_inside_strings():
    text = 'prose {"impl": "def solve(x):\\n    return \\"}\\"\\n"} tail'
    span = balanced_span(text)
    assert span.endswith("}") and json.loads(span)["impl"].startswith("def")


def test_an_unparseable_reply_substitutes_the_scripted_move(monkeypatch):
    policy = LLMPolicy(provider="none")
    logged = []
    monkeypatch.setattr(LLMPolicy, "_log", staticmethod(logged.append))

    class Reply:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": "no json here"})()]

    class Client:
        class messages:
            @staticmethod
            def create(**_kwargs):
                return Reply()

    policy._disabled = False
    policy._client = Client()
    played = policy.submission(1, OBSERVATION)
    assert "def solve" in played["impl"]              # the literalist move
    assert any("falling back (unparseable reply)" in line for line in logged)


def test_an_api_failure_substitutes_the_scripted_move_and_rotates_the_model():
    policy = LLMPolicy(provider="bedrock")
    first = policy.model

    class Client:
        class messages:
            @staticmethod
            def create(**_kwargs):
                raise RuntimeError("bedrock invoke: 429")

    policy._disabled = False
    policy._client = Client()
    played = policy.submission(1, OBSERVATION)
    assert "def solve" in played["impl"]
    assert policy.model != first


def test_the_prompt_is_bounded_and_carries_the_spec_verbatim():
    policy = LLMPolicy(provider="none", strategy="aim at the edges")
    observation = json.loads(json.dumps(OBSERVATION))
    observation["spec"]["prompt"] = "P" * 3000
    observation["history"] = [{"hole": i, "blob": "h" * 4000} for i in range(9)]
    prompt = policy.user_prompt(3, observation)
    assert len(prompt) <= 6000
    blocks = policy._system_blocks()
    assert "aim at the edges" in blocks[0]["text"]
    assert "MUST BEGIN WITH `{`" in blocks[0]["text"]


def test_the_model_candidates_drop_the_timing_out_profile():
    from players.llm_player import BEDROCK_MODEL_CANDIDATES
    assert BEDROCK_MODEL_CANDIDATES[0].startswith("us.anthropic.claude-haiku")
    assert not any("sonnet-4-6" in m for m in BEDROCK_MODEL_CANDIDATES)


# -- the wire ------------------------------------------------------------------

def test_normalize_builds_a_strict_wire_message():
    message = normalize_submission(
        {"impl": "def solve(x):\n    return x\n",
         "tests": [{"name": "a" * 90, "args": [1], "expect": 1, "why": "w" * 300},
                   {"name": "no args", "expect": 1},
                   {"name": "b", "args": [2], "expect": 2}],
         "note": "n" * 400}, 4)
    assert message["type"] == "submission" and message["hole"] == 4
    assert [t["name"][:2] for t in message["tests"]] == ["aa", "b"]
    assert len(message["tests"][0]["name"]) == 40
    assert len(message["tests"][0]["why"]) == 120
    assert len(message["note"]) == 200


@pytest.mark.parametrize("payload", [None, {}, {"impl": 5}, {"impl": "  "},
                                     {"impl": "x" * 5000}])
def test_normalize_refuses_an_unusable_answer(payload):
    assert normalize_submission(payload, 1) is None


def test_a_policy_that_raises_or_overruns_never_sends_a_noop():
    class Broken(Policy):
        def submission(self, hole, observation):
            raise RuntimeError("policy bug")

    message, fallback = asyncio.run(
        client_module._call_policy(Broken(), 1, OBSERVATION, 1.0))
    assert fallback and "def solve" in message["impl"]
    assert message["type"] == "submission"

    class Slow(Policy):
        def submission(self, hole, observation):
            import time
            time.sleep(2)
            return {"impl": "def solve(x):\n    return x\n"}

    message, fallback = asyncio.run(
        client_module._call_policy(Slow(), 1, OBSERVATION, 0.2))
    assert fallback and message["tests"]


# -- exit codes ----------------------------------------------------------------

def test_the_harness_exits_zero_on_done(monkeypatch):
    class Fake(Policy):
        def submission(self, hole, observation):
            return {"impl": "def solve(x):\n    return x\n"}

    async def fake_play(policy, url=None, **kwargs):
        policy.on_done({"scores": [1, -1]})
        return {"scores": [1, -1]}

    monkeypatch.setattr(client_module, "play_episode", fake_play)
    assert client_module.run_policy_main(Fake) == 0


def test_the_harness_exits_zero_when_the_server_goes_away(monkeypatch):
    """A close frame or a truncated read after the seat has connected means
    the server finished and went away — exit 0, never a player failure."""
    import aiohttp

    class Fake(Policy):
        def submission(self, hole, observation):
            return {"impl": "def solve(x):\n    return x\n"}

    seen = {"connects": 0}

    class FakeWS:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise aiohttp.ClientError("truncated read")

        async def close(self):
            return None

    class FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def ws_connect(self, url, **kwargs):
            seen["connects"] += 1
            if seen["connects"] == 1:
                return FakeWS()
            raise ConnectionRefusedError("refused")

        async def close(self):
            return None

    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    result = asyncio.run(play_episode(Fake(), "ws://example/player",
                                      reconnect_delay_seconds=0))
    assert result == {}
