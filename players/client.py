"""Reusable async player harness for cogame-cogolf websocket seats.

Speaks the ``cogame.cogolf.v1`` protocol (see ``docs/PROTOCOL.md``), one
JSON text message per hole each way:

    server -> player  {"type": "welcome", "slot": 0, "alias": "Ash",
                       "rules": {...}, "episode": {...},
                       "api_docs": "..."}              once per connection
    server -> player  {"type": "observation", "hole": k, "retry": false,
                       "deadline_seconds": 40, "observation": {...}}
    player -> server  {"type": "submission", "hole": k, "impl": "...",
                       "tests": [...], "note": "..."}
    server -> player  {"type": "done", "result": {...}}   episode end

The websocket URL comes from an explicit argument or, failing that, the
``COWORLD_PLAYER_WS_URL`` / ``COGAMES_ENGINE_WS_URL`` environment
variables.

A policy is a :class:`Policy`: ``on_welcome(welcome)`` (called on every
(re)connection), ``submission(hole, observation) -> dict`` (``impl``,
``tests``, ``note``) and ``on_done(result)``. ``submission`` runs in a
worker thread so a slow policy (an LLM call) never blocks the websocket
heartbeat; if it has not returned by the hole deadline, or it raises, or
it returns something unusable, the harness sends the scripted
``literalist`` submission for this hole — a real, legal move. It NEVER
sends a noop: a policy bug costs a weak move, never a forfeited hole.

Reconnects: the server allows a seat to reconnect any number of times and
re-sends ``welcome`` plus the *current* observation, so transient drops
are retried with a bounded number of consecutive attempts (a connection
that answered at least one hole resets the budget). Before the first
successful connection a 403 (bad slot/token) is fatal (exit 1) and
connection refusals burn the bounded budget (then exit 1). Once the seat
*has* connected, a 403, a refused connection, a close frame or a truncated
read means the server has finished and gone away: the harness returns
promptly with an empty result and exit 0 instead of raising — a player
container must always exit 0 on a dead socket (cogame-raid 0.1.3).

Telemetry (best effort, never affects play): when
``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL`` is set, one zip per episode
(``meta.json``, ``events.jsonl``, ``summary.json``) is written to a
``file://...zip`` URL or PUT to an ``http(s)`` URL when ``done`` arrives.
Any telemetry error disables telemetry for the rest of the episode.

Only aiohttp is required (stdlib otherwise).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import aiohttp
from aiohttp import WSMsgType

# Wire strings: prefer the server's zero-dependency contract module when
# it is importable (the image has PYTHONPATH=/workspace/server), so a
# rename there is caught by the four-surface rule; fall back to a local
# copy so the harness also runs outside the image.
try:  # pragma: no cover - which branch runs depends on the environment
    from cogame_cogolf import contract as _contract  # type: ignore

    PROTOCOL = _contract.PROTOCOL
    MSG_WELCOME = _contract.MSG_WELCOME
    MSG_OBSERVATION = _contract.MSG_OBSERVATION
    MSG_SUBMISSION = _contract.MSG_SUBMISSION
    MSG_DONE = _contract.MSG_DONE
    WS_URL_ENV_VARS = (_contract.ENV_PLAYER_WS_URL,
                       _contract.ENV_PLAYER_WS_URL_LEGACY)
    MAX_IMPL_CHARS = _contract.MAX_IMPL_CHARS
    MAX_TESTS_PER_HOLE = _contract.MAX_TESTS_PER_HOLE
    MAX_MESSAGE_BYTES = _contract.MAX_MESSAGE_BYTES
    MAX_TEST_NAME_CHARS = _contract.MAX_TEST_NAME_CHARS
    MAX_WHY_CHARS = _contract.MAX_WHY_CHARS
    MAX_NOTE_CHARS = _contract.MAX_NOTE_CHARS
except Exception:  # noqa: BLE001 - ImportError or a partial module
    PROTOCOL = "cogame.cogolf.v1"
    MSG_WELCOME = "welcome"
    MSG_OBSERVATION = "observation"
    MSG_SUBMISSION = "submission"
    MSG_DONE = "done"
    WS_URL_ENV_VARS = ("COWORLD_PLAYER_WS_URL", "COGAMES_ENGINE_WS_URL")
    MAX_IMPL_CHARS = 4000
    MAX_TESTS_PER_HOLE = 5
    MAX_MESSAGE_BYTES = 16384
    MAX_TEST_NAME_CHARS = 40
    MAX_WHY_CHARS = 120
    MAX_NOTE_CHARS = 200

ARTIFACT_URL_ENV_VAR = "COWORLD_PLAYER_ARTIFACT_UPLOAD_URL"

DEFAULT_MAX_CONNECT_ATTEMPTS = 5
DEFAULT_RECONNECT_DELAY_SECONDS = 0.5

# Bound on establishing one websocket connection (TCP + handshake).
CONNECT_TIMEOUT_SECONDS = 20.0

# Safety margin subtracted from the server's hole deadline when bounding
# the policy call: the reply still has to cross the wire.
DEADLINE_MARGIN_SECONDS = 3.0

# Handshake statuses that can never succeed on retry (before the seat has
# ever connected). 409 (slot already connected) is deliberately NOT here.
_FATAL_HTTP_STATUSES = {
    403: "connection rejected (403): bad slot or token",
}


class PlayerError(Exception):
    """Fatal player-side failure (bad auth, server never reachable, bad env)."""


class Policy(ABC):
    """A cogolf policy: one submission per hole."""

    def on_welcome(self, welcome: dict) -> None:
        """Called with the ``welcome`` message on every (re)connection."""

    @abstractmethod
    def submission(self, hole: int, observation: dict) -> dict:
        """Return ``{"impl": str, "tests": list, "note": str}`` for ``hole``."""

    def fallback(self, hole: int, observation: dict) -> dict:
        """The move the harness plays when :meth:`submission` fails.

        The default is the scripted ``literalist`` submission for this
        hole. Never a noop.
        """
        from players.scripted import scripted_submission
        return scripted_submission("literalist", hole, observation)

    def on_done(self, result: dict) -> None:
        """Called once with the episode ``result`` before the harness exits."""


def ws_url_from_env() -> str:
    """The seat websocket URL from the environment (first env var wins)."""
    for name in WS_URL_ENV_VARS:
        url = os.environ.get(name)
        if url:
            return url
    raise PlayerError(
        "no websocket URL: set " + " or ".join(WS_URL_ENV_VARS))


def _log(msg: str) -> None:
    print(f"player: {msg}", file=sys.stderr, flush=True)


def _clip(value, limit: int) -> str:
    """Rune-boundary truncation (Python str slicing is code-point based)."""
    text = value if isinstance(value, str) else ("" if value is None
                                                 else str(value))
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


def normalize_submission(payload, hole: int, max_tests: int = MAX_TESTS_PER_HOLE
                         ) -> dict | None:
    """Turn a policy's answer into a strict wire message, or None.

    The policy side is lenient, the wire is strict: the impl must be a
    non-empty string within the cap, tests are trimmed to ``max_tests``
    well-formed records, and every free-text field is truncated on rune
    boundaries.
    """
    if not isinstance(payload, dict):
        return None
    impl = payload.get("impl")
    if not isinstance(impl, str) or not impl.strip():
        return None
    if len(impl) > MAX_IMPL_CHARS:
        _log(f"policy impl is {len(impl)} chars (cap {MAX_IMPL_CHARS})")
        return None
    tests = []
    for i, entry in enumerate(payload.get("tests") or []):
        if len(tests) >= max_tests:
            break
        if not isinstance(entry, dict):
            continue
        args = entry.get("args")
        if not isinstance(args, list):
            continue
        tests.append({
            "name": _clip(entry.get("name") or f"test {i + 1}",
                          MAX_TEST_NAME_CHARS),
            "args": args,
            "expect": entry.get("expect"),
            "why": _clip(entry.get("why") or "", MAX_WHY_CHARS),
        })
    message = {"type": MSG_SUBMISSION, "hole": int(hole), "impl": impl,
               "tests": tests,
               "note": _clip(payload.get("note") or "", MAX_NOTE_CHARS)}
    while len(json.dumps(message).encode("utf-8")) > MAX_MESSAGE_BYTES \
            and message["tests"]:
        message["tests"].pop()
    if len(json.dumps(message).encode("utf-8")) > MAX_MESSAGE_BYTES:
        return None
    return message


# -- telemetry ------------------------------------------------------------------

class Telemetry:
    """Per-episode artifact zip (meta.json, events.jsonl, summary.json).

    Every method swallows its own errors and disables itself: telemetry
    can never fail the episode. ``upload_url`` None disables it outright.
    """

    def __init__(self, upload_url: str | None, policy_module: str):
        self.url = upload_url or None
        self.enabled = bool(self.url)
        self.meta: dict = {"policy_module": policy_module,
                           "started_at": time.time()}
        self.events: list[dict] = []
        self.connections = 0
        self.uploaded = False
        self.result: dict | None = None

    def _disable(self, why: str, exc: Exception) -> None:
        if self.enabled:
            _log(f"telemetry disabled ({why}): {exc!r}")
        self.enabled = False

    def on_welcome(self, welcome: dict) -> None:
        if not self.enabled:
            return
        try:
            self.connections += 1
            episode = welcome.get("episode")
            self.meta.update({
                "slot": welcome.get("slot"),
                "alias": welcome.get("alias"),
                "game_version": welcome.get("game_version"),
                "protocol": welcome.get("protocol"),
                "episode": episode if isinstance(episode, dict) else {},
                "connections": self.connections,
            })
        except Exception as exc:  # noqa: BLE001
            self._disable("on_welcome", exc)

    def on_submission(self, hole: int, message: dict, fallback: bool,
                      wall_ms: int) -> None:
        if not self.enabled:
            return
        try:
            self.events.append({
                "hole": hole,
                "impl_chars": len(message.get("impl") or ""),
                "tests": len(message.get("tests") or []),
                "note": message.get("note"),
                "harness_fallback": fallback,
                "wall_ms": wall_ms,
            })
        except Exception as exc:  # noqa: BLE001
            self._disable("on_submission", exc)

    def on_done(self, result: dict) -> None:
        if not self.enabled:
            return
        self.result = result

    def build_zip(self) -> bytes:
        summary = {
            "holes_answered": len(self.events),
            "harness_fallbacks": sum(1 for e in self.events
                                     if e.get("harness_fallback")),
            "result": self.result,
            "connections": self.connections,
            "finished_at": time.time(),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("meta.json", json.dumps(self.meta, default=str, indent=1))
            zf.writestr("events.jsonl", "".join(
                json.dumps(e, default=str) + "\n" for e in self.events))
            zf.writestr("summary.json", json.dumps(summary, default=str, indent=1))
        return buf.getvalue()

    async def upload(self, session: aiohttp.ClientSession | None = None) -> bool:
        """Write/PUT the zip once. Returns True on success; never raises."""
        if not self.enabled or self.uploaded:
            return False
        try:
            data = self.build_zip()
            parsed = urlparse(self.url)
            if parsed.scheme == "file":
                path = Path(unquote(parsed.path))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            elif parsed.scheme in ("http", "https"):
                own = session is None
                if own:
                    session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=60))
                try:
                    async with session.put(
                            self.url, data=data,
                            headers={"Content-Type": "application/zip"}) as resp:
                        if resp.status >= 300:
                            raise RuntimeError(f"upload HTTP {resp.status}")
                finally:
                    if own:
                        await session.close()
            else:
                raise ValueError(
                    f"unsupported artifact URL scheme {parsed.scheme!r}")
            self.uploaded = True
            _log(f"telemetry uploaded ({len(data)} bytes) to {self.url}")
            return True
        except Exception as exc:  # noqa: BLE001
            self._disable("upload", exc)
            return False


# -- episode loop --------------------------------------------------------------

async def play_episode(
        policy: Policy,
        url: str | None = None,
        *,
        max_connect_attempts: int = DEFAULT_MAX_CONNECT_ATTEMPTS,
        reconnect_delay_seconds: float = DEFAULT_RECONNECT_DELAY_SECONDS,
        deadline_margin_seconds: float = DEADLINE_MARGIN_SECONDS,
        telemetry: Telemetry | None = None,
) -> dict:
    """Play one episode; returns the ``result`` from the done message."""
    if url is None:
        url = ws_url_from_env()
    if telemetry is None:
        telemetry = Telemetry(os.environ.get(ARTIFACT_URL_ENV_VAR),
                              type(policy).__module__)

    failures = 0
    total_answered = 0
    ever_connected = False

    def _fail(reason: str, exc: Exception | None = None):
        nonlocal failures
        failures += 1
        _log(f"connection attempt failed "
             f"({failures}/{max_connect_attempts} consecutive): {reason}; "
             f"{total_answered} holes answered so far")
        if failures >= max_connect_attempts:
            raise PlayerError(
                f"giving up after {failures} consecutive failed "
                f"connection attempts: {reason}") from exc

    def _server_gone(reason: str) -> dict:
        _log(f"server gone after the seat had connected ({reason}); "
             f"{total_answered} holes answered; exiting cleanly")
        return {}

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=CONNECT_TIMEOUT_SECONDS,
        sock_connect=CONNECT_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            while True:
                try:
                    ws = await session.ws_connect(url, heartbeat=20.0)
                except aiohttp.WSServerHandshakeError as exc:
                    if exc.status in _FATAL_HTTP_STATUSES:
                        if ever_connected:
                            return _server_gone(f"HTTP {exc.status}")
                        raise PlayerError(
                            _FATAL_HTTP_STATUSES[exc.status]) from exc
                    _fail(f"handshake failed with status {exc.status}", exc)
                    await asyncio.sleep(reconnect_delay_seconds)
                    continue
                except (aiohttp.ClientConnectorError,
                        ConnectionRefusedError) as exc:
                    if ever_connected:
                        return _server_gone(f"connection refused: {exc}")
                    _fail(str(exc), exc)
                    await asyncio.sleep(reconnect_delay_seconds)
                    continue
                except (aiohttp.ClientError, OSError) as exc:
                    _fail(str(exc), exc)
                    await asyncio.sleep(reconnect_delay_seconds)
                    continue

                ever_connected = True
                try:
                    result, answered = await _play_connection(
                        ws, policy, deadline_margin_seconds, telemetry)
                finally:
                    try:
                        await ws.close()
                    except Exception:
                        # A close failure after the done message must never
                        # turn a completed episode into a player failure.
                        pass
                total_answered += answered
                if result is not None:
                    return result
                if answered > 0:
                    failures = 0  # made progress: fresh reconnect budget
                _fail("connection closed before the done message")
                await asyncio.sleep(reconnect_delay_seconds)
        finally:
            await telemetry.upload(session)


async def _call_policy(policy: Policy, hole: int, observation: dict,
                       deadline: float | None) -> tuple[dict, bool]:
    """Run ``policy.submission`` off-loop, bounded by ``deadline`` seconds.

    Returns ``(message, harness_fallback)``. A policy that raises,
    overruns or answers with something unusable is replaced by the
    scripted fallback — never by nothing.
    """
    coro = asyncio.to_thread(policy.submission, hole, observation)
    payload = None
    try:
        if deadline is not None:
            payload = await asyncio.wait_for(coro, timeout=deadline)
        else:
            payload = await coro
    except asyncio.TimeoutError:
        _log(f"policy.submission overran the deadline at hole {hole}; "
             f"falling back to the scripted move")
    except Exception as exc:  # noqa: BLE001 - a policy bug is not fatal
        _log(f"policy.submission raised at hole {hole}: {exc!r}; "
             f"falling back to the scripted move")
    message = normalize_submission(payload, hole)
    if message is not None:
        return message, False
    fallback = normalize_submission(policy.fallback(hole, observation), hole)
    if fallback is None:  # pragma: no cover - the scripted move is valid
        fallback = {"type": MSG_SUBMISSION, "hole": hole,
                    "impl": "def solve(*args):\n    return None\n",
                    "tests": [], "note": "fallback"}
    return fallback, True


def _policy_deadline(data: dict, welcome: dict | None,
                     margin: float) -> float | None:
    raw = data.get("deadline_seconds")
    if raw is None and welcome is not None:
        raw = welcome.get("hole_deadline_seconds")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    return max(float(raw) - margin, 1.0)


async def _play_connection(
        ws: aiohttp.ClientWebSocketResponse, policy: Policy,
        deadline_margin_seconds: float, telemetry: Telemetry,
) -> tuple[dict | None, int]:
    """Answer holes on one connection until done or disconnect.

    Returns ``(result, holes_answered)``; result is None on disconnect.
    Malformed or unknown messages are logged and skipped, never fatal.
    """
    answered = 0
    welcome: dict | None = None
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                _log("ignoring non-JSON message")
                continue
            if not isinstance(data, dict):
                _log("ignoring non-object message")
                continue
            mtype = data.get("type")

            if mtype == MSG_WELCOME:
                welcome = data
                if data.get("protocol") not in (None, PROTOCOL):
                    _log(f"server protocol {data.get('protocol')!r} != "
                         f"{PROTOCOL!r}; continuing anyway")
                telemetry.on_welcome(data)
                try:
                    policy.on_welcome(data)
                except Exception as exc:  # noqa: BLE001
                    _log(f"policy.on_welcome raised: {exc!r}; ignoring")
                continue

            if mtype == MSG_DONE:
                result = data.get("result")
                if not isinstance(result, dict):
                    result = {}
                telemetry.on_done(result)
                try:
                    policy.on_done(result)
                except Exception as exc:  # noqa: BLE001
                    _log(f"policy.on_done raised: {exc!r}; ignoring")
                return result, answered

            if mtype == MSG_OBSERVATION:
                hole = data.get("hole")
                observation = data.get("observation")
                if not isinstance(hole, int) or isinstance(hole, bool):
                    _log("ignoring observation without an integer hole")
                    continue
                if not isinstance(observation, dict):
                    _log(f"observation at hole {hole} has no observation "
                         f"object; answering with an empty one")
                    observation = {}
                deadline = _policy_deadline(data, welcome,
                                            deadline_margin_seconds)
                started = time.monotonic()
                message, fallback = await _call_policy(
                    policy, hole, observation, deadline)
                telemetry.on_submission(
                    hole, message, fallback,
                    int((time.monotonic() - started) * 1000))
                await ws.send_str(json.dumps(message, ensure_ascii=False))
                answered += 1
                continue

            _log(f"ignoring message of unknown type {mtype!r}")
    except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError):
        pass  # dropped mid-episode: caller decides whether to reconnect
    except Exception as exc:  # noqa: BLE001 - a dead socket is not a failure
        _log(f"receive loop ended with {type(exc).__name__}: {exc}")
    return None, answered


def run_policy_main(policy_factory: Callable[[], Policy]) -> int:
    """Entry-point helper: build the policy and play one episode.

    Returns a process exit code: 0 on a clean done message (or the server
    going away after the seat had played), 1 on fatal player errors (bad
    env config, bad auth, server never reachable), 130 on SIGINT.
    """
    try:
        policy = policy_factory()
        result = asyncio.run(play_episode(policy))
    except PlayerError as exc:
        print(f"player failed: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 130
    print(f"episode done: result={json.dumps(result)}",
          file=sys.stderr, flush=True)
    return 0


def main_for(policy_factory: Callable[[], Policy]) -> None:
    """``if __name__ == "__main__": main_for(MyPolicy)`` — exits the process."""
    sys.exit(run_policy_main(policy_factory))


__all__ = [
    "Policy", "PlayerError", "Telemetry", "play_episode", "normalize_submission",
    "run_policy_main", "main_for", "ws_url_from_env",
    "PROTOCOL", "WS_URL_ENV_VARS", "ARTIFACT_URL_ENV_VAR",
]
