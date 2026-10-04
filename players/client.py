"""Definitive v2 player windows, private native progress, and owned stop ACK."""

from __future__ import annotations

import asyncio
import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Literal

import aiohttp
from cogame_cogolf import contract, lifecycle
from cogame_cogolf.native_profile import NativeProfile
from cogame_cogolf.private_window import (
    Action,
    EvidenceReceived,
    ObservationPacket,
    Ready,
    Stop,
    Stopped,
)
from cogame_cogolf.submission import normalize_submission
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from .native import Attempt, Progress
from .progress import ProgressWriter
from .resolver import OwnedResolver

PROTOCOL = contract.PROTOCOL
WS_URL_ENV_VARS = (contract.ENV_PLAYER_WS_URL, contract.ENV_PLAYER_WS_URL_LEGACY)
CONNECT_TIMEOUT_SECONDS = 20.0
DEADLINE_MARGIN_SECONDS = 3.0


class PlayerError(RuntimeError):
    """A protocol or owned-lifecycle invariant was violated."""


class Welcome(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    type: Literal["welcome"]
    protocol: Literal["cogame.cogolf.v2"]
    slot: int
    native_profile: NativeProfile


class Done(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    type: Literal["done"]
    result: dict[str, JsonValue]


SERVER_PACKET = TypeAdapter(
    Welcome | ObservationPacket | Stop | EvidenceReceived | Done,
    config=ConfigDict(hide_input_in_errors=True),
)


class Policy(ABC):
    """One source-owned submission policy; native facts remain private attempts."""

    def __init__(self):
        self.attempts: list[Attempt] = []
        self.progress: Progress | None = None
        self.selected_attempt_id: str | None = None
        self.native_profile: NativeProfile | None = None

    def on_welcome(self, welcome: dict) -> None:
        self.native_profile = NativeProfile.model_validate(welcome["native_profile"])

    @abstractmethod
    async def submission(self, hole: int, observation: dict) -> dict:
        """Return ordinary impl/tests/note control fields."""

    def fallback(self, hole: int, observation: dict) -> dict:
        from .scripted import scripted_submission

        return scripted_submission("literalist", hole, observation)

    def on_done(self, result: dict) -> None:
        pass

    def readers_joined(self) -> bool:
        return all(
            attempt.response_reader_joined is not False for attempt in self.attempts
        )


def ws_url_from_env() -> str:
    for name in WS_URL_ENV_VARS:
        if name in os.environ:
            return os.environ[name]
    raise PlayerError("player requires its authenticated websocket URL")


async def _play_connection(ws: aiohttp.ClientWebSocketResponse, policy: Policy) -> dict:
    worker: asyncio.Task | None = None
    writer: ProgressWriter | None = None
    stop: Stop | None = None
    acknowledged = False
    welcomed = False
    receiver: asyncio.Task | None = None

    async def answer(window: ObservationPacket, progress: ProgressWriter) -> None:
        deadline = asyncio.get_running_loop().time() + max(
            0.001, window.deadline_seconds - DEADLINE_MARGIN_SECONDS
        )
        policy.progress = progress.update
        policy.selected_attempt_id = None
        call = lifecycle.owned_task(
            policy.submission(window.hole, window.observation.model_dump())
        )
        try:
            if await lifecycle.settle({call}, deadline, cancel=False):
                payload = call.result()
            else:
                if not await lifecycle.settle(
                    {call}, lifecycle.cleanup_deadline(), cancel=True
                ):
                    raise lifecycle.OwnershipUnsettled("hole policy owner did not join")
                payload = policy.fallback(window.hole, window.observation.model_dump())
                policy.selected_attempt_id = None
            if not policy.readers_joined():
                raise lifecycle.OwnershipUnsettled("native reader remains unresolved")
            control = normalize_submission(payload, window.hole)
            if control is None:
                control = normalize_submission(
                    policy.fallback(window.hole, window.observation.model_dump()),
                    window.hole,
                )
                policy.selected_attempt_id = None
            assert control is not None
            if not await progress.finish(lifecycle.cleanup_deadline()):
                raise lifecycle.OwnershipUnsettled(
                    "private native progress was not delivered"
                )
            if stop is None:
                async with asyncio.timeout_at(deadline + lifecycle.CLEANUP_SECONDS):
                    await ws.send_str(
                        Action(
                            request_id=window.request_id,
                            action=control,
                            selected_attempt_id=policy.selected_attempt_id,
                        ).model_dump_json()
                    )
        finally:
            if not await lifecycle.settle(
                {call}, lifecycle.cleanup_deadline(), cancel=True
            ):
                raise lifecycle.OwnershipUnsettled(
                    "policy completion left owned work unresolved"
                )

    try:
        while True:
            receiver = lifecycle.owned_task(ws.receive())
            waiting = {receiver}
            if worker is not None and not worker.done():
                waiting.add(worker)
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if worker is not None and worker in done and not worker.cancelled():
                worker.result()
            message = await receiver
            if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                if acknowledged:
                    return {}
                raise PlayerError(
                    "player disconnected before joined stop acknowledgement"
                )
            if message.type is aiohttp.WSMsgType.ERROR:
                raise PlayerError(
                    "player websocket transport failed"
                ) from ws.exception()
            if message.type is not aiohttp.WSMsgType.TEXT:
                raise PlayerError("player requires JSON text protocol frames")
            packet = SERVER_PACKET.validate_json(message.data)
            if isinstance(packet, Welcome):
                if welcomed:
                    raise PlayerError(
                        "welcome was repeated on an established connection"
                    )
                policy.on_welcome(packet.model_dump())
                assert policy.native_profile is not None
                await ws.send_str(
                    Ready(
                        slot=packet.slot, profile=policy.native_profile
                    ).model_dump_json()
                )
                welcomed = True
            elif isinstance(packet, ObservationPacket):
                if not welcomed or stop is not None:
                    raise PlayerError("observation arrived outside player admission")
                if worker is not None:
                    if not await lifecycle.settle(
                        {worker}, lifecycle.cleanup_deadline(), cancel=True
                    ):
                        raise lifecycle.OwnershipUnsettled(
                            "previous decision owner did not join"
                        )
                    if not worker.cancelled():
                        worker.result()
                if not policy.readers_joined():
                    raise lifecycle.OwnershipUnsettled(
                        "previous native reader did not join"
                    )
                writer = ProgressWriter(
                    packet.request_id,
                    lambda value: ws.send_str(value.model_dump_json()),
                )
                worker = lifecycle.owned_task(answer(packet, writer))
            elif isinstance(packet, Stop):
                if stop is not None and packet != stop:
                    raise PlayerError("engine rewrote its immutable stop request")
                if stop is None:
                    stop = packet
                    deadline = min(
                        lifecycle.cleanup_deadline(),
                        asyncio.get_running_loop().time() + packet.deadline_seconds,
                    )
                    owner = lifecycle.current_owner.get()
                    assert owner is not None
                    owner.stopping = True
                    owner.deadline = deadline
                    joined = worker is None or await lifecycle.settle(
                        {worker}, deadline, cancel=True
                    )
                    if not joined or not policy.readers_joined():
                        raise lifecycle.OwnershipUnsettled(
                            "stop could not join native policy owners"
                        )
                    excluded = (
                        frozenset({writer.task}) if writer is not None else frozenset()
                    )
                    if not await lifecycle.settle_owner(
                        owner, deadline, cancel=True, excluding=excluded
                    ):
                        raise lifecycle.OwnershipUnsettled(
                            "stop retains an unjoined policy descendant"
                        )
                    if writer is not None and not await writer.finish(deadline):
                        raise lifecycle.OwnershipUnsettled(
                            "stop could not drain private progress"
                        )
                    async with asyncio.timeout_at(deadline):
                        await ws.send_str(
                            Stopped(
                                stop_id=packet.stop_id, owners_joined=True
                            ).model_dump_json()
                        )
            elif isinstance(packet, EvidenceReceived):
                if stop is None or packet.stop_id != stop.stop_id:
                    raise PlayerError(
                        "evidence acknowledgement has no matching engine stop"
                    )
                acknowledged = True
            else:
                if not acknowledged:
                    raise PlayerError(
                        "public result preceded owned private evidence acknowledgement"
                    )
                policy.on_done(packet.result)
                return packet.result
    finally:
        deadline = lifecycle.cleanup_deadline()
        owner = lifecycle.current_owner.get()
        assert owner is not None
        owner.stopping = True
        owner.deadline = deadline
        owned = {task for task in (worker, receiver) if task is not None}
        joined = await lifecycle.settle(owned, deadline, cancel=True)
        if joined and policy.readers_joined() and writer is not None:
            joined = await writer.finish(deadline)
        if not joined or not policy.readers_joined():
            raise lifecycle.OwnershipUnsettled(
                "player connection has unresolved native owners"
            )


async def play_episode(policy: Policy, url: str | None = None) -> dict:
    owner = lifecycle.Owner(parent=lifecycle.current_owner.get())
    token = lifecycle.current_owner.set(owner)
    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=OwnedResolver()),
        timeout=aiohttp.ClientTimeout(
            total=None,
            connect=CONNECT_TIMEOUT_SECONDS,
            sock_connect=CONNECT_TIMEOUT_SECONDS,
        ),
    )
    ws: aiohttp.ClientWebSocketResponse | None = None
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
            ws = await session.ws_connect(
                url or ws_url_from_env(),
                heartbeat=20,
                max_msg_size=contract.MAX_PRIVATE_FRAME_BYTES,
            )
        return await _play_connection(ws, policy)
    finally:
        deadline = lifecycle.cleanup_deadline()
        owner.stopping = True
        owner.deadline = deadline
        closing = {lifecycle.owned_task(session.close())}
        if ws is not None:
            closing.add(lifecycle.owned_task(ws.close()))
        try:
            if not await lifecycle.settle(closing, deadline, cancel=False):
                await lifecycle.settle(closing, deadline, cancel=True)
                raise lifecycle.OwnershipUnsettled("player socket close did not join")
            for task in closing:
                task.result()
            if not await lifecycle.settle_owner(owner, deadline, cancel=True):
                raise lifecycle.OwnershipUnsettled(
                    "player connection retains unjoined work"
                )
        finally:
            lifecycle.current_owner.reset(token)


def run_policy_main(policy_factory: Callable[[], Policy]) -> int:
    lifecycle.main_owned(play_episode(policy_factory()))
    return 0


def main_for(policy_factory: Callable[[], Policy]) -> None:
    sys.exit(run_policy_main(policy_factory))
