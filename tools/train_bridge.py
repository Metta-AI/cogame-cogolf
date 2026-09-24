"""JSONL decision bridge over Cogolf's hosted observations and hole engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from cogame_cogolf import contract  # noqa: E402
from cogame_cogolf.baseline import baseline  # noqa: E402
from cogame_cogolf.config import GameConfig  # noqa: E402
from cogame_cogolf.engine import Engine, validate_submission_message  # noqa: E402
from cogame_cogolf.sandbox import Sandbox  # noqa: E402


class ChosenSource:
    """Return the action selected for one seat at the current hole."""

    def __init__(self) -> None:
        self.wrong_hole_count = 0
        self.message: dict = {}

    async def wait_connected(self, timeout_seconds: float) -> bool:
        return True

    async def get_submission(self, hole: int, payload: dict,
                             deadline_at: float) -> tuple[dict, None]:
        assert self.message["hole"] == hole
        accepted, cause = validate_submission_message(self.message, hole)
        assert cause is None and accepted == self.message
        return self.message, None


class Bridge:
    def __init__(self, manifest: Path, variant: str) -> None:
        data = json.loads(manifest.read_text())
        variants = {entry["id"]: entry["game_config"] for entry in data["variants"]}
        self.variant = variant
        self.variant_config = variants[variant]
        self.sources = [ChosenSource(), ChosenSource()]
        self.engine: Engine
        self.views: list[dict]
        self.seat = 0
        self.decision_id = 0

    def reset(self, command: dict) -> dict:
        assert command["players"] == 2
        seed = int.from_bytes(hashlib.sha256(command["seed"].encode()).digest()[:8], "big")
        config = GameConfig.from_dict({
            **self.variant_config,
            "tokens": ["local-seat-0", "local-seat-1"],
            "seed": seed,
            "min_hole_spacing_seconds": 0,
        })
        self.sources = [ChosenSource(), ChosenSource()]
        self.engine = Engine(config, self.sources, Sandbox(
            call_cpu_seconds=config.call_cpu_seconds,
            batch_seconds=config.sandbox_batch_seconds), seed=seed)
        self.engine._start = time.monotonic()
        self.engine._wall_deadline = self.engine._start + config.wall_clock_budget_seconds
        self.seat = 0
        self.decision_id = 0
        self.capture_views()
        return self.decision()

    def capture_views(self) -> None:
        hole = self.engine.holes_played + 1
        spec = self.engine.deck[self.engine.spec_keys[hole - 1]]
        self.views = [self.engine._observation_message(hole, spec, seat, False)["observation"]
                      for seat in range(2)]

    def decision(self) -> dict:
        view = self.views[self.seat]
        return {
            "kind": "decision", "game": "cogolf", "decision_id": self.decision_id,
            "seat": self.seat, "engine_seat": self.seat, "turn": view["hole"],
            "semantic_view": view, "inbox": [],
            "messages": [{"role": "system", "content": "Play Cogolf by submitting an implementation and tests."},
                         {"role": "user", "content": json.dumps(view, ensure_ascii=False)}],
            "speech_messages": [],
            "action_schema": {"type": "object", "properties": {
                "choice": {"type": "integer", "minimum": 0, "maximum": 3}},
                "required": ["choice"]},
            "typed_question": None,
        }

    def encode(self) -> dict:
        view = self.views[self.seat]
        history = view["history"]
        keys = sorted(self.engine.deck)
        values = [float(view["spec"]["key"] == key) for key in keys]
        values.extend([float(self.seat == seat) for seat in range(2)])
        values.extend([float(self.variant == name) for name in ("duel", "blitz")])
        values.extend([
            view["hole"] / view["holes"],
            view["you"]["score"] / (9 * view["holes"]),
            view["opponent"]["score"] / (9 * view["holes"]),
        ])
        for entry in history[-4:]:
            values.extend([
                entry["hole_score"] / 9,
                sum(test["outcome"] == "breach" for test in entry["your_tests"]) / 5,
                sum(test["outcome"] == "breach" for test in entry["their_tests"]) / 5,
                entry["your_par_fails"] / 4,
            ])
        values.extend([0.0] * (4 - len(history[-4:])) * 4)
        return {"decision_id": self.decision_id, "values": values,
                "actions": [{"choice": choice} for choice in range(4)]}

    def teacher(self) -> dict:
        return {"response": json.dumps({"choice": 0 if self.seat == 0 else 3})}

    def step(self, command: dict) -> dict:
        if command["decision_id"] != self.decision_id:
            return {"kind": "rejected", "reason": "stale decision"}
        action = json.loads(command["response"])
        choice = action["choice"]
        assert choice in range(4)
        view = self.views[self.seat]
        hole = view["hole"]
        spec = self.engine.deck[view["spec"]["key"]]
        max_tests = view["rules"]["max_tests_per_hole"]
        implementation = baseline(("literalist", "pedant")[choice // 2], spec,
                                  hole, max_tests)
        tests = baseline(("literalist", "pedant")[choice % 2], spec,
                         hole, max_tests)
        self.sources[self.seat].message = {
            "type": contract.MSG_SUBMISSION, "hole": hole,
            "impl": implementation["impl"], "tests": tests["tests"],
            "note": f"{('literalist', 'pedant')[choice // 2]} implementation; "
                    f"{('literalist', 'pedant')[choice % 2]} tests",
        }
        self.decision_id += 1
        if self.seat == 0:
            self.seat = 1
            observation = self.decision()
        else:
            self.engine.current_hole = hole
            asyncio.run(self.engine._play_hole(hole))
            self.engine.holes_played = hole
            if hole == self.engine._config.holes:
                scores = self.engine.scores
                scale = hole * (max_tests + view["rules"]["par_tests_per_hole"])
                observation = {"kind": "terminal", "scores": dict(enumerate(scores)),
                               "utilities": {seat: score / scale
                                             for seat, score in enumerate(scores)}}
            else:
                self.capture_views()
                self.seat = 0
                observation = self.decision()
        return {"kind": "accepted", "action": action, "observation": observation}


def main() -> None:
    manifest, variant = sys.argv[1:]
    bridge = Bridge(Path(manifest), variant)
    for line in sys.stdin:
        command = json.loads(line)
        kind = command["kind"]
        if kind == "reset":
            response = bridge.reset(command)
        elif kind == "encode":
            response = bridge.encode()
        elif kind == "teacher":
            response = bridge.teacher()
        elif kind == "step":
            response = bridge.step(command)
        else:
            raise ValueError(f"unknown command {kind!r}")
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
