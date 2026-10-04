"""Hostname resolution preserves the real HTTPS authority and TLS server name."""

import asyncio
import json
import ssl
from uuid import uuid4

from players import native


async def test_actual_https_owned_dns_preserves_host_and_sni(monkeypatch, tmp_path):
    certificate = tmp_path / "localhost.pem"
    key = tmp_path / "localhost.key"
    generation = await asyncio.create_subprocess_exec(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(certificate),
        "-subj",
        "/CN=localhost",
        "-addext",
        "subjectAltName=DNS:localhost",
        "-days",
        "1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(5):
            _, errors = await generation.communicate()
        assert generation.returncode == 0, errors.decode()
    finally:
        if generation.returncode is None:
            generation.kill()
        async with asyncio.timeout(2):
            await generation.wait()
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    # The pinned Nix Python loads its separately configured CA bundle.
    monkeypatch.setenv("NIX_SSL_CERT_FILE", str(certificate))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server_names = []
    context.set_servername_callback(
        lambda connection, name, ctx: server_names.append(name)
    )
    headers_seen = []
    handlers = set()
    request = native.Request(
        model="checkpoint:fixture",
        max_tokens=32,
        temperature=0.0,
        top_p=1.0,
        system="ordinary game instructions",
        messages=[native.Message(role="user", content="exact private window")],
    )
    body = json.dumps(
        {
            "model": request.model,
            "content": [{"type": "text", "text": "{}"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
    ).encode()
    call_id = str(uuid4())

    async def serve(reader, writer):
        handlers.add(asyncio.current_task())
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            headers_seen.append(headers)
            size = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            assert json.loads(await reader.readexactly(size)) == request.model_dump(
                exclude_none=True
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"X-Softmax-LLM-Call-ID: {call_id}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    endpoint = f"https://localhost:{port}/"
    monkeypatch.setenv("COWORLD_LLM_ENDPOINT", endpoint)
    scope = native.LearnerScope(slot=0, model=request.model)
    attempt = native.Attempt(slot=0, stage="submission", request=request)
    try:
        await native.complete(
            request, scope, stage="submission", timeout=3, attempt=attempt
        )
    finally:
        server.close()
        await server.wait_closed()
        if handlers:
            _, pending = await asyncio.wait(handlers, timeout=1)
            assert not pending
            for handler in handlers:
                handler.result()
    assert scope.endpoint == endpoint and attempt.endpoint == endpoint
    assert server_names == ["localhost"]
    assert headers_seen[0].startswith(b"POST /v1/messages HTTP/1.1\r\n")
    assert f"host: localhost:{port}\r\n".encode() in headers_seen[0].lower()
    assert attempt.raw_response.encode() == body
    assert (
        attempt.platform_call_id == call_id and attempt.response_reader_joined is True
    )
