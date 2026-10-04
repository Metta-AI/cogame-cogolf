"""Real native HTTP bytes independently bind to ordinary engine-installed control."""

import asyncio
import base64
import json
from uuid import uuid4

import pytest
from cogame_cogolf.baseline import baseline
from cogame_cogolf.engine import Engine
from cogame_cogolf.private_window import ObservationPacket, PrivateWindow
from cogame_cogolf.submission import sanitize_submission

from players import native
from tests.conftest import make_config
from tests.fakes import FakeSandbox, ScriptedSource


async def test_actual_http_response_binds_and_different_wire_action_is_rejected(
    monkeypatch,
):
    config = make_config()
    engine = Engine(config, [ScriptedSource(), ScriptedSource()], FakeSandbox())
    spec = engine.deck["median"]
    observation = engine._observation_message(1, spec, 0, retry=False)["observation"]
    window = PrivateWindow(
        slot=0,
        hole=1,
        retry=False,
        profile=config.native_profiles[0],
        observation=observation,
    )
    action = baseline("literalist", spec, 1)
    action["tests"][0]["expect"] = (
        True  # Valid intent; differs from numeric1 under game equality.
    )
    response = {
        "model": window.profile.model,
        "content": [
            {"type": "thinking", "thinking": "private-model-sentinel"},
            {"type": "text", "text": json.dumps(action)},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 4, "output_tokens": 4},
    }
    body = (" \n" + json.dumps(response) + "\n ").encode()
    call_id = str(uuid4())
    requests = []
    handlers = set()

    async def receive(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            size = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            requests.append((headers, json.loads(await reader.readexactly(size))))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"X-Softmax-LLM-Call-ID: {call_id}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    attempt = native.Attempt(
        slot=0, stage="submission", request=window.native_request()
    )
    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("COWORLD_LLM_ENDPOINT", f"http://127.0.0.1:{port}")
    snapshots = []

    async def progress(snapshot):
        snapshots.append(snapshot)

    try:
        await native.complete(
            attempt.request,
            native.LearnerScope(slot=0, model=window.profile.model),
            stage="submission",
            timeout=2,
            attempt=attempt,
            progress=progress,
        )
    finally:
        server.close()
        await server.wait_closed()
        if handlers:
            _, pending = await asyncio.wait(handlers, timeout=1)
            assert not pending
            for task in handlers:
                task.result()
    assert requests[0][1] == window.native_request().model_dump(exclude_none=True)
    assert b"x-coworld-player-slot: 0" in requests[0][0].lower()
    assert attempt.raw_response.encode() == body
    assert base64.b64decode(attempt.response_body_b64) == body
    assert attempt.response_reader_joined is True
    assert attempt.platform_call_id == call_id
    assert window.bind_model_action(attempt, action) == sanitize_submission(
        action, 1, 5
    )
    changed = action | {"impl": "def solve(xs):\n    return 777\n"}
    with pytest.raises(ValueError, match="separately submitted control"):
        window.bind_model_action(attempt, changed)
    numeric = json.loads(json.dumps(action))
    numeric["tests"][0]["expect"] = 1
    with pytest.raises(ValueError, match="separately submitted control"):
        window.bind_model_action(attempt, numeric)
    unjoined = attempt.model_copy(update={"response_reader_joined": False})
    with pytest.raises(ValueError, match="ownership is incomplete"):
        window.bind_model_action(unjoined, action)
    assert snapshots[0].response_body_b64 is None
    assert snapshots[-1].raw_response == attempt.raw_response


def test_started_body_prefix_is_monotone_and_unknown_window_cannot_start():
    from cogame_cogolf.private_window import Admission

    config = make_config()
    engine = Engine(config, [ScriptedSource(), ScriptedSource()], FakeSandbox())
    observation = engine._observation_message(1, engine.deck["median"], 0, retry=False)[
        "observation"
    ]
    window = PrivateWindow(
        slot=0,
        hole=1,
        retry=False,
        profile=config.native_profiles[0],
        observation=observation,
    )
    owner = Admission(0)
    owner.issue(window)
    attempt = native.Attempt(
        slot=0, stage="submission", request=window.native_request()
    )
    owner.progress(window.request_id, attempt)
    observed = attempt.model_copy(
        update={
            "endpoint": "http://localhost:9100/",
            "http_status": 200,
            "response_complete": False,
            "response_reader_joined": False,
            "response_body_b64": base64.b64encode(b"{\xff").decode(),
            "received_header_pairs": [("x-softmax-llm-call-id", str(uuid4()))],
        }
    )
    owner.progress(window.request_id, observed)
    assert owner.has_unsettled_readers()
    with pytest.raises(ValueError, match="body prefix"):
        owner.progress(
            window.request_id,
            observed.model_copy(
                update={
                    "response_body_b64": base64.b64encode(b"different").decode(),
                }
            ),
        )
    with pytest.raises(ValueError, match="header pairs"):
        owner.progress(
            window.request_id, observed.model_copy(update={"received_header_pairs": []})
        )
    with pytest.raises(ValueError, match="immutable metadata"):
        owner.progress(
            window.request_id,
            observed.model_copy(update={"endpoint": "http://localhost:9101"}),
        )
    owner.close_window(window.request_id)
    with pytest.raises(ValueError, match="outside its admission window"):
        owner.progress(
            window.request_id, attempt.model_copy(update={"attempt_id": str(uuid4())})
        )
    with pytest.raises(ValueError, match="no issued decision window"):
        owner.progress(uuid4(), attempt)
    owner.stop_admission()
    assert (
        owner.has_unsettled_readers()
    )  # STOP never relabels a received reader as joined.


def test_profile_registration_is_seat_bound_once_and_before_first_window():
    from cogame_cogolf.native_profile import NativeProfile
    from cogame_cogolf.private_window import Ready
    from cogame_cogolf.server import WsSeat

    profiles = [NativeProfile(), NativeProfile()]
    seat = WsSeat(0, "policy", profiles)
    profile = NativeProfile(
        model="checkpoint:owned", temperature=0.0, strategy="  private strategy\n"
    )
    with pytest.raises(ValueError, match="wrong-seat"):
        seat.deliver(Ready(slot=1, profile=profile).model_dump(mode="json"))
    seat.deliver(Ready(slot=0, profile=profile).model_dump(mode="json"))
    assert profiles[0] == profile and profiles[0].strategy == "  private strategy\n"
    assert seat._connected.is_set()
    with pytest.raises(ValueError, match="repeated"):
        seat.deliver(Ready(slot=0, profile=profile).model_dump(mode="json"))
    late = WsSeat(1, "other", profiles)
    cfg = make_config()
    engine = Engine(cfg, [ScriptedSource(), ScriptedSource()], FakeSandbox())
    window = ObservationPacket.model_validate(
        engine._observation_message(1, engine.deck["median"], 1, False)
    )
    late.admission.issue(window)
    with pytest.raises(ValueError, match="late"):
        late.deliver(Ready(slot=1, profile=profile).model_dump(mode="json"))
    assert profiles[1] == NativeProfile()
