"""Observed partial bytes and terminal errors survive the actual HTTP reader join."""

import asyncio
import base64
from uuid import uuid4

import pytest

from players import native


@pytest.mark.parametrize(
    "kind", ["partial_timeout", "invalid_utf8", "http_error", "no_headers"]
)
async def test_actual_reader_retains_partial_terminal_or_unobserved_facts(
    monkeypatch, kind
):
    request = native.Request(
        model="checkpoint:fixture",
        max_tokens=32,
        temperature=0.0,
        top_p=1.0,
        system="ordinary game instructions",
        messages=[native.Message(role="user", content="private-request-sentinel")],
    )
    attempt = native.Attempt(slot=0, stage="submission", request=request)
    call_id = str(uuid4())
    prefix = (
        b'{"error":"private-response-sentinel"}'
        if kind == "http_error"
        else b"\xffpartial-private-response-sentinel"
    )
    release = asyncio.Event()
    handlers = set()

    async def serve(reader, writer):
        handlers.add(asyncio.current_task())
        try:
            await reader.readuntil(b"\r\n\r\n")
            if kind != "no_headers":
                status = b"503 Unavailable" if kind == "http_error" else b"200 OK"
                size = len(prefix) + (100 if kind == "partial_timeout" else 0)
                writer.write(
                    b"HTTP/1.1 "
                    + status
                    + b"\r\nContent-Type: application/json\r\n"
                    + f"X-Softmax-LLM-Call-ID: {call_id}\r\nContent-Length: {size}\r\n\r\n".encode()
                    + prefix
                )
                await writer.drain()
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("COWORLD_LLM_ENDPOINT", f"http://localhost:{port}")
    snapshots = []

    async def progress(snapshot):
        snapshots.append(snapshot)

    expected = (
        TimeoutError
        if kind in {"partial_timeout", "no_headers"}
        else native.ResponseRejected
        if kind == "http_error"
        else UnicodeDecodeError
    )
    try:
        with pytest.raises(expected):
            await native.complete(
                request,
                native.LearnerScope(slot=0, model=request.model),
                stage="submission",
                timeout=0.3,
                attempt=attempt,
                progress=progress,
            )
    finally:
        release.set()
        server.close()
        await server.wait_closed()
        if handlers:
            _, pending = await asyncio.wait(handlers, timeout=1)
            assert not pending
            for task in handlers:
                task.result()
    assert attempt.response is None
    assert attempt.raw_response == (prefix.decode() if kind == "http_error" else None)
    assert snapshots[-1] == attempt
    assert "private-request-sentinel" not in attempt.rejection_reason
    if kind == "no_headers":
        assert attempt.platform_call_id is None and attempt.http_status is None
        assert attempt.response_body_b64 is None and attempt.response_complete is None
        assert attempt.response_reader_joined is None
    else:
        assert base64.b64decode(attempt.response_body_b64) == prefix
        assert attempt.platform_call_id == call_id
        assert attempt.http_status == (503 if kind == "http_error" else 200)
        assert attempt.response_complete is (kind != "partial_timeout")
        assert attempt.response_reader_joined is True
