"""aiohttp game server implementing the Coworld runtime contract.

Episode mode (default): read the game config from ``COGAME_CONFIG_URI``,
serve ``GET /player?slot=N&token=T`` websockets, run the hole loop
(docs/PROTOCOL.md), broadcast ``done``, write results
(``COGAME_RESULTS_URI``) and the replay (``COGAME_SAVE_REPLAY_URI``), and
exit 0. A seat that never connects is declared to
``COGAME_PLAYER_FAILURE_URI`` and plays the literalist fallback every hole
— it never ends the episode.

Global viewer: ``GET /global`` is a broadcast-only websocket (status
snapshot on connect, ``progress`` after every resolved hole, final
``done``); ``GET /client/global`` and ``GET /client/player?slot=N&token=T``
are real token-checked pages that never open a player socket.

Replay mode: with ``COGAME_LOAD_REPLAY_URI`` set no episode runs; the
replay JSON is served at ``GET /replay-data`` and the static viewer bundle
(``viewer/dist``) at ``/client/replay/``.

Entry point: ``python -m cogame_cogolf.server`` (``/bin/cogolf``). Binds
``COGAME_HOST``/``COGAME_PORT`` (default 0.0.0.0:8080). Exit codes: 0
episode complete — including ``deadline`` and ``harness_fault``, whose
artifacts are still written — and 2 for a missing or invalid config.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from aiohttp import WSCloseCode, WSMsgType, web
from pydantic import ConfigDict, TypeAdapter

from . import contract, lifecycle, uris
from .config import ConfigError, GameConfig
from .engine import Engine
from .guidance import API_DOCS
from .journal import Journal
from .native_profile import NativeProfile
from .private_window import (
    Action,
    Admission,
    EvidenceReceived,
    ObservationPacket,
    Ready,
    Started,
    Stop,
    Stopped,
)
from .replay import Replay, ReplayError, ReplayWriter
from .results import EpisodeResult, fault_results_doc, results_doc
from .sandbox import Sandbox, SandboxError
from .specs import DECK_VERSION, DeckError, load_deck
from .submission import validate_submission_message
from .version import GAME_VERSION

PROTOCOL = contract.PROTOCOL
PLAYER_PACKET = TypeAdapter(
    Ready | Started | Action | Stopped, config=ConfigDict(hide_input_in_errors=True)
)

# After artifacts are written, keep /healthz and /global answering for this
# long: the certification runner pings /global AFTER the player pods start,
# and a short episode may otherwise already be gone (cogame-lantern 0.1.3).
SHUTDOWN_GRACE_SECONDS = 20.0
# Per-seat bound on sending the final done message + close.
DONE_SEND_TIMEOUT_SECONDS = 3.0
# aiohttp ping/pong heartbeat on /player sockets: a half-open connection
# is reaped within ~this interval instead of 409-ing real reconnects.
PLAYER_WS_HEARTBEAT_SECONDS = 20.0


GLOBAL_CLIENT_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>cogame-cogolf</title>
<style>
  body { font-family: ui-monospace, monospace; margin: 2rem; }
  #log { white-space: pre-wrap; }
</style>
</head>
<body>
<h1>cogame-cogolf live feed</h1>
<div id="log">connecting to /global ...</div>
<script>
const log = document.getElementById("log");
const proto = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(proto + "//" + location.host + "/global");
ws.onmessage = (ev) => { log.textContent += "\\n" + ev.data; };
ws.onopen = () => { log.textContent = "connected"; };
ws.onclose = () => { log.textContent += "\\n[closed]"; };
</script>
</body>
</html>
"""

PLAYER_CLIENT_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>cogame-cogolf seat</title></head>
<body style="font-family: ui-monospace, monospace; margin: 2rem;">
<h1>cogame-cogolf</h1>
<p>Seat <span id="slot"></span> is played over the websocket protocol
(<code>GET /player?slot=N&amp;token=T</code>, see docs/PROTOCOL.md);
this page only confirms the seat credential is valid.</p>
<script>
document.getElementById("slot").textContent =
  new URLSearchParams(location.search).get("slot");
</script>
</body>
</html>
"""


class WsSeat:
    """One player seat: websocket state + engine SubmissionSource.

    ``get_submission`` sends the hole's observation to the connected player
    and waits for a valid matching-hole ``submission`` reply until the
    deadline. The pending observation is kept so a (re)connecting player
    gets ``welcome`` and then the current observation with the remaining
    deadline.
    """

    def __init__(self, slot: int, name: str, profiles: list[NativeProfile]):
        self.slot = slot
        self.name = name
        self.ws: web.WebSocketResponse | None = None
        self.ever_connected = False
        self.welcome: dict | None = None
        self.wrong_hole_count = 0
        self._connected = asyncio.Event()
        self._pending: tuple[int, dict, float, asyncio.Future] | None = None
        self._seen_connection = False
        self.admission = Admission(slot)
        self.stop_message: Stop | None = None
        self.stop_deadline: float | None = None
        self.stopped = asyncio.Event()
        self.registered_profile = False
        self.profiles = profiles
        self.sealed = False
        self.journal: Journal | None = None

    @property
    def connected(self) -> bool:
        return self.ws is not None and not self.ws.closed

    # -- SubmissionSource ----------------------------------------------------

    async def wait_connected(self, timeout_seconds: float) -> bool:
        if self._connected.is_set():
            return True
        try:
            await asyncio.wait_for(self._connected.wait(), timeout_seconds)
        except TimeoutError:
            return False
        return True

    async def get_submission(self, hole: int, payload: dict, deadline_at: float):
        fut = asyncio.get_running_loop().create_future()
        window = ObservationPacket.model_validate(payload)
        self.admission.issue(window)
        self._pending = (hole, payload, deadline_at, fut)
        self._seen_connection = self.connected
        try:
            if self.connected:
                await self._send_pending()
            remaining = deadline_at - time.monotonic()
            try:
                return await asyncio.wait_for(fut, max(0.0, remaining))
            except TimeoutError:
                return None, ("timeout" if self._seen_connection else "disconnected")
        finally:
            self.admission.close_window(window.request_id)
            self._pending = None

    async def _send_pending(self) -> None:
        ws = self.ws
        if ws is None or ws.closed or self._pending is None:
            return
        _hole, payload, deadline_at, _fut = self._pending
        message = dict(payload)
        message["deadline_seconds"] = max(0.0, deadline_at - time.monotonic())
        async with asyncio.timeout_at(deadline_at):
            await ws.send_str(json.dumps(message, ensure_ascii=False))

    def deliver(self, data) -> None:
        """Bind native progress and separately submitted actions to issued UUIDs."""
        if self.sealed:
            raise ValueError("private seat admission is sealed")
        packet = PLAYER_PACKET.validate_python(data)
        if isinstance(packet, Ready):
            if (
                packet.slot != self.slot
                or self.registered_profile
                or self.admission.windows
                or self.stop_message is not None
            ):
                raise ValueError(
                    "native profile registration is wrong-seat, repeated or late"
                )
            self.profiles[self.slot] = packet.profile
            self.registered_profile = True
            if self.journal is not None:
                self.journal.append(
                    "player_profile_registration", packet.model_dump(mode="json")
                )
            self._connected.set()
            return
        if isinstance(packet, Started):
            self.admission.progress(packet.request_id, packet.attempt)
            if self.journal is not None:
                self.journal.progress(packet.request_id, packet.attempt)
            return
        if isinstance(packet, Stopped):
            if (
                self.stop_message is None
                or packet.stop_id != self.stop_message.stop_id
                or not packet.owners_joined
                or self.admission.has_unsettled_readers()
            ):
                raise ValueError("player did not establish current owned stop")
            self.stopped.set()
            return
        action = self.admission.consume(packet)
        assert self._pending is not None
        hole, _payload, _deadline, fut = self._pending
        message, cause = validate_submission_message(action, hole)
        if not fut.done():
            fut.set_result((message, cause))

    async def stop_owner(self, deadline: float) -> bool:
        if self.sealed:
            return True
        self.admission.stop_admission()
        if not self.ever_connected:
            self.sealed = True  # This process never admitted a remote policy owner.
            return True
        if self.stop_message is None:
            self.stop_deadline = deadline
            self.stop_message = Stop(
                stop_id=uuid4(),
                deadline_seconds=max(
                    0.000001, min(2, deadline - asyncio.get_running_loop().time())
                ),
            )
        assert self.stop_deadline is not None
        deadline = min(deadline, self.stop_deadline)
        if self.connected:
            assert self.ws is not None
            sent = lifecycle.owned_task(
                self.ws.send_str(self.stop_message.model_dump_json())
            )
            if not await lifecycle.settle({sent}, deadline, cancel=False):
                await lifecycle.settle({sent}, deadline, cancel=True)
                return False
            sent.result()
        acknowledged = lifecycle.owned_task(self.stopped.wait())
        if not await lifecycle.settle({acknowledged}, deadline, cancel=False):
            await lifecycle.settle({acknowledged}, deadline, cancel=True)
            return False
        acknowledged.result()
        if self.admission.has_unsettled_readers():
            return False
        assert self.ws is not None
        received = lifecycle.owned_task(
            self.ws.send_str(
                EvidenceReceived(stop_id=self.stop_message.stop_id).model_dump_json()
            )
        )
        if not await lifecycle.settle({received}, deadline, cancel=False):
            await lifecycle.settle({received}, deadline, cancel=True)
            return False
        received.result()
        self.sealed = True
        return True

    def deliver_bad(self, cause: str) -> None:
        if self._pending is None:
            return
        fut = self._pending[3]
        if not fut.done():
            fut.set_result((None, cause))

    # -- connection lifecycle -----------------------------------------------

    async def attach(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws
        self.ever_connected = True
        if self.welcome is not None:
            await ws.send_str(json.dumps(self.welcome, ensure_ascii=False))
        if self.stop_message is not None:
            await ws.send_str(self.stop_message.model_dump_json())
        elif self._pending is not None:
            self._seen_connection = True
            await self._send_pending()

    def detach(self, ws: web.WebSocketResponse) -> None:
        if self.ws is ws:
            self.ws = None


class GameServer:
    def __init__(
        self,
        config: GameConfig,
        *,
        results_uri: str | None = None,
        save_replay_uri: str | None = None,
        player_failure_uri: str | None = None,
        sandbox: Sandbox | None = None,
    ):
        self.config = config
        self.results_uri = results_uri
        self.save_replay_uri = save_replay_uri
        self.player_failure_uri = player_failure_uri
        self.sandbox = sandbox or Sandbox(
            call_cpu_seconds=config.call_cpu_seconds,
            batch_seconds=config.sandbox_batch_seconds,
        )
        self.native_profiles = list(config.native_profiles)
        self.seats = [
            WsSeat(slot, p.name, self.native_profiles)
            for slot, p in enumerate(config.players)
        ]
        self.engine: Engine | None = None
        self.result: EpisodeResult | None = None
        self.results_doc: dict | None = None
        self.seed = config.resolve_seed()
        self.current_hole = 0
        self.scores = [0] * config.num_seats
        self._global_wss: set[web.WebSocketResponse] = set()
        self._global_send_tasks: dict[web.WebSocketResponse, asyncio.Task] = {}
        self._reported_failure_slot: int | None = None
        self._started_at = time.monotonic()
        self.journal = Journal.from_environment(
            self.seed,
            [seat.admission for seat in self.seats],
            [player.name for player in config.players],
            config,
        )
        for seat in self.seats:
            seat.journal = self.journal
        self._set_welcomes()

    # -- routes --------------------------------------------------------------

    def make_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/healthz", self._handle_healthz)
        app.router.add_get("/player", self._handle_player)
        app.router.add_get("/global", self._handle_global)
        app.router.add_get("/client/global", self._handle_global_client)
        app.router.add_get("/client/player", self._handle_player_client)
        return app

    async def _handle_healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    def _authorized_slot(self, request: web.Request) -> int:
        try:
            slot = int(request.query.get("slot", ""))
        except ValueError:
            raise web.HTTPForbidden(text="bad slot")
        if not 0 <= slot < len(self.seats):
            raise web.HTTPForbidden(text="bad slot")
        token = request.query.get("token", "")
        if not hmac.compare_digest(
            token.encode("utf-8"), self.config.tokens[slot].encode("utf-8")
        ):
            raise web.HTTPForbidden(text="bad token")
        return slot

    async def _handle_global_client(self, request: web.Request) -> web.Response:
        return web.Response(text=GLOBAL_CLIENT_HTML, content_type="text/html")

    async def _handle_player_client(self, request: web.Request) -> web.Response:
        self._authorized_slot(request)
        return web.Response(text=PLAYER_CLIENT_HTML, content_type="text/html")

    def _set_welcomes(self) -> None:
        cfg = self.config
        for seat in self.seats:
            other = 1 - seat.slot
            seat.welcome = {
                "type": contract.MSG_WELCOME,
                "protocol": PROTOCOL,
                "game_version": GAME_VERSION,
                "slot": seat.slot,
                "alias": contract.ALIASES[seat.slot],
                "opponent_alias": contract.ALIASES[other],
                "holes": cfg.holes,
                "hole_deadline_seconds": cfg.hole_deadline_seconds,
                "retry_deadline_seconds": cfg.retry_deadline_seconds,
                "rules": self._rules(),
                # Episode parameters stated outright at t=0 (a policy must
                # never infer one from play).
                "episode": {
                    "game_version": GAME_VERSION,
                    "seats": cfg.num_seats,
                    "slot": seat.slot,
                    "holes": cfg.holes,
                    "deck": cfg.deck,
                    "deck_version": DECK_VERSION,
                    "seed": self.seed,
                    "scoring": "zero_sum_v1",
                },
                "api_docs": API_DOCS,
                "native_profile": cfg.native_profiles[seat.slot].model_dump(),
            }

    def _rules(self) -> dict:
        cfg = self.config
        return {
            "max_tests_per_hole": cfg.max_tests_per_hole,
            "max_impl_chars": contract.MAX_IMPL_CHARS,
            "max_test_name_chars": contract.MAX_TEST_NAME_CHARS,
            "max_why_chars": contract.MAX_WHY_CHARS,
            "max_args_chars": contract.MAX_ARGS_CHARS,
            "max_expect_chars": contract.MAX_EXPECT_CHARS,
            "max_note_chars": contract.MAX_NOTE_CHARS,
            "max_message_bytes": contract.MAX_MESSAGE_BYTES,
            "par_tests_per_hole": cfg.par_tests_per_hole,
            "call_cpu_seconds": cfg.call_cpu_seconds,
            "blocked": list(contract.BLOCKED),
        }

    def _status_snapshot(self) -> dict:
        snapshot = {
            "type": contract.MSG_STATUS,
            "game_version": GAME_VERSION,
            "aliases": list(contract.ALIASES[: self.config.num_seats]),
            "names": [s.name for s in self.seats],
            "holes": self.config.holes,
            "hole": self.current_hole,
            "scores": list(self.scores),
            "done": self.results_doc is not None,
        }
        if self.results_doc is not None:
            snapshot["result"] = self.results_doc
        return snapshot

    async def _handle_global(self, request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps(self._status_snapshot(), ensure_ascii=False))
        self._global_wss.add(ws)
        try:
            async for _msg in ws:
                pass  # broadcast-only
        finally:
            self._global_wss.discard(ws)
            self._global_send_tasks.pop(ws, None)
        return ws

    def _broadcast_global(self, payload: dict) -> None:
        if not self._global_wss:
            return
        message = json.dumps(payload, ensure_ascii=False)
        for ws in tuple(self._global_wss):
            if ws.closed:
                continue
            prev = self._global_send_tasks.get(ws)
            if prev is not None and not prev.done():
                continue  # drop rather than interleave sends
            task = lifecycle.owned_task(self._global_send(ws, message))
            self._global_send_tasks[ws] = task
            task.add_done_callback(lambda t, ws=ws: self._discard_global_send(ws, t))

    def _discard_global_send(self, ws, task) -> None:
        if not task.cancelled() and task.exception() is not None:
            print("spectator progress delivery failed", file=sys.stderr)
        if self._global_send_tasks.get(ws) is task:
            del self._global_send_tasks[ws]

    @staticmethod
    async def _global_send(ws: web.WebSocketResponse, message: str) -> None:
        await ws.send_str(message)

    async def _handle_player(self, request: web.Request):
        slot = self._authorized_slot(request)
        seat = self.seats[slot]
        if seat.connected:
            print(
                f"seat {slot} ({seat.name}): rejected duplicate connection (409)",
                file=sys.stderr,
            )
            raise web.HTTPConflict(text="slot already connected")

        ws = web.WebSocketResponse(
            heartbeat=PLAYER_WS_HEARTBEAT_SECONDS,
            max_msg_size=contract.MAX_PRIVATE_FRAME_BYTES,
        )
        await ws.prepare(request)
        if seat.connected:
            await ws.close(
                code=WSCloseCode.POLICY_VIOLATION, message=b"slot already connected"
            )
            return ws
        seat.ws = ws
        seat.ever_connected = True
        print(f"seat {slot} ({seat.name}) connected", file=sys.stderr)
        try:
            await seat.attach(ws)
            await self._read_player(seat, ws)
        finally:
            seat.detach(ws)
            print(f"seat {slot} ({seat.name}) disconnected", file=sys.stderr)
        return ws

    @staticmethod
    async def _read_player(seat: WsSeat, ws: web.WebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            raw = msg.data
            if (
                len(raw.encode("utf-8", "surrogatepass"))
                > contract.MAX_PRIVATE_FRAME_BYTES
            ):
                seat.deliver_bad("oversize")
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                seat.deliver_bad("malformed")
                continue
            seat.deliver(data)

    # -- episode orchestration -----------------------------------------------

    async def run_episode(self) -> EpisodeResult:
        cfg = self.config
        writer = ReplayWriter(cfg, self.seed)
        engine = Engine(
            cfg,
            self.seats,
            self.sandbox,
            seed=self.seed,
            on_event=writer.append_event,
            on_hole=writer.append_hole,
            on_window=None if self.journal is None else self.journal.window,
            native_profiles=self.native_profiles,
            on_applied=None if self.journal is None else self.journal.applied,
            on_progress=self._on_progress,
            on_never_connected=self._report_player_failure,
        )
        self.engine = engine
        try:
            try:
                result = await engine.run()
            finally:
                deadline = lifecycle.cleanup_deadline()
                owners = {
                    lifecycle.owned_task(seat.stop_owner(deadline))
                    for seat in self.seats
                }
                joined = await lifecycle.settle(owners, deadline, cancel=False)
                if not joined:
                    await lifecycle.settle(owners, deadline, cancel=True)
                    raise lifecycle.OwnershipUnsettled(
                        "game cannot publish before player owners join"
                    )
                if not all(task.result() for task in owners):
                    raise lifecycle.OwnershipUnsettled(
                        "game cannot publish before private player evidence joins"
                    )
        except SandboxError:
            print("harness fault; writing joined partial artifacts", file=sys.stderr)
            doc = fault_results_doc(
                cfg,
                time.monotonic() - self._started_at,
                self.seed,
                DECK_VERSION,
                engine.outcomes,
            )
            self.results_doc = doc
            if self.journal is not None:
                self.journal.configuration["native_profiles"] = [
                    profile.model_dump(mode="json") for profile in self.native_profiles
                ]
                self.journal.finish(
                    doc, owners_joined=all(seat.sealed for seat in self.seats)
                )
            await self._broadcast_done(doc)
            await self._write_artifacts(doc, writer)
            await self._shutdown_grace()
            return EpisodeResult(
                seats=engine.outcomes,
                reason="harness_fault",
                wall_clock_seconds=time.monotonic() - self._started_at,
                holes_played=engine.holes_played,
                seed=self.seed,
                deck_version=DECK_VERSION,
            )
        self.result = result
        self.scores = [int(s.score) for s in result.seats]
        self.current_hole = result.holes_played
        doc = results_doc(cfg, result)
        self.results_doc = doc
        if self.journal is not None:
            self.journal.configuration["native_profiles"] = [
                profile.model_dump(mode="json") for profile in self.native_profiles
            ]
            self.journal.finish(
                doc, owners_joined=all(seat.sealed for seat in self.seats)
            )
        self._log_seat_degrades(result)
        self._log_pacing(result)

        # Done broadcast FIRST (players must not wait out artifact retries).
        await self._broadcast_done(doc)
        errors = await self._write_artifacts(doc, writer)
        await self._shutdown_grace()
        if errors:
            raise OSError("artifact writes failed: " + "; ".join(errors))
        return result

    async def _write_artifacts(self, doc: dict, writer: ReplayWriter) -> list[str]:
        write_errors: list[str] = []

        async def attempt(label, uri, data, content_type):
            if not uri:
                return
            try:
                await uris.write_uri(uri, data, content_type)
            except Exception as exc:  # noqa: BLE001
                write_errors.append(f"{label} -> {uri}: {exc}")
                print(
                    f"artifact write failed: {label} -> {uri}: {exc}", file=sys.stderr
                )

        await attempt(
            "results",
            self.results_uri,
            (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
            "application/json",
        )
        await attempt(
            "replay", self.save_replay_uri, writer.finalize(doc), "application/json"
        )
        return write_errors

    async def _shutdown_grace(self) -> None:
        """Keep /healthz and /global answering for a bounded grace.

        The certification runner pings /global AFTER the player pods start;
        a short episode that exited immediately failed that probe
        (cogame-lantern 0.1.3).
        """
        await asyncio.sleep(SHUTDOWN_GRACE_SECONDS)

    def _on_progress(self, hole: int, scores: list, killer) -> None:
        self.current_hole = hole
        self.scores = list(scores)
        self._broadcast_global(
            {
                "type": contract.MSG_PROGRESS,
                "hole": hole,
                "scores": list(scores),
                "killer": killer,
            }
        )

    def _log_pacing(self, result: EpisodeResult) -> None:
        cfg = self.config
        seats = " ".join(
            f"s{slot}:{o.score:+d}/{o.breaches}b/{o.par_fails}par/{o.fallbacks}fb"
            for slot, o in enumerate(result.seats)
        )
        print(
            f"pacing: reason={result.reason} holes={result.holes_played}"
            f"/{cfg.holes} seats[{seats}] "
            f"wall={result.wall_clock_seconds:.0f}s/"
            f"{cfg.wall_clock_budget_seconds:.0f}s seed={result.seed}",
            file=sys.stderr,
            flush=True,
        )

    def _log_seat_degrades(self, result: EpisodeResult) -> None:
        for slot, o in enumerate(result.seats):
            if o.fallbacks:
                causes = {k: v for k, v in o.fallback_causes.items() if v}
                print(
                    f"seat {slot} ({self.seats[slot].name}): "
                    f"{o.fallbacks} fallback submissions {causes}",
                    file=sys.stderr,
                )

    async def _report_player_failure(self, slot: int) -> None:
        """Declare a never-connected seat to COGAME_PLAYER_FAILURE_URI.

        The URI holds ONE GamePlayerFailure document; with several no-shows
        the lowest slot wins.
        """
        seat = self.seats[slot]
        if seat.ever_connected:
            return
        if (
            self._reported_failure_slot is not None
            and self._reported_failure_slot <= slot
        ):
            return
        self._reported_failure_slot = slot
        if not self.player_failure_uri:
            return
        payload = {
            "message": (
                f"player '{seat.name}' in slot {slot} did not connect within "
                f"{self.config.player_connect_timeout_seconds:g}s "
                f"(reason: connect_timeout); the seat plays the literalist "
                f"fallback unless it connects later"
            ),
            "failed_policy_index": slot,
        }
        try:
            await uris.write_uri(
                self.player_failure_uri,
                json.dumps(payload).encode("utf-8"),
                "application/json",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"player-failure report failed: {exc}", file=sys.stderr)

    async def _broadcast_done(self, doc: dict) -> None:
        message = json.dumps(
            {"type": contract.MSG_DONE, "result": doc}, ensure_ascii=False
        )

        async def _send(ws: web.WebSocketResponse) -> None:
            await ws.send_str(message)
            await ws.close()

        deadline = min(
            lifecycle.cleanup_deadline(),
            asyncio.get_running_loop().time() + DONE_SEND_TIMEOUT_SECONDS,
        )
        previous = set(self._global_send_tasks.values())
        if not await lifecycle.settle(previous, deadline, cancel=True):
            raise lifecycle.OwnershipUnsettled(
                "spectator writer did not join before final publication"
            )
        sockets = {ws for ws in self._global_wss if not ws.closed}
        for seat in self.seats:
            if seat.connected:
                assert seat.ws is not None
                sockets.add(seat.ws)
        delivery = {lifecycle.owned_task(_send(ws)) for ws in sockets}
        if not await lifecycle.settle(
            delivery, deadline, cancel=False
        ) and not await lifecycle.settle(delivery, deadline, cancel=True):
            raise lifecycle.OwnershipUnsettled("final socket writer did not join")
        for task in delivery:
            if task.cancelled() or task.exception() is not None:
                print(
                    "final spectator/player delivery failed after acknowledged ownership",
                    file=sys.stderr,
                )


# -- replay mode -------------------------------------------------------------

DEFAULT_VIEWER_DIST = Path(__file__).resolve().parents[2] / "viewer" / "dist"

REPLAY_PLACEHOLDER_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>cogame-cogolf replay</title>
<style>
  body { font-family: ui-monospace, monospace; margin: 2rem; }
  dt { font-weight: bold; margin-top: .6rem; }
  .note { margin-top: 2rem; color: #666; }
</style>
</head>
<body>
<h1>cogame-cogolf replay</h1>
<dl id="info">loading /replay-data ...</dl>
<p class="note">Placeholder viewer: this server was built without the
static replay viewer bundle (run viewer/build_viewer.sh).</p>
<script>
async function load() {
  const resp = await fetch("/replay-data");
  const doc = await resp.json();
  const info = document.getElementById("info");
  info.textContent = "";
  const add = (label, value) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = String(value);
    info.appendChild(dt);
    info.appendChild(dd);
  };
  add("players", doc.names.join(", "));
  add("aliases", doc.aliases.join(", "));
  add("scores", doc.result.scores.join(", "));
  add("reason", doc.result.reason);
  add("holes", doc.holes.length);
}
load().catch(e => {
  document.getElementById("info").textContent = "failed: " + e.message;
});
</script>
</body>
</html>
"""


def make_replay_app(
    replay_bytes: bytes, viewer_dist: Path | None = None
) -> web.Application:
    """Replay-mode app: JSON at /replay-data, viewer at /client/replay/."""
    replay = Replay.parse(replay_bytes)
    dist = DEFAULT_VIEWER_DIST if viewer_dist is None else Path(viewer_dist)
    index = dist / "index.html"
    have_bundle = index.is_file()
    if not have_bundle:
        print(
            f"viewer bundle not found at {dist}; serving placeholder page",
            file=sys.stderr,
        )

    async def handle_replay_data(request):
        return web.Response(body=replay_bytes, content_type="application/json")

    async def handle_replay_client(request):
        if have_bundle:
            raise web.HTTPFound("/client/replay/")
        return web.Response(text=REPLAY_PLACEHOLDER_HTML, content_type="text/html")

    async def handle_replay_index(request):
        if have_bundle:
            return web.FileResponse(index)
        return web.Response(text=REPLAY_PLACEHOLDER_HTML, content_type="text/html")

    async def handle_healthz(request):
        return web.json_response({"status": "ok"})

    async def handle_replay_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(
            json.dumps(
                {
                    "type": "replay_header",
                    "format": replay.doc["format"],
                    "version": replay.doc["version"],
                    "names": replay.names,
                    "aliases": replay.aliases,
                    "result": replay.result,
                },
                ensure_ascii=False,
            )
        )
        async for _msg in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/replay", handle_replay_ws)
    app.router.add_get("/replay-data", handle_replay_data)
    app.router.add_get("/client/replay", handle_replay_client)
    app.router.add_get("/client/replay/", handle_replay_index)
    if have_bundle:
        app.router.add_static("/client/replay/", dist)
    return app


# -- process entry point -----------------------------------------------------


async def async_main() -> int:
    host = os.environ.get("COGAME_HOST", "0.0.0.0")
    port = int(os.environ.get("COGAME_PORT", "8080"))

    load_replay_uri = os.environ.get("COGAME_LOAD_REPLAY_URI", "")
    if load_replay_uri:
        replay_bytes = await uris.read_uri(load_replay_uri)
        try:
            app = make_replay_app(replay_bytes)
        except ReplayError as exc:
            print(f"invalid replay at {load_replay_uri}: {exc}", file=sys.stderr)
            return 2
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        print(
            f"cogame-cogolf replay mode on {host}:{port} "
            f"({len(replay_bytes)} replay bytes)",
            file=sys.stderr,
        )
        try:
            await asyncio.Event().wait()
            return 0
        finally:
            cleanup = lifecycle.owned_task(runner.cleanup())
            deadline = lifecycle.cleanup_deadline()
            if not await lifecycle.settle({cleanup}, deadline, cancel=False):
                await lifecycle.settle({cleanup}, deadline, cancel=True)
                raise lifecycle.OwnershipUnsettled("replay HTTP writers did not join")
            cleanup.result()

    config_uri = os.environ.get("COGAME_CONFIG_URI", "")
    if not config_uri:
        print("COGAME_CONFIG_URI is required", file=sys.stderr)
        return 2
    try:
        if uris.local_path(config_uri) is not None:
            config = GameConfig.from_file_uri(config_uri)
        else:
            raw = await uris.read_uri(config_uri)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ConfigError(
                    f"config at {config_uri} is not valid JSON: {exc}"
                ) from exc
            config = GameConfig.from_dict(data)
        deck = load_deck(config.deck)
        if config.holes > len(deck):
            raise ConfigError(
                f"deck {config.deck!r} has {len(deck)} specs, config asks "
                f"for {config.holes} holes"
            )
    except (ConfigError, DeckError) as exc:
        print(f"invalid config: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"cannot read config from {config_uri}: {exc}", file=sys.stderr)
        return 2

    server = GameServer(
        config,
        results_uri=os.environ.get("COGAME_RESULTS_URI"),
        save_replay_uri=os.environ.get("COGAME_SAVE_REPLAY_URI"),
        player_failure_uri=os.environ.get("COGAME_PLAYER_FAILURE_URI"),
    )
    runner = web.AppRunner(server.make_app())
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    print(
        f"cogame-cogolf serving on {host}:{port} "
        f"({config.num_seats} seats, deck {config.deck}, "
        f"{config.holes} holes, seed {server.seed})",
        file=sys.stderr,
        flush=True,
    )
    try:
        result = await server.run_episode()
        print(
            f"episode over: reason={result.reason} "
            f"scores={[o.score for o in result.seats]} "
            f"wall={result.wall_clock_seconds:.0f}s",
            file=sys.stderr,
        )
        return 0
    finally:
        cleanup = lifecycle.owned_task(runner.cleanup())
        deadline = lifecycle.cleanup_deadline()
        if not await lifecycle.settle({cleanup}, deadline, cancel=False):
            await lifecycle.settle({cleanup}, deadline, cancel=True)
            raise lifecycle.OwnershipUnsettled("game HTTP writers did not join")
        cleanup.result()


def main() -> int | None:
    code = lifecycle.main_owned(async_main())
    sys.stdout.flush()
    sys.stderr.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
