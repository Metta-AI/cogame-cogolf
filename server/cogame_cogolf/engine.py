"""The hole loop: cogolf's engine, transport-free (docs/PROTOCOL.md).

One hole, in exact resolution order:

 1. draw and reveal — both seats get the SAME observation in ONE parallel
    batch (concurrent awaits, never seat-by-seat);
 2. collect — wait until ``hole_deadline_seconds`` for both submissions,
    concurrently;
 3. retry once — re-send the identical observation with ``retry: true`` and
    ``retry_deadline_seconds``, again concurrently;
 4. fallback — a seat still without a valid submission gets the scripted
    ``literalist`` move: a real, legal play. No seat is ever removed;
 5. sanitise — rune-boundary truncation, caps, control characters;
 6. load — each impl in the sandbox; a broken impl fails every shot;
 7. legality gate — every test is run against the spec's hidden reference;
 8. cross-fire — seat 0's legal tests at seat 1's impl and vice versa;
 9. par audit — the spec's four hidden cases against each impl;
10. score the hole and emit the beats;
11. wall guard — stop before starting a hole that cannot finish.

Degrade, never hang: every wait is bounded (connect timeout, hole
deadline, retry deadline, sandbox batch cap, wall clock) and every failure
has a move that keeps play going.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
import unicodedata
from typing import Awaitable, Callable, Protocol, Sequence

from . import contract, scoring
from .baseline import literalist
from .config import GameConfig
from .results import (REASON_COMPLETE, REASON_DEADLINE, EpisodeResult,
                      SeatOutcome)
from .sandbox import Sandbox, SandboxError, describe
from .specs import DECK_VERSION, load_deck
from .values import BadValue, canon, equal, fingerprint

HISTORY_HOLES = 4
MAX_HISTORY_CHARS = 1200
PROGRESS_INTERVAL_SECONDS = 30.0
PAR_ID_BASE = 10_000


# -- text hygiene -------------------------------------------------------------
# Every string that lands in the replay is decoded once, stripped of lone
# surrogates and control characters, and truncated on RUNE (code point)
# boundaries — never bytes. Python `str` slicing is code-point based, so the
# rule is: decode at the websocket edge, cap the `str`, re-encode last.

def clean_text(value, limit: int | None = None) -> str:
    """Sanitise ``value`` for the wire and the replay."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = value.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
    value = "".join(
        ch for ch in value
        if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    if limit is not None and len(value) > limit:
        value = value[:limit - 1] + "\u2026"
    return value


def compact(value) -> str:
    """Compact JSON for a value, for the cap checks and the replay."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      default=str)


# -- submission validation ----------------------------------------------------

def validate_submission_message(data, hole: int) -> tuple[dict | None,
                                                          str | None]:
    """Classify a decoded client message for the pending ``hole``.

    ``(payload, None)`` when valid, ``(None, "wrong_hole")`` when it
    addresses another hole (dropped; the hole keeps waiting), else
    ``(None, "malformed" | "oversize")``.
    """
    if not isinstance(data, dict) or data.get("type") != contract.MSG_SUBMISSION:
        return None, "malformed"
    got = data.get("hole")
    if isinstance(got, bool) or not isinstance(got, int):
        return None, "malformed"
    if got != hole:
        return None, "wrong_hole"
    impl = data.get("impl")
    if not isinstance(impl, str) or not impl.strip():
        return None, "malformed"
    if len(impl) > contract.MAX_IMPL_CHARS:
        return None, "oversize"
    tests = data.get("tests", [])
    if tests is None:
        tests = []
    if not isinstance(tests, list):
        return None, "malformed"
    note = data.get("note", "")
    if note is not None and not isinstance(note, str):
        return None, "malformed"
    return data, None


def sanitize_submission(data: dict, hole: int, max_tests: int) -> dict:
    """Normalise a validated submission: caps, truncation, drops."""
    raw_tests = [t for t in (data.get("tests") or []) if isinstance(t, dict)]
    dropped = max(0, len(raw_tests) - max_tests)
    tests = []
    for idx, entry in enumerate(raw_tests[:max_tests]):
        tests.append({
            "idx": idx,
            "name": clean_text(entry.get("name") or f"test {idx + 1}",
                               contract.MAX_TEST_NAME_CHARS),
            "args": entry.get("args"),
            "expect": entry.get("expect"),
            "why": clean_text(entry.get("why") or "",
                              contract.MAX_WHY_CHARS),
        })
    return {
        "hole": hole,
        "impl": clean_text(data.get("impl") or ""),
        "tests": tests,
        "note": clean_text(data.get("note") or "", contract.MAX_NOTE_CHARS),
        "dropped_tests": dropped,
    }


# -- transport-free seat source ----------------------------------------------

class SubmissionSource(Protocol):
    """Per-seat submission provider (websocket seat, scripted fake, ...)."""

    wrong_hole_count: int

    async def wait_connected(self, timeout_seconds: float) -> bool:
        """Block until the seat has connected (True) or timeout (False)."""
        ...

    async def get_submission(self, hole: int, payload: dict,
                             deadline_at: float) -> tuple[dict | None,
                                                          str | None]:
        """Send ``payload`` and wait for this hole's submission.

        Returns ``(message, None)`` for a valid reply, else
        ``(None, cause)`` with ``cause`` in ``FALLBACK_CAUSES``. Must never
        raise except on cancellation; a raise is recorded ``host_error``.
        """
        ...


class Engine:
    def __init__(self, config: GameConfig, sources: Sequence[SubmissionSource],
                 sandbox: Sandbox, *, seed: int | None = None,
                 on_event: Callable[[dict], None] | None = None,
                 on_hole: Callable[[dict], None] | None = None,
                 on_progress: Callable[[int, list, dict | None], None] | None = None,
                 on_never_connected: Callable[[int], Awaitable[None]] | None = None,
                 progress_interval_seconds: float = PROGRESS_INTERVAL_SECONDS):
        if len(sources) != config.num_seats:
            raise ValueError(
                f"need {config.num_seats} sources, got {len(sources)}")
        self._config = config
        self._sources = list(sources)
        self._sandbox = sandbox
        self._on_event = on_event
        self._on_hole = on_hole
        self._on_progress = on_progress
        self._on_never_connected = on_never_connected
        self._progress_interval = progress_interval_seconds
        self.seed = config.resolve_seed() if seed is None else int(seed)
        self.deck = load_deck(config.deck)
        self.deck_version = DECK_VERSION
        self.outcomes = tuple(SeatOutcome() for _ in range(config.num_seats))
        self.per_hole: list[list[int]] = []
        self.shots: list[dict] = []
        self.current_hole = 0
        self.holes_played = 0
        self._start = 0.0
        self._wall_deadline = 0.0
        self._deadline_hit = False
        self._last_reveal = 0.0

        self._history: list[list[dict]] = [[] for _ in range(config.num_seats)]

        keys = sorted(self.deck)
        if config.holes > len(keys):
            raise ValueError(
                f"deck {config.deck!r} has {len(keys)} specs, "
                f"config asks for {config.holes} holes")
        self.spec_keys = random.Random(self.seed).sample(keys, config.holes)

    # -- helpers -------------------------------------------------------------

    def _log(self, msg: str) -> None:
        print(f"engine: {msg}", file=sys.stderr, flush=True)

    def _emit(self, kind: str, **fields) -> None:
        event = {"kind": kind, **fields}
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception as exc:  # noqa: BLE001 - observers never crash
                self._log(f"event hook raised {type(exc).__name__}: {exc}")

    def _wall_remaining(self) -> float:
        return self._wall_deadline - time.monotonic()

    @property
    def scores(self) -> list[int]:
        return scoring.match_scores(self.per_hole)

    # -- run -----------------------------------------------------------------

    async def run(self) -> EpisodeResult:
        cfg = self._config
        self._start = time.monotonic()
        self._wall_deadline = self._start + cfg.wall_clock_budget_seconds
        await self._await_connections()
        reason = REASON_COMPLETE
        for hole in range(1, cfg.holes + 1):
            if self._wall_remaining() < cfg.hole_reserve_seconds:
                self._deadline_hit = True
                self._log(
                    f"stopping before hole {hole}: "
                    f"{self._wall_remaining():.0f}s left, reserve is "
                    f"{cfg.hole_reserve_seconds:.0f}s")
                reason = REASON_DEADLINE
                break
            await self._space_holes()
            self.current_hole = hole
            try:
                await self._play_hole(hole)
            except SandboxError:
                raise
            self.holes_played = hole
            if self._wall_remaining() <= 0:
                self._deadline_hit = True
                if hole < cfg.holes:
                    reason = REASON_DEADLINE
                    break
        wall = time.monotonic() - self._start
        scores = self.scores
        killer = scoring.killer_test(self.shots, self.per_hole, scores)
        self._emit("episode_end", reason=reason, scores=list(scores),
                   killer_test=killer)
        return EpisodeResult(seats=tuple(self.outcomes), reason=reason,
                             wall_clock_seconds=wall,
                             holes_played=self.holes_played, seed=self.seed,
                             deck_version=self.deck_version,
                             killer_test=killer)

    async def _await_connections(self) -> None:
        cfg = self._config
        wait = min(cfg.player_connect_timeout_seconds,
                   max(0.0, self._wall_remaining()))
        connected = await asyncio.gather(
            *(source.wait_connected(wait) for source in self._sources))
        for slot, ok in enumerate(connected):
            if ok:
                continue
            self._log(f"seat {slot} not connected after {wait:g}s; it plays "
                      f"the literalist fallback until it connects")
            if self._on_never_connected is not None:
                try:
                    await self._on_never_connected(slot)
                except Exception as exc:  # noqa: BLE001
                    self._log(f"never-connected hook failed: {exc!r}")

    async def _space_holes(self) -> None:
        """Floor the wall-clock gap between two hole reveals.

        The Bedrock sidecar caps 30 requests/minute/episode; an all-scripted
        episode would otherwise burst it.
        """
        gap = self._config.min_hole_spacing_seconds
        if gap <= 0 or self._last_reveal == 0.0:
            return
        wait = gap - (time.monotonic() - self._last_reveal)
        if wait > 0:
            await asyncio.sleep(min(wait, gap))

    # -- one hole ------------------------------------------------------------

    async def _play_hole(self, hole: int) -> None:
        cfg = self._config
        spec = self.deck[self.spec_keys[hole - 1]]
        self._last_reveal = time.monotonic()
        prompt = clean_text(spec.PROMPT)
        self._emit("hole_start", hole=hole, spec_key=spec.KEY,
                   title=clean_text(spec.TITLE, 48),
                   prompt_head=clean_text(prompt.replace("\n", " "), 160))

        submissions, causes = await self._collect(hole, spec)

        # 5/6. sanitise + load, 7. legality, 8. cross-fire, 9. par audit
        legality = await self._legality(spec, submissions)
        verdicts, par_fails, broken = await self._cross_fire(
            spec, submissions, legality)

        breaches = [0, 0]
        for slot in range(cfg.num_seats):
            for shot in verdicts[slot]:
                if shot["outcome"] == "breach":
                    breaches[slot] += 1
        score = scoring.hole_score(breaches, par_fails)
        self.per_hole.append(score)
        cumulative = scoring.cumulative(self.per_hole)[-1]

        for slot in range(cfg.num_seats):
            seat = self.outcomes[slot]
            other = 1 - slot
            seat.hole_scores.append(score[slot])
            seat.breaches += breaches[slot]
            seat.breaches_taken += breaches[other]
            seat.par_fails += par_fails[slot]
            seat.tests_fired += sum(1 for s in verdicts[slot] if s["legal"])
            seat.illegal_tests += sum(
                1 for s in verdicts[slot] if not s["legal"])

        # 10. beats
        for slot in range(cfg.num_seats):
            sub = submissions[slot]
            self._emit("submission", hole=hole, slot=slot,
                       impl_lines=len(sub["impl"].splitlines()),
                       impl_chars=len(sub["impl"]),
                       test_count=len(sub["tests"]), note=sub["note"],
                       fallback=causes[slot])
        for slot in range(cfg.num_seats):
            for shot in verdicts[slot]:
                self._emit("test_verdict", **shot)
                self.shots.append(shot)
        for slot in range(cfg.num_seats):
            self._emit("par_result", hole=hole, slot=slot,
                       par_fails=par_fails[slot],
                       par_total=cfg.par_tests_per_hole)
        self._emit("hole_score", hole=hole, score=list(score),
                   cumulative=list(cumulative))

        if self._on_hole is not None:
            self._on_hole(self._hole_record(
                hole, spec, submissions, causes, verdicts, par_fails, broken,
                score, cumulative))
        if self._on_progress is not None:
            try:
                self._on_progress(hole, list(cumulative), None)
            except Exception as exc:  # noqa: BLE001
                self._log(f"progress hook raised {type(exc).__name__}: {exc}")
        self._history_push(hole, spec, submissions, verdicts, par_fails, score)
        self._log(f"hole {hole} ({spec.KEY}): score {score} cumulative "
                  f"{cumulative} breaches {breaches} par_fails {par_fails}")

    # -- steps 1-4: the parallel batch, one retry, then the fallback ---------

    async def _collect(self, hole: int, spec) -> tuple[list[dict], list]:
        cfg = self._config
        payloads = [self._observation_message(hole, spec, slot, retry=False)
                    for slot in range(cfg.num_seats)]
        deadline = min(cfg.hole_deadline_seconds,
                       max(1.0, self._wall_remaining()))
        replies = await self._batch(range(cfg.num_seats), payloads, deadline)

        retry_slots = [slot for slot, (msg, _) in replies.items() if msg is None]
        if retry_slots:
            retry_payloads = [
                self._observation_message(hole, spec, slot, retry=True)
                for slot in retry_slots]
            retry_deadline = min(cfg.retry_deadline_seconds,
                                 max(1.0, self._wall_remaining()))
            again = await self._batch(retry_slots, retry_payloads,
                                      retry_deadline)
            replies.update(again)

        submissions: list[dict] = []
        causes: list[dict | None] = []
        for slot in range(cfg.num_seats):
            message, cause = replies[slot]
            if message is None:
                cause = cause if cause in contract.FALLBACK_CAUSES else "timeout"
                seat = self.outcomes[slot]
                seat.fallbacks += 1
                seat.fallback_causes[cause] += 1
                message = literalist(spec, hole, cfg.max_tests_per_hole)
                causes.append({"cause": cause, "baseline": "literalist"})
                self._log(f"seat {slot} hole {hole}: {cause} -> literalist "
                          f"fallback")
            else:
                causes.append(None)
            submissions.append(
                sanitize_submission(message, hole, cfg.max_tests_per_hole))
        return submissions, causes

    async def _batch(self, slots, payloads, deadline: float) -> dict:
        """ONE parallel batch: every seat's observation goes out before any
        reply is awaited (concurrent awaits, never sequential)."""
        slots = list(slots)
        deadline_at = time.monotonic() + deadline
        for payload in payloads:
            payload["deadline_seconds"] = deadline

        async def ask(slot: int, payload: dict):
            source = self._sources[slot]
            try:
                return await source.get_submission(payload["hole"], payload,
                                                   deadline_at)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log(f"seat {slot} source raised "
                          f"{type(exc).__name__}: {exc} (host_error)")
                return None, "host_error"

        results = await asyncio.gather(
            *(ask(slot, payload) for slot, payload in zip(slots, payloads)))
        return dict(zip(slots, results))

    # -- steps 7-9: the sandbox ---------------------------------------------

    async def _legality(self, spec, submissions) -> list[list[dict]]:
        """Run every test of both seats against the spec's reference."""
        calls = []
        index: list[list[dict]] = [[] for _ in submissions]
        next_id = 0
        arity = len(spec.SIGNATURE.get("params") or [])
        for slot, sub in enumerate(submissions):
            for test in sub["tests"]:
                record = {"reason": None, "call_id": None}
                args = test["args"]
                expect = test["expect"]
                if not isinstance(args, list):
                    record["reason"] = "not_json"
                else:
                    try:
                        args = canon(args)
                        expect = canon(expect)
                    except BadValue:
                        record["reason"] = "not_json"
                    else:
                        test["args"] = args
                        test["expect"] = expect
                        if len(args) != arity:
                            record["reason"] = "arity"
                        elif len(compact(args)) > contract.MAX_ARGS_CHARS \
                                or len(compact(expect)) > contract.MAX_EXPECT_CHARS:
                            record["reason"] = "oversize"
                if record["reason"] is None:
                    record["call_id"] = next_id
                    calls.append({"id": next_id, "args": args})
                    next_id += 1
                index[slot].append(record)

        batch = None
        if calls:
            batch = await asyncio.to_thread(
                self._sandbox.run_reference, spec.REFERENCE_IMPL, calls)
            if batch.broken:
                raise SandboxError(
                    f"spec {spec.KEY}: the reference did not load "
                    f"({batch.broken})")

        for slot, sub in enumerate(submissions):
            seen: set[str] = set()
            for test, record in zip(sub["tests"], index[slot]):
                if record["reason"] is not None:
                    continue
                result = batch.get(record["call_id"])
                if not result.ok:
                    record["reason"] = ("ref_timeout" if result.kind == "timeout"
                                        else "ref_error")
                    continue
                if not equal(result.value, test["expect"]):
                    record["reason"] = "ref_mismatch"
                    continue
                key = fingerprint(test["args"])
                if key in seen:
                    record["reason"] = "duplicate"
                    continue
                seen.add(key)
        return index

    async def _cross_fire(self, spec, submissions, legality):
        """Fire each seat's legal tests at the other's impl, then audit both."""
        cfg = self._config
        par_tests = list(spec.PAR_TESTS)[:cfg.par_tests_per_hole]
        jobs = []
        for defender in range(cfg.num_seats):
            attacker = 1 - defender
            calls = []
            for test, record in zip(submissions[attacker]["tests"],
                                    legality[attacker]):
                if record["reason"] is None:
                    calls.append({"id": test["idx"], "args": test["args"]})
            for i, par in enumerate(par_tests):
                calls.append({"id": PAR_ID_BASE + i, "args": par["args"]})
            jobs.append((defender, calls))

        batches = await asyncio.gather(*(
            asyncio.to_thread(self._sandbox.run,
                              submissions[defender]["impl"], calls)
            for defender, calls in jobs))

        broken = [b.broken for b in batches]
        for slot, reason in enumerate(broken):
            if reason:
                self._log(f"seat {slot} hole {self.current_hole}: impl broken "
                          f"({reason})")

        verdicts: list[list[dict]] = [[] for _ in range(cfg.num_seats)]
        for attacker in range(cfg.num_seats):
            defender = 1 - attacker
            batch = batches[defender]
            for test, record in zip(submissions[attacker]["tests"],
                                    legality[attacker]):
                shot = {
                    "hole": self.current_hole,
                    "slot": attacker,
                    "target_slot": defender,
                    "idx": test["idx"],
                    "name": test["name"],
                    "args": test["args"],
                    "expect": test["expect"],
                    "why": test["why"],
                    "legal": record["reason"] is None,
                    "legal_reason": record["reason"],
                    "outcome": "illegal",
                    "observed": "",
                }
                if record["reason"] is None:
                    result = batch.get(test["idx"])
                    held = result.ok and equal(result.value, test["expect"])
                    shot["outcome"] = "held" if held else "breach"
                    shot["observed"] = clean_text(
                        describe(result), contract.MAX_OBSERVED_CHARS)
                verdicts[attacker].append(shot)

        par_fails = []
        for defender in range(cfg.num_seats):
            batch = batches[defender]
            failed = 0
            for i, par in enumerate(par_tests):
                result = batch.get(PAR_ID_BASE + i)
                if not (result.ok and equal(result.value, canon(par["expect"]))):
                    failed += 1
            par_fails.append(failed)
        return verdicts, par_fails, broken

    # -- observations --------------------------------------------------------

    def rules(self) -> dict:
        cfg = self._config
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

    def _spec_view(self, spec) -> dict:
        """What a seat sees of a spec. The reference, the par tests and the
        ambiguity note are NEVER in here."""
        return {
            "key": spec.KEY,
            "title": clean_text(spec.TITLE, 48),
            "prompt": clean_text(spec.PROMPT),
            "signature": spec.SIGNATURE,
            "examples": [{"args": ex["args"], "expect": ex["expect"]}
                         for ex in spec.EXAMPLES],
        }

    def _observation_message(self, hole: int, spec, slot: int,
                             retry: bool) -> dict:
        cfg = self._config
        other = 1 - slot
        scores = self.scores
        return {
            "type": contract.MSG_OBSERVATION,
            "hole": hole,
            "deadline_seconds": cfg.hole_deadline_seconds,
            "retry": retry,
            "observation": {
                "hole": hole,
                "holes": cfg.holes,
                "spec": self._spec_view(spec),
                "you": {"alias": contract.ALIASES[slot], "slot": slot,
                        "score": scores[slot]},
                "opponent": {"alias": contract.ALIASES[other], "slot": other,
                             "score": scores[other]},
                "history": list(self._history[slot]),
                "rules": self.rules(),
            },
        }

    def _history_push(self, hole, spec, submissions, verdicts, par_fails,
                      score) -> None:
        for slot in range(self._config.num_seats):
            other = 1 - slot
            entry = {
                "hole": hole,
                "spec_key": spec.KEY,
                "hole_score": score[slot],
                "your_tests": [
                    {"name": s["name"], "args": s["args"],
                     "expect": s["expect"], "legal": s["legal"],
                     "legal_reason": s["legal_reason"],
                     "outcome": s["outcome"]}
                    for s in verdicts[slot]],
                "their_tests": [
                    {"name": s["name"], "args": s["args"],
                     "expect": s["expect"], "why": s["why"],
                     "outcome": s["outcome"], "your_result": s["observed"]}
                    for s in verdicts[other]],
                "their_note": submissions[other]["note"],
                "your_par_fails": par_fails[slot],
                "their_par_fails": par_fails[other],
            }
            while len(compact(entry)) > MAX_HISTORY_CHARS and (
                    entry["your_tests"] or entry["their_tests"]):
                if len(entry["their_tests"]) >= len(entry["your_tests"]):
                    entry["their_tests"].pop()
                else:
                    entry["your_tests"].pop()
            self._history[slot].append(entry)
            del self._history[slot][:-HISTORY_HOLES]

    def _hole_record(self, hole, spec, submissions, causes, verdicts,
                     par_fails, broken, score, cumulative) -> dict:
        seats = []
        for slot in range(self._config.num_seats):
            sub = submissions[slot]
            seats.append({
                "slot": slot,
                "impl": sub["impl"],
                "impl_lines": len(sub["impl"].splitlines()),
                "broken": bool(broken[slot]),
                "broken_reason": clean_text(
                    broken[slot], contract.MAX_BROKEN_REASON_CHARS)
                if broken[slot] else None,
                "note": sub["note"],
                "fallback": causes[slot],
                "dropped_tests": sub["dropped_tests"],
                "tests": [
                    {"idx": s["idx"], "name": s["name"], "args": s["args"],
                     "expect": s["expect"], "why": s["why"],
                     "legal": s["legal"], "legal_reason": s["legal_reason"],
                     "outcome": s["outcome"], "observed": s["observed"]}
                    for s in verdicts[slot]],
                "par_fails": par_fails[slot],
                "par_total": self._config.par_tests_per_hole,
            })
        return {
            "hole": hole,
            "spec": {**self._spec_view(spec),
                     "ambiguity": clean_text(spec.AMBIGUITY, 140)},
            "seats": seats,
            "hole_score": list(score),
            "cumulative": list(cumulative),
        }
