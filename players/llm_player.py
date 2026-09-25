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

Degrade, never hang: every model call is bounded by
``min(COGAME_LLM_TIMEOUT, budget - elapsed)`` where the hole budget is
``COGAME_LLM_HOLE_BUDGET`` (36 s, four seconds inside the manifest's 40 s
hole deadline) measured from the start of ``submission()``. A TRANSIENT
failure (timeout, connection error, HTTP 408/409/429/5xx — the sidecar's
``503 LLM provider is unavailable`` included) rotates to the next model
candidate and is retried after a short backoff (0.5, 1, 2 s), but only
while ``elapsed + backoff + 1 s`` still fits in the budget; the SDK's own
retry is disabled at the call site so this loop is the only retry policy
and the hole can never overrun. A PERMANENT failure (4xx other than the
three above, parse errors), an exhausted budget, a refusal or unparseable
text substitutes the scripted ``literalist`` submission for this hole with
its ``note`` set to ``fallback:<reason>`` so a client-side substitution is
distinguishable in the replay. The harness never sends a noop.

``PLAYER_PROMPT`` is appended to the system preamble as the policy's
strategy paragraph: a policy is just a prompt.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error

from players.client import MAX_NOTE_CHARS, Policy, main_for
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
SIDECAR_MODEL_CANDIDATES = [
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.5",
]

# An implementation of ~60 lines plus five test records; 400/900 truncate
# mid-function.
MAX_TOKENS = 1800
DEFAULT_TIMEOUT_SECONDS = 32.0
# The manifest's default `hole_deadline_seconds` is 40; keep 4 s of margin
# for the websocket round trip and the harness's own deadline margin.
DEFAULT_HOLE_BUDGET_SECONDS = 36.0
# Sleeps between retries of a transient provider failure. A retry is only
# attempted while `elapsed + backoff + 1.0 < budget`.
RETRY_BACKOFFS = (0.5, 1.0, 2.0)
# Reasons a hole is played by the scripted literalist instead of the model;
# each lands in the submission `note` as `fallback:<reason>`.
FALLBACK_REASONS = ("provider_error", "permanent_error", "refusal",
                    "unparseable", "no_client")
MAX_PROMPT_CHARS = 6000
MAX_HISTORY_HOLES = 4
MAX_HISTORY_ENTRY_CHARS = 1200
MAX_API_DOCS_CHARS = 12000

_PY_FENCE_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)\s*\n(.*?)```", re.DOTALL)
# HTTP statuses (besides 5xx) that mean "try again": request timeout,
# conflict, and throttling.
_TRANSIENT_STATUSES = frozenset({408, 409, 429})
# `_BedrockHttpClient.create` wraps every transport failure in a
# RuntimeError carrying the urllib exception's repr, e.g.
# `<HTTPError 503: ''>`, `URLError(timeout('timed out'))`,
# `ConnectionResetError(...)`. When the repr names an HTTP status that
# status decides; otherwise a transport-level substring does.
_WRAPPED_STATUS_RE = re.compile(r"HTTPError (\d{3})")
_TRANSIENT_MESSAGE_RE = re.compile(
    r"timed out|timeout|URLError|Connection", re.IGNORECASE)

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


def _hole_budget_from_env() -> float:
    raw = os.environ.get("COGAME_LLM_HOLE_BUDGET", "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_HOLE_BUDGET_SECONDS


def _is_transient(exc: BaseException) -> bool:
    """True when ``exc`` is a provider failure worth retrying within the hole.

    Transient: SDK timeouts and connection errors, HTTP 408/409/429/5xx (from
    the SDK or from ``_BedrockHttpClient``'s wrapped urllib error), socket
    timeouts and connection-level ``OSError`` subclasses. Everything else —
    400/401/403/404/422, parse problems, refusals — is permanent for this
    hole: no retry.
    """
    try:
        import anthropic  # noqa: PLC0415 - optional dependency
    except Exception:  # noqa: BLE001
        anthropic = None
    if anthropic is not None:
        # APITimeoutError is an APIConnectionError subclass.
        if isinstance(exc, anthropic.APIConnectionError):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            status = getattr(exc, "status_code", None)
            return isinstance(status, int) and (
                status in _TRANSIENT_STATUSES or status >= 500)
    if isinstance(exc, urllib.error.HTTPError):
        code = getattr(exc, "code", None)
        return isinstance(code, int) and (
            code in _TRANSIENT_STATUSES or code >= 500)
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError,
                        socket.gaierror, socket.herror,
                        urllib.error.URLError)):
        return True
    if isinstance(exc, RuntimeError):
        text = f"{exc!r} {exc}"
        status = _WRAPPED_STATUS_RE.search(text)
        if status:
            code = int(status.group(1))
            return code in _TRANSIENT_STATUSES or code >= 500
        return bool(_TRANSIENT_MESSAGE_RE.search(text))
    return False


def _fallback_note(reason: str, original: str) -> str:
    """``fallback:<reason>`` plus the scripted note when it fits in the cap."""
    note = f"fallback:{reason}"
    if original:
        combined = f"{note}; {original}"
        if len(combined) <= MAX_NOTE_CHARS:
            return combined
    return note[:MAX_NOTE_CHARS]


def _invoke(client, model: str, system, user: str, timeout: float):
    """One ``messages.create`` bounded by ``timeout`` with SDK retries off —
    the policy's own loop is the retry policy, so a call can never take
    longer than the timeout it was given."""
    with_options = getattr(client, "with_options", None)
    bound = client
    if callable(with_options):
        bound = with_options(timeout=timeout, max_retries=0)
    return bound.messages.create(
        model=model, max_tokens=MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": user}],
    )


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
        self.token = ("" if os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME")
                      else os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip())
        self.timeout = timeout
        self.messages = self  # so `client.messages.create(...)` works

    def with_options(self, *, timeout: float | None = None, **_kwargs):
        if timeout is None:
            return self
        bound = self.__class__.__new__(self.__class__)
        bound.__dict__.update(self.__dict__)
        bound.timeout = timeout
        bound.messages = bound
        return bound

    def create(self, *, model: str, max_tokens: int, system, messages):
        sidecar = bool(os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME"))
        body = {"max_tokens": max_tokens, "system": system,
                "messages": messages}
        if sidecar:
            body["model"] = model
        else:
            body["anthropic_version"] = "bedrock-2023-05-31"
        req = self._urllib.Request(
            (f"{self.endpoint}/v1/messages" if sidecar else
             f"{self.endpoint}/model/{model}/invoke"),
            data=json.dumps(body).encode(), method="POST",
            headers={"content-type": "application/json",
                     "accept": "application/json",
                     **({"anthropic-version": "2023-06-01"} if sidecar else {}),
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
                 strategy: str | None = None,
                 hole_budget_seconds: float | None = None):
        self.provider = (provider or _provider_from_env()).lower()
        pinned = model or os.environ.get("COGAME_LLM_MODEL") or (
            os.environ.get("BEDROCK_MODEL") if self.provider == "bedrock"
            else None)
        if self.provider == "bedrock":
            candidates = (SIDECAR_MODEL_CANDIDATES
                          if os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME")
                          else BEDROCK_MODEL_CANDIDATES)
            self._models = [m for m in ([pinned] if pinned else [])
                            + candidates if m]
            self._models = list(dict.fromkeys(self._models))
        else:
            self._models = [pinned or DEFAULT_MODEL]
        self.model = self._models[0]
        self.timeout = timeout_seconds or float(
            os.environ.get("COGAME_LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
        self.hole_budget = (hole_budget_seconds if hole_budget_seconds
                            else _hole_budget_from_env())
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

    def _degrade(self, reason: str, hole: int, observation: dict) -> dict:
        """The scripted literalist move, tagged so the replay can tell a
        client-side substitution from a real answer."""
        played = scripted_submission("literalist", hole, observation)
        played["note"] = _fallback_note(reason, played.get("note") or "")
        return played

    def _rotate_model(self, purpose: str) -> None:
        index = self._models.index(self.model) \
            if self.model in self._models else 0
        if index + 1 < len(self._models):
            self.model = self._models[index + 1]
            self._log(f"switching to {self.model} for {purpose}")

    def _call_model(self, client, user: str, deadline_at: float):
        """One model call (plus the caching-rejected retry) bounded by the
        time left before ``deadline_at``."""
        def timeout() -> float:
            return max(0.0, min(self.timeout, deadline_at - time.monotonic()))

        try:
            return _invoke(client, self.model, self._system_blocks(), user,
                           timeout())
        except Exception as exc:  # noqa: BLE001
            if self._cache_ok and "cache_control" in str(exc):
                self._cache_ok = False
                self._log("prompt caching rejected; retrying without it")
                return _invoke(client, self.model, self._system_blocks(),
                               user, timeout())
            raise

    def submission(self, hole: int, observation: dict) -> dict:
        started = time.monotonic()
        client = self._client_or_none()
        if client is None:
            return self._degrade("no_client", hole, observation)
        user = self.user_prompt(hole, observation)
        budget = self.hole_budget
        deadline_at = started + budget
        attempt = 0
        while True:
            try:
                response = self._call_model(client, user, deadline_at)
                break
            except Exception as exc:  # noqa: BLE001 - any API failure
                self._log(f"API call failed at hole {hole} on {self.model}: "
                          f"{exc!r}")
                if not _is_transient(exc):
                    self._rotate_model("the next hole")
                    return self._degrade("permanent_error", hole, observation)
                elapsed = time.monotonic() - started
                backoff = (RETRY_BACKOFFS[attempt]
                           if attempt < len(RETRY_BACKOFFS) else None)
                if backoff is None or elapsed + backoff + 1.0 >= budget:
                    self._rotate_model("the next hole")
                    self._log(f"no retry at hole {hole}: "
                              f"{attempt} retries used, {elapsed:.1f}s of "
                              f"the {budget:.1f}s budget elapsed; playing "
                              f"scripted")
                    return self._degrade("provider_error", hole, observation)
                self._rotate_model("the retry")
                self._log(f"transient failure at hole {hole}; retry "
                          f"{attempt + 1}/{len(RETRY_BACKOFFS)} in "
                          f"{backoff:.1f}s")
                attempt += 1
                time.sleep(backoff)
        if getattr(response, "stop_reason", None) == "refusal":
            self._log(f"model refused at hole {hole}; playing scripted")
            return self._degrade("refusal", hole, observation)
        text = "".join(getattr(b, "text", "")
                       for b in getattr(response, "content", [])
                       if getattr(b, "type", "") == "text")
        payload = parse_reply(text)
        if payload is None:
            self._log("falling back (unparseable reply)")
            return self._degrade("unparseable", hole, observation)
        return {"impl": payload.get("impl"),
                "tests": payload.get("tests") or [],
                "note": payload.get("note") or ""}


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    main_for(LLMPolicy)
