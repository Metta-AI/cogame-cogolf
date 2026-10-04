"""Native Messages policy for the source-owned Cogolf programming profile.

The model and decoder remain frozen throughout a match. Received attempts are
private; resolved submission notes remain public communications under the rules.
"""

from __future__ import annotations

import asyncio
import math
import os
from urllib.parse import parse_qs, urlsplit

import httpx
from cogame_cogolf import lifecycle
from cogame_cogolf.contract import MAX_NOTE_CHARS
from cogame_cogolf.native_profile import NativeProfile
from cogame_cogolf.prompt import PromptProfile
from cogame_cogolf.submission import parse_reply
from pydantic import ValidationError

from players import native
from players.client import Policy, main_for
from players.scripted import scripted_submission

DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
DEFAULT_TIMEOUT_SECONDS = 32.0
DEFAULT_HOLE_BUDGET_SECONDS = 36.0
RETRY_BACKOFFS = (0.5, 1.0, 2.0)
_TRANSIENT_STATUSES = frozenset({408, 409, 429})


def _fallback_note(reason: str, original: str) -> str:
    """``fallback:<reason>`` plus the scripted note when it fits in the cap."""
    note = f"fallback:{reason}"
    if original:
        combined = f"{note}; {original}"
        if len(combined) <= MAX_NOTE_CHARS:
            return combined
    return note[:MAX_NOTE_CHARS]


class LLMPolicy(Policy):
    """Actual native attempts followed by the ordinary submission parser."""

    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: float | None = None,
        strategy: str | None = None,
        hole_budget_seconds: float | None = None,
    ):
        super().__init__()
        if not os.environ["COWORLD_LLM_ENDPOINT"]:
            raise ValueError(
                "native language profile requires its registered sidecar endpoint"
            )
        slot = int(
            parse_qs(urlsplit(os.environ["COWORLD_PLAYER_WS_URL"]).query)["slot"][0]
        )
        self.scope = native.LearnerScope(
            slot=slot, model=model or os.environ.get("COWORLD_LLM_MODEL", DEFAULT_MODEL)
        )
        self.timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else float(os.environ.get("COGAME_LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
        )
        self.hole_budget = (
            hole_budget_seconds
            if hole_budget_seconds is not None
            else float(
                os.environ.get("COGAME_LLM_HOLE_BUDGET", DEFAULT_HOLE_BUDGET_SECONDS)
            )
        )
        if not all(
            math.isfinite(value) and value > 0
            for value in (self.timeout, self.hole_budget)
        ):
            raise ValueError("native policy budgets must be finite and positive")
        self.temperature = float(os.environ.get("COWORLD_LLM_TEMPERATURE", "0.7"))
        self.profile = PromptProfile(
            strategy=(
                strategy
                if strategy is not None
                else os.environ.get("PLAYER_PROMPT", "")
            )
        )
        self.configured_model = model or os.environ.get("COWORLD_LLM_MODEL")
        self.configured_temperature = os.environ.get("COWORLD_LLM_TEMPERATURE")
        self.configured_strategy = (
            strategy if strategy is not None else os.environ.get("PLAYER_PROMPT")
        )
        self.max_tokens = 1800
        self.attempts: list[native.Attempt] = []
        self.progress: native.Progress | None = None

    def on_welcome(self, welcome: dict) -> None:
        if welcome["slot"] != self.scope.slot:
            raise ValueError("welcome seat differs from frozen native caller")
        registered = NativeProfile.model_validate(welcome["native_profile"])
        chosen = registered.model_dump()
        if self.configured_model is not None:
            chosen["model"] = self.configured_model
        if self.configured_temperature is not None:
            chosen["temperature"] = float(self.configured_temperature)
        if self.configured_strategy is not None:
            chosen["strategy"] = self.configured_strategy
        registered = NativeProfile.model_validate(chosen)
        self.native_profile = registered
        self.scope = native.LearnerScope(
            slot=self.scope.slot, model=registered.model, endpoint=self.scope.endpoint
        )
        self.temperature = registered.temperature
        self.max_tokens = registered.max_tokens
        self.profile = PromptProfile(strategy=registered.strategy)
        self.profile = self.profile.model_copy(
            update={"alias": welcome["alias"], "api_docs": welcome["api_docs"][:12000]}
        )

    def readers_joined(self) -> bool:
        return all(
            attempt.response_reader_joined is not False for attempt in self.attempts
        )

    def _degrade(self, reason: str, hole: int, observation: dict) -> dict:
        played = scripted_submission("literalist", hole, observation)
        played["note"] = _fallback_note(reason, played["note"])
        return played

    async def submission(self, hole: int, observation: dict) -> dict:
        if not self.readers_joined():
            raise lifecycle.OwnershipUnsettled(
                "new hole admitted before prior native reader joined"
            )
        self.attempts = []
        deadline = asyncio.get_running_loop().time() + self.hole_budget
        async with lifecycle.own_until(deadline + lifecycle.CLEANUP_SECONDS):
            request = self.profile.request(
                hole,
                observation,
                model=self.scope.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            for index in range(len(RETRY_BACKOFFS) + 1):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return self._degrade("provider_error", hole, observation)
                attempt = native.Attempt(
                    slot=self.scope.slot, stage="submission", request=request
                )
                self.attempts.append(attempt)
                call = lifecycle.owned_task(
                    native.complete(
                        request,
                        self.scope,
                        stage="submission",
                        timeout=min(self.timeout, remaining),
                        attempt=attempt,
                        progress=self.progress,
                    )
                )
                _done, pending = await asyncio.wait(
                    {call},
                    timeout=min(self.timeout, remaining) + lifecycle.CLEANUP_SECONDS,
                )
                if pending:
                    joined = await lifecycle.settle(
                        {call}, lifecycle.cleanup_deadline(), cancel=True
                    )
                    if not joined:
                        raise lifecycle.OwnershipUnsettled(
                            "native hole request owner did not settle"
                        )
                    raise TimeoutError(
                        "native hole request owner exceeded its complete lifecycle budget"
                    )
                if call.cancelled():
                    raise asyncio.CancelledError
                error = call.exception()
                if error is not None:
                    if not isinstance(
                        error,
                        (
                            httpx.RequestError,
                            TimeoutError,
                            ValidationError,
                            native.ResponseRejected,
                        ),
                    ):
                        call.result()
                    transient = (
                        isinstance(error, (httpx.RequestError, TimeoutError))
                        or attempt.http_status in _TRANSIENT_STATUSES
                        or (
                            attempt.http_status is not None
                            and attempt.http_status >= 500
                        )
                    )
                    if not transient:
                        return self._degrade("permanent_error", hole, observation)
                    if (
                        index == len(RETRY_BACKOFFS)
                        or deadline - asyncio.get_running_loop().time()
                        <= RETRY_BACKOFFS[index] + 1
                    ):
                        return self._degrade("provider_error", hole, observation)
                    await asyncio.sleep(RETRY_BACKOFFS[index])
                    continue
                response = call.result()
                if response.stop_reason == "refusal":
                    return self._degrade("refusal", hole, observation)
                payload = parse_reply(response.text)
                if payload is None:
                    return self._degrade("unparseable", hole, observation)
                self.selected_attempt_id = attempt.attempt_id
                return {
                    "impl": payload["impl"],
                    "tests": payload.get("tests") or [],
                    "note": payload.get("note") or "",
                }
            raise AssertionError("native retry loop did not produce a submission")


if __name__ == "__main__":
    main_for(LLMPolicy)
