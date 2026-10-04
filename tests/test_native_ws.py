"""Actual large native completion crosses WS and binds engine-owned action."""

import asyncio
import base64
import json
from uuid import uuid4

import pytest
from aiohttp import web
from cogame_cogolf import contract
from cogame_cogolf.baseline import baseline
from cogame_cogolf.engine import Engine
from cogame_cogolf.guidance import API_DOCS
from cogame_cogolf.private_window import (
    Action,
    Admission,
    EvidenceReceived,
    ObservationPacket,
    Ready,
    Started,
    Stop,
    Stopped,
)

from players.client import play_episode
from players.llm_player import LLMPolicy
from tests.conftest import make_config
from tests.fakes import FakeSandbox, ScriptedSource


@pytest.mark.parametrize("final", [True, False])
async def test_real_native_large_context_ws_action_and_joined_stop(monkeypatch, final):
    config = make_config()
    profile = config.native_profiles[0].model_copy(update={"temperature": 0.0})
    engine = Engine(config, [ScriptedSource(), ScriptedSource()], FakeSandbox())
    spec = engine.deck["median"]
    window = ObservationPacket(
        slot=0,
        hole=1,
        retry=False,
        profile=profile,
        deadline_seconds=8,
        observation=engine._observation_message(1, spec, 0, retry=False)["observation"],
    )
    action = baseline("literalist", spec, 1)
    text = json.dumps(action)
    body = (
        " \n"
        + json.dumps(
            {
                "model": profile.model,
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 32768, "output_tokens": 1},
                "sampling_evidence": {
                    "policy_revision": "checkpoint-test",
                    "tokenizer_revision": "tokenizer-test",
                    "chat_template": "template-test",
                    "sampling": None,
                    "enable_thinking": False,
                    "max_new_tokens": 1800,
                    "max_sequence_length": 32769,
                    "sampling_seed": 0,
                    "eos_token_ids": [7],
                    "prompt_token_ids": [1] * 32768,
                    "completion_token_ids": [7],
                    "behavior_log_probs": None,
                    "response": text,
                    "stop_reason": "eos",
                },
            }
        )
        + "\n "
    ).encode()
    calls = []
    native_handlers = set()

    async def native_handler(reader, writer):
        native_handlers.add(asyncio.current_task())
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            size = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            assert (
                f"host: localhost:{native_server.sockets[0].getsockname()[1]}".encode()
                in headers.lower()
            )
            calls.append(json.loads(await reader.readexactly(size)))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nX-Softmax-LLM-Call-ID: {uuid4()}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    native_server = await asyncio.start_server(native_handler, "127.0.0.1", 0)
    monkeypatch.setenv(
        "COWORLD_LLM_ENDPOINT",
        f"http://localhost:{native_server.sockets[0].getsockname()[1]}",
    )
    admission = Admission(0)
    admission.issue(window)
    accepted = asyncio.get_running_loop().create_future()
    frame_sizes = []
    ws_handlers = set()

    async def player(request):
        ws_handlers.add(asyncio.current_task())
        ws = web.WebSocketResponse(max_msg_size=contract.MAX_PRIVATE_FRAME_BYTES)
        await ws.prepare(request)
        try:
            await ws.send_json(
                {
                    "type": "welcome",
                    "protocol": contract.PROTOCOL,
                    "slot": 0,
                    "alias": "Ash",
                    "api_docs": API_DOCS,
                    "native_profile": profile.model_dump(),
                }
            )
            registration = Ready.model_validate_json((await ws.receive()).data)
            assert registration.slot == 0 and registration.profile == window.profile
            await ws.send_str(window.model_dump_json())
            while True:
                message = await ws.receive()
                frame_sizes.append(len(message.data.encode()))
                value = json.loads(message.data)
                if value["type"] == "attempt_started":
                    progress = Started.model_validate(value)
                    admission.progress(progress.request_id, progress.attempt)
                else:
                    packet = Action.model_validate(value)
                    actual = admission.consume(packet)
                    assert packet.selected_attempt_id is not None
                    attempt = admission.attempts[window.request_id][
                        packet.selected_attempt_id
                    ]
                    assert base64.b64decode(attempt.response_body_b64) == body
                    assert len(attempt.sampling_evidence.prompt_token_ids) == 32768
                    assert attempt.response_reader_joined is True
                    accepted.set_result(actual)
                    break
            admission.stop_admission()
            stop = Stop(stop_id=uuid4(), deadline_seconds=2)
            await ws.send_str(stop.model_dump_json())
            stopped = Stopped.model_validate_json((await ws.receive()).data)
            assert stopped.stop_id == stop.stop_id and stopped.owners_joined
            assert not admission.has_unsettled_readers()
            await ws.send_str(EvidenceReceived(stop_id=stop.stop_id).model_dump_json())
            if final:
                await ws.send_json({"type": "done", "result": {"scores": [0, 0]}})
        finally:
            await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/player", player)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/player?slot=0"
    monkeypatch.setenv("COWORLD_PLAYER_WS_URL", url)
    client = asyncio.create_task(
        play_episode(LLMPolicy(timeout_seconds=2, hole_budget_seconds=3), url)
    )
    try:
        async with asyncio.timeout(6):
            result = await client
            assert result == ({"scores": [0, 0]} if final else {})
            assert (await accepted)["impl"] == action["impl"]
        assert calls == [window.native_request().model_dump(exclude_none=True)]
        assert max(frame_sizes) > 65536
    finally:
        native_server.close()
        await native_server.wait_closed()
        await runner.cleanup()
        for task in native_handlers | ws_handlers:
            if not task.done():
                await asyncio.wait({task}, timeout=1)
            assert task.done()
            if not client.cancelled() and client.exception() is None:
                task.result()
