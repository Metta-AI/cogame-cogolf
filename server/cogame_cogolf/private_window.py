"""Engine-owned native admission window and response-to-control binding."""

import base64
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt

from players.native import Attempt, PrivateModel, Request, Response

from .contract import ALIASES
from .guidance import API_DOCS
from .native_profile import NativeProfile
from .prompt import PromptProfile
from .submission import (
    normalize_submission,
    parse_reply,
    sanitize_submission,
    validate_submission_message,
)
from .values import equal


class Rules(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )
    max_tests_per_hole: StrictInt = Field(ge=1, le=5)
    max_impl_chars: StrictInt
    max_test_name_chars: StrictInt
    max_why_chars: StrictInt
    max_args_chars: StrictInt
    max_expect_chars: StrictInt
    max_note_chars: StrictInt
    max_message_bytes: StrictInt
    par_tests_per_hole: StrictInt
    call_cpu_seconds: float = Field(gt=0)
    blocked: list[str]


class Spec(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )
    key: str
    title: str
    prompt: str
    signature: dict[str, JsonValue]
    examples: list[dict[str, JsonValue]]


class Seat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    alias: Literal["Ash", "Basil"]
    slot: StrictInt = Field(ge=0, le=1)
    score: StrictInt


class Observation(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )
    hole: StrictInt = Field(gt=0)
    holes: StrictInt = Field(gt=0)
    spec: Spec
    you: Seat
    opponent: Seat
    history: list[dict[str, JsonValue]]
    rules: Rules


class PrivateWindow(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )
    request_id: UUID = Field(default_factory=uuid4)
    slot: StrictInt = Field(ge=0, le=1)
    hole: StrictInt = Field(gt=0)
    retry: StrictBool
    profile: NativeProfile
    observation: Observation

    def native_request(self) -> Request:
        prompt = PromptProfile(
            strategy=self.profile.strategy,
            alias=ALIASES[self.slot],
            api_docs=API_DOCS[:12000],
        )
        return prompt.request(
            self.hole,
            self.observation.model_dump(),
            model=self.profile.model,
            temperature=self.profile.temperature,
            max_tokens=self.profile.max_tokens,
        )

    def bind_model_action(self, attempt: Attempt, separately_submitted: dict) -> dict:
        """Parse actual native text independently before the engine installs action."""
        if attempt.slot != self.slot or attempt.request != self.native_request():
            raise ValueError("native attempt differs from immutable engine window")
        if (
            attempt.http_status != 200
            or attempt.response_complete is not True
            or attempt.response_reader_joined is not True
        ):
            raise ValueError("native response ownership is incomplete")
        if attempt.raw_response is None or attempt.response_body_b64 is None:
            raise ValueError("native response lacks received body evidence")
        raw = base64.b64decode(attempt.response_body_b64, validate=True)
        if raw.decode("utf-8") != attempt.raw_response:
            raise ValueError("native response text differs from received bytes")
        observed = Response.model_validate_json(attempt.raw_response)
        if observed.text != attempt.response or observed.model != attempt.model:
            raise ValueError("native completion differs from captured response")
        if (
            observed.sampling_evidence != attempt.sampling_evidence
            or observed.usage != attempt.usage
        ):
            raise ValueError("native sampling or usage differs from received response")
        expected_stop = observed.stop_reason
        if observed.sampling_evidence is not None:
            expected_stop = observed.sampling_evidence.stop_reason
        if attempt.stop_reason != expected_stop:
            raise ValueError("native stop differs from received response")
        if attempt.platform_call_id != attempt.response_headers.get(
            "x-softmax-llm-call-id"
        ):
            raise ValueError("native platform call differs from received header")
        parsed = normalize_submission(
            parse_reply(observed.text),
            self.hole,
            max_tests=self.observation.rules.max_tests_per_hole,
        )
        if parsed is None:
            raise ValueError("selected completion does not parse to a submission")
        accepted, cause = validate_submission_message(parsed, self.hole)
        if cause is not None:
            raise ValueError("selected completion fails ordinary wire validation")
        assert accepted is not None
        actual, actual_cause = validate_submission_message(
            separately_submitted, self.hole
        )
        if actual_cause is not None:
            raise ValueError("separate submission fails ordinary wire validation")
        assert actual is not None
        max_tests = self.observation.rules.max_tests_per_hole
        expected_control = sanitize_submission(accepted, self.hole, max_tests)
        submitted_control = sanitize_submission(actual, self.hole, max_tests)
        if not equal(expected_control, submitted_control):
            raise ValueError(
                "native response differs from separately submitted control"
            )
        return expected_control


class ObservationPacket(PrivateWindow):
    type: Literal["observation"] = "observation"
    deadline_seconds: float = Field(gt=0)


class Ready(PrivateModel):
    type: Literal["ready"] = "ready"
    slot: StrictInt = Field(ge=0, le=1)
    profile: NativeProfile


class Started(BaseModel):
    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, allow_inf_nan=False
    )
    type: Literal["attempt_started"] = "attempt_started"
    request_id: UUID
    attempt: Attempt


class Action(BaseModel):
    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, allow_inf_nan=False
    )
    type: Literal["action"] = "action"
    request_id: UUID
    action: dict[str, JsonValue]
    selected_attempt_id: str | None = None


class Admission:
    """One seat's immutable windows and monotone received attempt snapshots."""

    def __init__(self, slot: int):
        self.slot = slot
        self.windows: dict[UUID, PrivateWindow] = {}
        self.attempts: dict[UUID, dict[str, Attempt]] = {}
        self.active: UUID | None = None
        self.accepted: dict[UUID, Action] = {}
        self.stopping = False

    def issue(self, window: PrivateWindow) -> None:
        if self.stopping or self.active is not None or window.slot != self.slot:
            raise ValueError("decision admission is closed or owned by another window")
        if window.request_id in self.windows:
            raise ValueError("decision window identifier was reused")
        self.windows[window.request_id] = window.model_copy(deep=True)
        self.attempts[window.request_id] = {}
        self.active = window.request_id

    def progress(self, request_id: UUID, attempt: Attempt) -> None:
        if request_id not in self.windows:
            raise ValueError("native attempt has no issued decision window")
        window = self.windows[request_id]
        if attempt.slot != self.slot or attempt.request != window.native_request():
            raise ValueError(
                "started native request differs from immutable engine window"
            )
        rows = self.attempts[request_id]
        if attempt.attempt_id not in rows:
            if len(rows) >= 4:
                raise ValueError("native attempt count exceeds source retry policy")
            if request_id != self.active or self.stopping:
                raise ValueError("native attempt started outside its admission window")
            if any(attempt.attempt_id in rows for rows in self.attempts.values()):
                raise ValueError("native attempt identifier belongs to another window")
        else:
            previous = rows[attempt.attempt_id]
            for field in (
                "request",
                "slot",
                "purpose",
                "stage",
                "protocol",
                "inference_mode",
                "timeout_ms",
                "http_status",
                "platform_call_id",
                "provider_request_id",
                "model_identity",
                "tokenizer_identity",
                "chat_template_sha256",
            ):
                old = getattr(previous, field)
                if old is not None and getattr(attempt, field) != old:
                    raise ValueError(
                        "native attempt rewrote observed immutable metadata"
                    )
            if (
                previous.received_header_pairs
                and attempt.received_header_pairs != previous.received_header_pairs
            ):
                raise ValueError("native attempt rewrote received header pairs")
            if (
                previous.response_headers
                and attempt.response_headers != previous.response_headers
            ):
                raise ValueError("native attempt rewrote received headers")
            if previous.response_body_b64 is not None:
                if attempt.response_body_b64 is None:
                    raise ValueError("native attempt removed received body bytes")
                old_body = base64.b64decode(previous.response_body_b64, validate=True)
                new_body = base64.b64decode(attempt.response_body_b64, validate=True)
                if not new_body.startswith(old_body):
                    raise ValueError("native attempt rewrote received body prefix")
                if previous.response_complete is True and new_body != old_body:
                    raise ValueError("native attempt changed completed received body")
            if (
                previous.response_complete is True
                and attempt.response_complete is not True
            ):
                raise ValueError("native attempt reversed observed HTTP completion")
            if (
                previous.response_reader_joined is True
                and attempt.response_reader_joined is not True
            ):
                raise ValueError("native attempt reversed observed reader join")
        self.attempts[request_id][attempt.attempt_id] = attempt.model_copy(deep=True)

    def consume(self, packet: Action) -> dict:
        if self.stopping or packet.request_id != self.active:
            raise ValueError("action arrived outside its issued window")
        if packet.selected_attempt_id is not None:
            if packet.selected_attempt_id not in self.attempts[packet.request_id]:
                raise ValueError("selected native attempt was not observed")
            self.windows[packet.request_id].bind_model_action(
                self.attempts[packet.request_id][packet.selected_attempt_id],
                packet.action,
            )
        self.accepted[packet.request_id] = packet.model_copy(deep=True)
        self.active = None
        return packet.action

    def close_window(self, request_id: UUID) -> None:
        if self.active == request_id:
            self.active = None

    def stop_admission(self) -> None:
        self.stopping = True
        self.active = None

    def has_unsettled_readers(self) -> bool:
        return any(
            attempt.response_reader_joined is False
            for rows in self.attempts.values()
            for attempt in rows.values()
        )


class Stop(BaseModel):
    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, allow_inf_nan=False
    )
    type: Literal["stop"] = "stop"
    stop_id: UUID
    deadline_seconds: float = Field(gt=0, le=2)


class Stopped(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    type: Literal["stopped"] = "stopped"
    stop_id: UUID
    owners_joined: StrictBool


class EvidenceReceived(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    type: Literal["evidence_received"] = "evidence_received"
    stop_id: UUID
