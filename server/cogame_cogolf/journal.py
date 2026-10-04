"""Private engine evidence; final publication requires every admitted owner joined."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, StrictBool, StrictInt

from players.native import Attempt, PrivateModel

from .config import GameConfig
from .private_window import Admission, ObservationPacket
from .submission import (
    normalize_submission,
    parse_reply,
    sanitize_submission,
    validate_submission_message,
)
from .values import equal
from .version import GAME_VERSION


class SourceIdentity(PrivateModel):
    episode_id: str = Field(min_length=1)
    source_revision: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    game_version: str = Field(min_length=1)
    image_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class LabelAttempt(PrivateModel):
    attempt_id: str
    policy: str
    origin: Literal["model", "teacher", "fallback", "unknown"]
    inference_mode: Literal["text_action"] = "text_action"
    platform_call_id: UUID | None = None
    prompt: JsonValue | None = None
    request: JsonValue | None = None
    response: JsonValue | None = None
    raw_response: JsonValue | None = None
    response_headers: dict[str, str] | None = None
    response_body_b64: str | None = None
    response_complete: StrictBool | None = None
    response_reader_joined: StrictBool | None = None
    http_status: StrictInt | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = None
    model: str | None = None
    model_identity: str | None = None
    tokenizer_identity: str | None = None
    chat_template_sha256: str | None = None
    decoder: JsonValue | None = None
    prompt_token_ids: list[int] | None = None
    sampled_token_ids: list[int] | None = None
    stop_reason: str | None = None
    parsed_action: JsonValue | None = None
    accepted: bool
    rejection_reason: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    behavior_logprobs: list[float] | None = None


class Decision(PrivateModel):
    schema_version: Literal["1"] = "1"
    event_type: Literal["decision"] = "decision"
    event_id: UUID = Field(default_factory=uuid4)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    episode_id: str
    decision_id: str
    decision_index: int
    game: Literal["cogolf"] = "cogolf"
    game_version: str
    source_revision: str
    image_digest: str | None
    seat: str
    visibility: Literal["private"] = "private"
    observation: JsonValue
    prompt: JsonValue
    attempts: list[LabelAttempt]
    selected_attempt_id: str
    executed_action: JsonValue
    action_status: Literal["accepted", "fallback"]
    fallback_origin: str | None = None
    reward: JsonValue
    terminal: bool = False


class Terminal(PrivateModel):
    schema_version: Literal["1"] = "1"
    event_type: Literal["episode"] = "episode"
    event_id: UUID = Field(default_factory=uuid4)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    episode_id: str
    seed_family: str
    game: Literal["cogolf"] = "cogolf"
    game_version: str
    source_revision: str
    image_digest: str | None
    status: Literal["completed", "truncated", "failed"]
    outcome: JsonValue
    participant_outcomes: JsonValue


class Complete(PrivateModel):
    schema_version: Literal["1"] = "1"
    episode: Terminal
    decisions: list[Decision]


def prompt_messages(window: ObservationPacket) -> list[dict[str, str]]:
    """Project text blocks exactly as the native checkpoint's chat template does."""
    request = window.native_request()
    assert isinstance(request.system, list)
    messages = [
        {"role": "system", "content": "\n".join(block.text for block in request.system)}
    ]
    for message in request.messages:
        assert isinstance(message.content, str)
        messages.append({"role": message.role, "content": message.content})
    return messages


class Journal:
    """Synchronous owned spool stays writable until actual engine/player joins."""

    def __init__(
        self,
        path: Path,
        identity: SourceIdentity,
        seed: int,
        admissions: list[Admission],
        policies: list[str],
        config: GameConfig,
    ):
        if len(admissions) != 2 or len(policies) != 2:
            raise ValueError("Cogolf private journal requires its two actual seats")
        self.path = path
        self.identity = identity
        self.seed = seed
        self.configuration = config.to_dict() | {
            "seed": seed,
            "rules_version": GAME_VERSION,
            "native_profiles": [
                profile.model_dump(mode="json") for profile in config.native_profiles
            ],
        }
        self.admissions = admissions
        self.policies = policies
        self.windows: dict[tuple[int, int], list[ObservationPacket]] = {}
        self.decisions: list[Decision] = []
        self.applications: list[tuple[int, list[dict], dict]] = []
        self.teachers: dict[UUID, LabelAttempt] = {}
        self.sealed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self.spool_path = path.with_suffix(path.suffix + ".partial")
        if path.exists():
            raise FileExistsError("private complete archive already exists")
        self.spool = os.fdopen(
            os.open(self.spool_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
            "w",
            encoding="utf-8",
        )

    @classmethod
    def from_environment(
        cls,
        seed: int,
        admissions: list[Admission],
        policies: list[str],
        config: GameConfig,
    ) -> Journal | None:
        if "COGAME_SAVE_TRAJECTORY_URI" not in os.environ:
            return None
        parsed = urlsplit(os.environ["COGAME_SAVE_TRAJECTORY_URI"])
        if (
            parsed.scheme != "file"
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or not Path(parsed.path).is_absolute()
        ):
            raise ValueError("private trajectory requires an absolute file URI")
        identity = SourceIdentity(
            episode_id=os.environ["COWORLD_EPISODE_ID"],
            source_revision=os.environ["COWORLD_SOURCE_REVISION"],
            game_version=os.environ["COWORLD_GAME_VERSION"],
            image_digest=os.environ.get("COWORLD_GAME_IMAGE_DIGEST"),
        )
        return cls(Path(parsed.path), identity, seed, admissions, policies, config)

    def append(self, kind: str, value: dict) -> None:
        if self.sealed:
            raise ValueError("private journal is sealed")
        self.spool.write(
            json.dumps(
                {"kind": kind, "value": value}, ensure_ascii=False, allow_nan=False
            )
            + "\n"
        )
        self.spool.flush()

    def window(self, window: ObservationPacket) -> None:
        self.windows.setdefault((window.hole, window.slot), []).append(
            window.model_copy(deep=True)
        )
        self.append("window", window.model_dump(mode="json"))

    def progress(self, request_id: UUID, attempt: Attempt) -> None:
        self.append(
            "received_attempt",
            {"request_id": str(request_id), "attempt": attempt.model_dump(mode="json")},
        )

    def teacher(
        self, window: ObservationPacket, policy: str, response: str, installed: dict
    ) -> None:
        parsed = normalize_submission(
            parse_reply(response),
            window.hole,
            max_tests=window.observation.rules.max_tests_per_hole,
        )
        accepted, cause = validate_submission_message(parsed, window.hole)
        if cause is not None:
            raise ValueError("source teacher does not pass ordinary native parser")
        assert accepted is not None
        action = sanitize_submission(
            accepted, window.hole, window.observation.rules.max_tests_per_hole
        )
        if not equal(action, installed):
            raise ValueError("teacher parser differs from intended installed control")
        row = LabelAttempt(
            attempt_id=str(uuid4()),
            policy=policy,
            origin="teacher",
            prompt=prompt_messages(window),
            response=response,
            parsed_action=action,
            accepted=False,
        )
        self.teachers[window.request_id] = row
        self.append(
            "source_teacher",
            {
                "request_id": str(window.request_id),
                "attempt": row.model_dump(mode="json"),
            },
        )

    def applied(self, hole: int, submissions: list[dict], effects: dict) -> None:
        self.append(
            "engine_application",
            {"hole": hole, "installed_actions": submissions, "effects": effects},
        )
        self.applications.append(
            (hole, json.loads(json.dumps(submissions)), json.loads(json.dumps(effects)))
        )

    def _decisions_for_application(
        self, hole: int, submissions: list[dict], effects: dict
    ) -> None:
        for slot, executed in enumerate(submissions):
            windows = self.windows[(hole, slot)]
            attempts: list[LabelAttempt] = []
            selected = None
            admission = self.admissions[slot]
            for window in windows:
                submitted = admission.accepted.get(window.request_id)
                chosen = None if submitted is None else submitted.selected_attempt_id
                for attempt in admission.attempts.get(window.request_id, {}).values():
                    accepted = (
                        chosen == attempt.attempt_id
                        and effects["seats"][slot]["fallback"] is None
                    )
                    parsed = (
                        window.bind_model_action(attempt, submitted.action)
                        if accepted and submitted is not None
                        else None
                    )
                    sample = attempt.sampling_evidence
                    usage = attempt.usage
                    row = LabelAttempt(
                        attempt_id=attempt.attempt_id,
                        policy=self.policies[slot],
                        origin="model",
                        platform_call_id=attempt.platform_call_id,
                        prompt=prompt_messages(window),
                        request=attempt.request.model_dump(
                            mode="json", exclude_none=True
                        ),
                        response=attempt.response,
                        raw_response=attempt.raw_response,
                        response_headers=attempt.response_headers or None,
                        response_body_b64=attempt.response_body_b64,
                        response_complete=attempt.response_complete,
                        response_reader_joined=attempt.response_reader_joined,
                        http_status=attempt.http_status,
                        provider_request_id=attempt.provider_request_id,
                        model=attempt.model,
                        model_identity=attempt.model_identity,
                        tokenizer_identity=attempt.tokenizer_identity,
                        chat_template_sha256=attempt.chat_template_sha256,
                        decoder={
                            "temperature": attempt.request.temperature,
                            "top_p": attempt.request.top_p,
                            "max_tokens": attempt.request.max_tokens,
                            "timeout_ms": attempt.timeout_ms,
                        },
                        prompt_token_ids=None
                        if sample is None
                        else sample.prompt_token_ids,
                        sampled_token_ids=None
                        if sample is None
                        else sample.completion_token_ids,
                        behavior_logprobs=None
                        if sample is None
                        else sample.behavior_log_probs,
                        stop_reason=attempt.stop_reason,
                        parsed_action=parsed,
                        accepted=accepted,
                        rejection_reason=attempt.rejection_reason,
                        latency_ms=attempt.latency_ms,
                        input_tokens=None if usage is None else usage.input_tokens,
                        output_tokens=None if usage is None else usage.output_tokens,
                    )
                    attempts.append(row)
                    if accepted:
                        if not equal(parsed, executed):
                            raise ValueError(
                                "independent native parse differs from installed engine control"
                            )
                        selected = row.attempt_id
                if window.request_id in self.teachers:
                    row = self.teachers[window.request_id]
                    accepted = effects["seats"][slot]["fallback"] is None and equal(
                        row.parsed_action, executed
                    )
                    row = row.model_copy(update={"accepted": accepted})
                    attempts.append(row)
                    if accepted:
                        selected = row.attempt_id
            fallback = effects["seats"][slot]["fallback"]
            if selected is None:
                row = LabelAttempt(
                    attempt_id=str(uuid4()),
                    policy="literalist" if fallback else self.policies[slot],
                    origin="fallback" if fallback else "unknown",
                    parsed_action=executed,
                    accepted=True,
                )
                attempts.append(row)
                selected = row.attempt_id
            decision = Decision(
                **self.identity.model_dump(),
                decision_id=f"hole-{hole}-seat-{slot}",
                decision_index=len(self.decisions),
                seat=str(slot),
                observation={
                    "windows": [w.model_dump(mode="json") for w in windows],
                    "engine_effects": effects["seats"][slot],
                },
                prompt=prompt_messages(windows[-1]),
                attempts=attempts,
                selected_attempt_id=selected,
                executed_action=executed,
                action_status="fallback" if fallback else "accepted",
                fallback_origin="literalist" if fallback else None,
                reward=effects["hole_score"][slot],
            )
            self.decisions.append(decision)

    def finish(self, outcome: dict, *, owners_joined: bool) -> Complete:
        if self.sealed:
            raise ValueError("private journal was already finalized")
        if not owners_joined or any(
            admission.has_unsettled_readers() for admission in self.admissions
        ):
            raise ValueError(
                "private final archive requires actual writer/player joins"
            )
        for hole, submissions, effects in self.applications:
            self._decisions_for_application(hole, submissions, effects)
        status = "completed" if outcome["reason"] == "complete" else "truncated"
        terminal = Terminal(
            **self.identity.model_dump(),
            seed_family=f"cogolf:{self.seed}",
            status=status,
            outcome={
                "results": outcome,
                "runtime_configuration": self.configuration,
                "slot_to_engine_seat": {"0": 0, "1": 1},
            },
            participant_outcomes=[
                {"seat": str(slot), "score": score}
                for slot, score in enumerate(outcome["scores"])
            ],
        )
        complete = Complete(episode=terminal, decisions=self.decisions)
        self.append("terminal", terminal.model_dump(mode="json"))
        staging = self.path.with_suffix(self.path.suffix + ".staging")
        with os.fdopen(
            os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
            "w",
            encoding="utf-8",
        ) as stream:
            stream.write(complete.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, self.path)
        self.spool.close()
        self.sealed = True
        return complete
