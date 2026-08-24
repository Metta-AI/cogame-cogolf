"""Reference LLM policy: ask Claude for each hole's submission.

Every hole the policy sends the spec (verbatim), the score, and a compacted
history of the last four holes, and asks for ONE JSON object holding an
implementation of ``solve(...)`` and up to five test cases. The reply is
parsed leniently and the wire message is built strictly.

Providers (chosen by ``COGAME_LLM_PROVIDER``, else auto-detected):

- ``anthropic`` — the Claude API via the ``anthropic`` SDK, credentials
  from ``ANTHROPIC_API_KEY``.
- ``bedrock`` — Claude on Amazon Bedrock; hosted pods reach it through a
  sidecar (``AWS_ENDPOINT_URL_BEDROCK_RUNTIME`` +
  ``AWS_BEARER_TOKEN_BEDROCK``).
- ``none`` — no LLM: every hole plays the scripted ``literalist`` move.

Degrade, never hang: the call is bounded by ``COGAME_LLM_TIMEOUT`` (32 s,
inside the 40 s hole deadline), retried once by the SDK, and ANY failure —
API error, refusal, unparseable text — substitutes the scripted
``literalist`` submission for this hole. The harness never sends a noop.

``PLAYER_PROMPT`` is appended to the system preamble as the policy's
strategy paragraph: a policy is just a prompt.
"""

from __future__ import annotations

import json
import os
import re
import sys

from players.client import Policy, main_for
from players.scripted import scripted_submission

DEFAULT_MODEL = "claude-haiku-4-5"
# Bedrock inference profiles, tried in order. `us.anthropic.claude-sonnet-4-6`
# is deliberately absent: it times out on every sidecar call, and one
# throttle then cascades into scripted fallbacks (cogame-raid, 2026-08-23).
BEDROCK_MODEL_CANDIDATES = [
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
]
DEFAULT_BEDROCK_MODEL = BEDROCK_MODEL_CANDIDATES[0]

# An implementation of ~60 lines plus five test records; 400/900 truncate
# mid-function.
MAX_TOKENS = 1800
DEFAULT_TIMEOUT_SECONDS = 32.0
MAX_PROMPT_CHARS = 6000
MAX_HISTORY_HOLES = 4
MAX_HISTORY_ENTRY_CHARS = 1200
MAX_API_DOCS_CHARS = 12000

_PY_FENCE_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)\s*\n(.*?)```", re.DOTALL)

SYSTEM_PREAMBLE = """You are one of two code agents playing cogolf, a nine-hole adversarial-programming match.
Each hole you get one deliberately ambiguous spec. You must reply with ONE implementation of
`solve(...)` and up to 5 test cases. Your tests are fired at your opponent's implementation; their
tests are fired at yours. A hidden reference implementation decides every ambiguous clause: a test of
yours only counts if the reference agrees with it, and a hidden 4-case audit runs against your code
every hole. You score `(your breaching tests + their audit failures) - (their breaching tests + your
audit failures)`. So: implement the reading a careful author most likely meant, and aim your tests at
the clauses where a careless reader would diverge from that reading.
REPLY FORMAT - your reply MUST BEGIN WITH `{` and be a single JSON object:
`{"impl": "def solve(...):\\n    ...", "tests": [{"name": "...", "args": [...], "expect": ..., "why": "..."}], "note": "..."}`.
`impl` is Python source (stdlib only, no imports of socket/subprocess/ctypes/multiprocessing, no file
or network access, no infinite loops - each call gets 1 second of CPU). `args` is the argument LIST
for one `solve(*args)` call and `expect` is the exact JSON value it must return. `why` is one short
sentence naming the clause you are testing. Emit no prose outside the JSON object."""


def _provider_from_env() -> str:
    explicit = os.environ.get("COGAME_LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("USE_BEDROCK", "").strip().lower() in ("1", "true", "yes") \
            or os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME") \
            or os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        return "bedrock"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE") \
            or os.environ.get("AWS_ROLE_ARN") \
            or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
        return "bedrock"
    return "none"


def balanced_span(text: str) -> str | None:
    """The first balanced ``{...}`` span of ``text`` (accepts trailing prose)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_reply(text: str) -> dict | None:
    """Lenient reply parsing; the wire stays strict.

    In order: (a) ``json.loads`` of the whole reply; (b) ``json.loads`` of
    the first balanced ``{...}`` span; (c) the fenced-block fallback — the
    first ```python block becomes ``impl`` and the first ```json block is
    parsed for ``tests``/``note``. Returns None when none of the three
    yields an ``impl`` string.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    for candidate in (text, balanced_span(text)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("impl"), str):
            return payload
    code = _PY_FENCE_RE.search(text)
    if not code:
        return None
    payload = {"impl": code.group(1), "tests": [], "note": ""}
    block = _JSON_FENCE_RE.search(text)
    if block:
        try:
            extra = json.loads(block.group(1))
        except (json.JSONDecodeError, ValueError):
            extra = None
        if isinstance(extra, dict):
            if isinstance(extra.get("tests"), list):
                payload["tests"] = extra["tests"]
            if isinstance(extra.get("note"), str):
                payload["note"] = extra["note"]
        elif isinstance(extra, list):
            payload["tests"] = extra
    return payload


class _BedrockHttpClient:
    """Minimal InvokeModel client over the Bedrock runtime endpoint (or the
    hosted sidecar named by AWS_ENDPOINT_URL_BEDROCK_RUNTIME) authenticating
    with AWS_BEARER_TOKEN_BEDROCK. Exposes the ``messages.create`` shape the
    policy uses so both transports share one call site."""

    class _Block:
        def __init__(self, d):
            self.type = d.get("type", "")
            self.text = d.get("text", "")

    class _Response:
        def __init__(self, d):
            self.stop_reason = d.get("stop_reason")
            self.content = [_BedrockHttpClient._Block(b)
                            for b in d.get("content", [])]

    def __init__(self, timeout: float):
        import urllib.request  # noqa: PLC0415
        self._urllib = urllib.request
        region = os.environ.get("AWS_REGION") \
            or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
        endpoint = (os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "").strip()
                    or f"https://bedrock-runtime.{region}.amazonaws.com")
        self.endpoint = endpoint.rstrip("/")
        self.token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip()
        self.timeout = timeout
        self.messages = self  # so `client.messages.create(...)` works

    def with_options(self, **_kwargs):
        return self

    def create(self, *, model: str, max_tokens: int, system, messages):
        body = {"anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens, "system": system,
                "messages": messages}
        req = self._urllib.Request(
            f"{self.endpoint}/model/{model}/invoke",
            data=json.dumps(body).encode(), method="POST",
            headers={"content-type": "application/json",
                     "accept": "application/json",
                     **({"authorization": f"Bearer {self.token}"}
                        if self.token else {})})
        try:
            with self._urllib.urlopen(req, timeout=self.timeout) as resp:
                return self._Response(json.loads(resp.read().decode()))
        except Exception as exc:  # noqa: BLE001
            detail = ""
            if hasattr(exc, "read"):
                try:
                    detail = exc.read().decode(errors="replace")[:300]  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    detail = ""
            raise RuntimeError(f"bedrock invoke {model}: {exc!r} {detail}") from exc


class LLMPolicy(Policy):
    """One Claude call per hole; the scripted move whenever it fails."""

    def __init__(self, provider: str | None = None, model: str | None = None,
                 timeout_seconds: float | None = None,
                 strategy: str | None = None):
        self.provider = (provider or _provider_from_env()).lower()
        pinned = model or os.environ.get("COGAME_LLM_MODEL") or (
            os.environ.get("BEDROCK_MODEL") if self.provider == "bedrock"
            else None)
        if self.provider == "bedrock":
            self._models = [m for m in ([pinned] if pinned else [])
                            + BEDROCK_MODEL_CANDIDATES if m]
            self._models = list(dict.fromkeys(self._models))
        else:
            self._models = [pinned or DEFAULT_MODEL]
        self.model = self._models[0]
        self.timeout = timeout_seconds or float(
            os.environ.get("COGAME_LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
        self.strategy = (strategy if strategy is not None
                         else os.environ.get("PLAYER_PROMPT", "")).strip()
        self.api_docs = ""
        self.alias = "?"
        self.episode: dict = {}
        self._client = None
        self._cache_ok = True
        self._disabled = self.provider == "none"
        if self._disabled:
            self._log("no LLM provider configured; playing the scripted "
                      "literalist move every hole")

    @staticmethod
    def _log(msg: str) -> None:
        print(f"llm_player: {msg}", file=sys.stderr, flush=True)

    # -- client construction (lazy, optional deps) --------------------------

    def _client_or_none(self):
        if self._client is not None or self._disabled:
            return self._client
        try:
            import anthropic  # noqa: PLC0415 - optional dependency
        except Exception as exc:  # noqa: BLE001
            self._log(f"anthropic SDK unavailable ({exc!r}); playing scripted")
            self._disabled = True
            return None
        try:
            if self.provider == "bedrock" and (
                    os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME")
                    or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")):
                self._client = _BedrockHttpClient(timeout=self.timeout)
                return self._client
            if self.provider == "bedrock":
                region = (os.environ.get("AWS_REGION")
                          or os.environ.get("AWS_DEFAULT_REGION")
                          or "us-east-1")
                client = anthropic.AnthropicBedrock(aws_region=region)
            else:
                client = anthropic.Anthropic()
            self._client = client.with_options(timeout=self.timeout,
                                               max_retries=1)
        except Exception as exc:  # noqa: BLE001
            self._log(f"could not build {self.provider} client ({exc!r}); "
                      f"playing scripted")
            self._disabled = True
            return None
        return self._client

    # -- Policy hooks -------------------------------------------------------

    def on_welcome(self, welcome: dict) -> None:
        docs = welcome.get("api_docs")
        if isinstance(docs, str):
            self.api_docs = docs[:MAX_API_DOCS_CHARS]
        self.alias = str(welcome.get("alias") or "?")
        episode = welcome.get("episode")
        self.episode = episode if isinstance(episode, dict) else {}

    def _system_blocks(self) -> list:
        preamble = SYSTEM_PREAMBLE
        if self.strategy:
            preamble = preamble + "\n\nYOUR STRATEGY: " + self.strategy
        blocks = [{"type": "text", "text": preamble}]
        if self.api_docs:
            block = {"type": "text", "text": self.api_docs}
            if self._cache_ok:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks

    def user_prompt(self, hole: int, observation: dict) -> str:
        spec = observation.get("spec") or {}
        you = observation.get("you") or {}
        opponent = observation.get("opponent") or {}
        rules = observation.get("rules") or {}
        lines = [
            f"Hole {hole} of {observation.get('holes', '?')}. "
            f"You are {you.get('alias', self.alias)} on "
            f"{you.get('score', 0)}; {opponent.get('alias', 'your opponent')} "
            f"is on {opponent.get('score', 0)}.",
            f"SPEC {spec.get('key', '?')} - {spec.get('title', '')}",
            str(spec.get("prompt", "")),
            "SIGNATURE: " + json.dumps(spec.get("signature") or {}),
            "WORKED EXAMPLES: " + json.dumps(spec.get("examples") or []),
            f"You may submit up to {rules.get('max_tests_per_hole', 5)} tests; "
            f"impl at most {rules.get('max_impl_chars', 4000)} characters.",
        ]
        history = list(observation.get("history") or [])[-MAX_HISTORY_HOLES:]
        if history:
            lines.append("HISTORY (most recent last):")
            for entry in history:
                rendered = json.dumps(entry, ensure_ascii=False)
                if len(rendered) > MAX_HISTORY_ENTRY_CHARS:
                    rendered = rendered[:MAX_HISTORY_ENTRY_CHARS - 1] + "\u2026"
                lines.append(rendered)
        lines.append("Reply with the single JSON object now. It must begin "
                     "with {.")
        prompt = "\n\n".join(lines)
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[:MAX_PROMPT_CHARS - 1] + "\u2026"
        return prompt

    def submission(self, hole: int, observation: dict) -> dict:
        client = self._client_or_none()
        if client is None:
            return scripted_submission("literalist", hole, observation)
        user = self.user_prompt(hole, observation)
        try:
            system = self._system_blocks()
            try:
                response = client.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS, system=system,
                    messages=[{"role": "user", "content": user}],
                )
            except Exception as exc:  # noqa: BLE001
                if self._cache_ok and "cache_control" in str(exc):
                    self._cache_ok = False
                    self._log("prompt caching rejected; retrying without it")
                    response = client.messages.create(
                        model=self.model, max_tokens=MAX_TOKENS,
                        system=self._system_blocks(),
                        messages=[{"role": "user", "content": user}],
                    )
                else:
                    raise
        except Exception as exc:  # noqa: BLE001 - any API failure -> scripted
            self._log(f"API call failed at hole {hole} on {self.model}: "
                      f"{exc!r}")
            index = self._models.index(self.model) \
                if self.model in self._models else 0
            if index + 1 < len(self._models):
                self.model = self._models[index + 1]
                self._log(f"switching to {self.model} for the next hole")
            return scripted_submission("literalist", hole, observation)
        if getattr(response, "stop_reason", None) == "refusal":
            self._log(f"model refused at hole {hole}; playing scripted")
            return scripted_submission("literalist", hole, observation)
        text = "".join(getattr(b, "text", "")
                       for b in getattr(response, "content", [])
                       if getattr(b, "type", "") == "text")
        payload = parse_reply(text)
        if payload is None:
            self._log("falling back (unparseable reply)")
            return scripted_submission("literalist", hole, observation)
        return {"impl": payload.get("impl"),
                "tests": payload.get("tests") or [],
                "note": payload.get("note") or ""}


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    main_for(LLMPolicy)
