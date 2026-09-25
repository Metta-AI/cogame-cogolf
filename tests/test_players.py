"""The player side: the env switch, the three reply-parsing paths, and the
rule that a player container always exits 0."""

from __future__ import annotations

import asyncio
import io
import json
import socket
import time

import pytest
from players import client as client_module
from players import llm_player
from players.client import MAX_NOTE_CHARS, Policy, normalize_submission, play_episode
from players.llm_player import (LLMPolicy, RETRY_BACKOFFS, _is_transient,
                                balanced_span, parse_reply)
from players.main import choose_policy
from players.jev import JevPolicy
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


def test_jev_uses_the_normal_private_view_and_returns_a_complete_submission(
        monkeypatch):
    monkeypatch.delenv("PLAYER_SCRIPTED", raising=False)
    monkeypatch.setenv("PLAYER_JEV", "true")
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "http://sidecar")
    monkeypatch.setenv("BEDROCK_MODEL", "typesafe/jev-1.13")
    policy = choose_policy()
    assert isinstance(policy, JevPolicy)
    requests = []

    def respond(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps({"answers": {"submission": {
            "type": "choice", "confidence": 0.9,
            "probabilities": {"literalist": 0.1, "pedant": 0.9},
        }}}).encode())

    monkeypatch.setattr("players.jev.urlopen", respond)
    played = policy.submission(1, OBSERVATION)
    assert played["impl"] == scripted_submission("pedant", 1, OBSERVATION)["impl"]
    assert played["tests"] == scripted_submission("pedant", 1, OBSERVATION)["tests"]
    assert played["note"].startswith("Jev chose pedant;")
    request, timeout = requests[0]
    body = json.loads(request.data)
    assert request.full_url == "http://sidecar/v1/systemone"
    assert body["model"] == "typesafe/jev-1.13"
    assert timeout < 27  # inside the Blitz player deadline
    state = json.loads(body["state"].split("\n", 1)[1])
    assert state["observation"] == OBSERVATION
    assert set(state["candidates"]) == {"literalist", "pedant"}
    assert normalize_submission(played, 1)["type"] == "submission"


def test_prompt_sidecar_uses_messages_api(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "http://sidecar")
    monkeypatch.setenv("BEDROCK_MODEL", "anthropic/claude-haiku-4.5")
    client = llm_player._BedrockHttpClient(timeout=12)
    requests = []

    def respond(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps({"stop_reason": "end_turn",
            "content": [{"type": "text", "text": LLM_ANSWER}]}).encode())

    monkeypatch.setattr(client._urllib, "urlopen", respond)
    reply = client.create(model="anthropic/claude-haiku-4.5", max_tokens=1800,
                          system="rules", messages=[{"role": "user", "content": "view"}])
    assert reply.content[0].text == LLM_ANSWER
    request, timeout = requests[0]
    body = json.loads(request.data)
    assert request.full_url == "http://sidecar/v1/messages"
    assert body["model"] == "anthropic/claude-haiku-4.5"
    assert "anthropic_version" not in body
    assert timeout == 12


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


# -- transient provider failures are retried inside the hole budget ---------

# The exact shape `_BedrockHttpClient.create` raised in issue #2.
SIDECAR_503 = ("bedrock invoke us.anthropic.claude-haiku-4-5-20251001-v1:0: "
               "<HTTPError 503: ''> {\"message\":\"LLM provider is unavailable\"}")
SIDECAR_400 = ("bedrock invoke us.anthropic.claude-haiku-4-5-20251001-v1:0: "
               "<HTTPError 400: 'Bad Request'> {\"message\":\"bad schema\"}")
LLM_ANSWER = json.dumps({"impl": "def solve(xs):\n    return sorted(xs)[len(xs) // 2]\n",
                         "tests": [{"name": "odd", "args": [[3, 1, 2]],
                                    "expect": 2, "why": "middle"}],
                         "note": "the model's own note"})


class _Reply:
    def __init__(self, text, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [type("B", (), {"type": "text", "text": text})()]


class _ScriptedClient:
    """A fake `messages.create` that plays a script of exceptions / replies
    and records every call's model and timeout."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = self
        self._timeout = None

    def with_options(self, **kwargs):
        self._timeout = kwargs.get("timeout")
        return self

    def create(self, **kwargs):
        self.calls.append({"model": kwargs["model"], "timeout": self._timeout})
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _bedrock_policy(monkeypatch, client, **kwargs):
    policy = LLMPolicy(provider="bedrock", **kwargs)
    policy._disabled = False
    policy._client = client
    logged = []
    monkeypatch.setattr(LLMPolicy, "_log", staticmethod(logged.append))
    return policy, logged


def _no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(llm_player.time, "sleep", slept.append)
    return slept


def test_a_transient_503_is_retried_and_the_model_s_answer_is_played(monkeypatch):
    slept = _no_sleep(monkeypatch)
    client = _ScriptedClient([RuntimeError(SIDECAR_503), RuntimeError(SIDECAR_503),
                              _Reply(LLM_ANSWER)])
    policy, logged = _bedrock_policy(monkeypatch, client)
    first = policy.model
    played = policy.submission(3, OBSERVATION)
    assert played["note"] == "the model's own note"
    assert played["impl"].startswith("def solve(xs)")
    assert len(client.calls) == 3
    assert slept == list(RETRY_BACKOFFS[:2])
    assert all(s < 3 for s in slept)
    # the retry switched to the next candidate immediately, and stayed there
    assert client.calls[0]["model"] == first
    assert client.calls[1]["model"] != first
    assert client.calls[2]["model"] == client.calls[1]["model"]
    assert any(f"API call failed at hole 3 on {first}" in line for line in logged)
    assert any("for the retry" in line for line in logged)
    # every call is bounded by the per-call timeout, never more
    assert all(0 < c["timeout"] <= policy.timeout for c in client.calls)


def test_a_permanent_400_degrades_immediately_without_a_retry(monkeypatch):
    slept = _no_sleep(monkeypatch)
    client = _ScriptedClient([RuntimeError(SIDECAR_400), _Reply(LLM_ANSWER)])
    policy, logged = _bedrock_policy(monkeypatch, client)
    first = policy.model
    played = policy.submission(1, OBSERVATION)
    assert "def solve" in played["impl"]
    assert played["note"].startswith("fallback:permanent_error")
    assert len(client.calls) == 1 and slept == []
    assert policy.model != first                       # rotated for the next hole
    assert any("for the next hole" in line for line in logged)


def test_the_hole_budget_forbids_a_retry_that_would_not_fit(monkeypatch):
    monkeypatch.setenv("COGAME_LLM_HOLE_BUDGET", "0.6")
    client = _ScriptedClient([RuntimeError(SIDECAR_503)] * 4 + [_Reply(LLM_ANSWER)])
    policy, logged = _bedrock_policy(monkeypatch, client)
    assert policy.hole_budget == 0.6
    started = time.monotonic()
    played = policy.submission(2, OBSERVATION)
    wall = time.monotonic() - started
    assert wall < 0.6 + 1.0
    assert played["note"].startswith("fallback:provider_error")
    assert "def solve" in played["impl"]
    # 0 + 0.5 + 1.0 >= 0.6: the first backoff already does not fit
    assert len(client.calls) == 1
    assert client.calls[0]["timeout"] <= 0.6
    assert any("no retry at hole 2" in line for line in logged)


def test_retries_stop_when_the_backoffs_are_exhausted(monkeypatch):
    slept = _no_sleep(monkeypatch)
    client = _ScriptedClient([RuntimeError(SIDECAR_503)] * 10)
    policy, _ = _bedrock_policy(monkeypatch, client)
    played = policy.submission(1, OBSERVATION)
    assert played["note"].startswith("fallback:provider_error")
    assert len(client.calls) == len(RETRY_BACKOFFS) + 1
    assert slept == list(RETRY_BACKOFFS)


def test_the_caching_rejected_retry_still_works_inside_the_loop(monkeypatch):
    slept = _no_sleep(monkeypatch)
    client = _ScriptedClient([RuntimeError("400 cache_control is not supported"),
                              _Reply(LLM_ANSWER)])
    policy, logged = _bedrock_policy(monkeypatch, client)
    policy.api_docs = "docs"
    played = policy.submission(1, OBSERVATION)
    assert played["note"] == "the model's own note"
    assert policy._cache_ok is False and slept == []
    assert len(client.calls) == 2
    assert any("prompt caching rejected" in line for line in logged)


@pytest.mark.parametrize("exc,expected", [
    (RuntimeError(SIDECAR_503), True),
    (RuntimeError("bedrock invoke m: <HTTPError 502: 'Bad Gateway'> "), True),
    (RuntimeError("bedrock invoke m: <HTTPError 429: 'Too Many Requests'> "), True),
    (RuntimeError("bedrock invoke m: URLError(timeout('timed out')) "), True),
    (RuntimeError("bedrock invoke m: TimeoutError('The read operation timed out') "), True),
    (RuntimeError("bedrock invoke m: ConnectionResetError(54, 'Connection reset by peer') "), True),
    (RuntimeError("bedrock invoke m: URLError(ConnectionRefusedError(61, 'Connection refused')) "), True),
    (TimeoutError(), True),
    (socket.timeout(), True),
    (ConnectionResetError(), True),
    (ConnectionRefusedError(), True),
    (socket.gaierror(), True),
    (RuntimeError(SIDECAR_400), False),
    (RuntimeError("bedrock invoke m: <HTTPError 401: 'Unauthorized'> "), False),
    (RuntimeError("bedrock invoke m: <HTTPError 403: 'Forbidden'> "), False),
    (RuntimeError("bedrock invoke m: <HTTPError 404: 'Not Found'> "), False),
    (RuntimeError("bedrock invoke m: <HTTPError 422: 'Unprocessable'> "), False),
    # the status wins over a body that merely mentions a transport word
    (RuntimeError("bedrock invoke m: <HTTPError 400: ''> "
                  "{\"message\":\"Connection timeout in request body\"}"), False),
    (RuntimeError("bedrock invoke m: JSONDecodeError('Expecting value') "), False),
    (ValueError("not json"), False),
    (json.JSONDecodeError("Expecting value", "", 0), False),
    (PermissionError("denied"), False),
    (FileNotFoundError("gone"), False),
    (KeyError("impl"), False),
])
def test_is_transient_classifies_representative_failures(exc, expected):
    assert _is_transient(exc) is expected


def test_is_transient_understands_the_anthropic_sdk_errors():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    request = httpx.Request("POST", "https://api.example/v1/messages")

    def status(code):
        return anthropic.APIStatusError(
            f"status {code}", response=httpx.Response(code, request=request),
            body=None)

    assert _is_transient(anthropic.APITimeoutError(request=request))
    assert _is_transient(anthropic.APIConnectionError(request=request))
    for code in (408, 409, 429, 500, 502, 503, 529):
        assert _is_transient(status(code)), code
    for code in (400, 401, 403, 404, 422):
        assert not _is_transient(status(code)), code


@pytest.mark.parametrize("reason", ["refusal", "unparseable", "no_client"])
def test_every_substitution_is_tagged_in_the_note(monkeypatch, reason):
    if reason == "no_client":
        policy = LLMPolicy(provider="none")
    else:
        reply = (_Reply("", stop_reason="refusal") if reason == "refusal"
                 else _Reply("no json here"))
        policy, _ = _bedrock_policy(monkeypatch, _ScriptedClient([reply]))
    played = policy.submission(1, OBSERVATION)
    note = played["note"]
    assert note.startswith(f"fallback:{reason}")
    assert len(note) <= MAX_NOTE_CHARS
    assert note.endswith(scripted_submission("literalist", 1, OBSERVATION)["note"])
    assert normalize_submission(played, 1)["note"] == note


def test_the_fallback_note_is_capped_on_rune_boundaries():
    from players.llm_player import _fallback_note
    note = _fallback_note("provider_error", "\u00e9" * 500)
    assert note == "fallback:provider_error"
    note = _fallback_note("refusal", "short")
    assert note == "fallback:refusal; short"


def test_the_hole_budget_leaves_margin_inside_the_manifest_deadline():
    from players.llm_player import DEFAULT_HOLE_BUDGET_SECONDS
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "coworld_manifest_template.json").read_text())
    deadline = manifest["game"]["config_schema"]["properties"][
        "hole_deadline_seconds"]["default"]
    assert DEFAULT_HOLE_BUDGET_SECONDS + 4.0 <= deadline
    assert sum(RETRY_BACKOFFS) + 1.0 < DEFAULT_HOLE_BUDGET_SECONDS


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
