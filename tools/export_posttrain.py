"""Export complete Cogolf matches with the published scripted teachers."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from cogame_cogolf import contract
from cogame_cogolf.config import GameConfig
from cogame_cogolf.engine import Engine, validate_submission_message
from cogame_cogolf.sandbox import Sandbox
from players.llm_player import LLMPolicy
from players.scripted import scripted_submission
from cogame_cogolf.server import API_DOCS


class RecordingSource:
    """Use the shipped player policy and retain its model-facing messages."""

    def __init__(self, name: str, variant: str, seed: int, slot: int, rows: list[dict]):
        self.name = name
        self.variant = variant
        self.seed = seed
        self.slot = slot
        self.rows = rows
        self.wrong_hole_count = 0
        self.policy = LLMPolicy(provider="none", strategy="")
        self.policy.on_welcome({"alias": contract.ALIASES[slot], "api_docs": API_DOCS})

    async def wait_connected(self, timeout_seconds: float) -> bool:
        return True

    async def get_submission(self, hole: int, payload: dict,
                             deadline_at: float) -> tuple[dict, None]:
        observation = payload["observation"]
        completion = scripted_submission(self.name, hole, observation)
        message = {"type": contract.MSG_SUBMISSION, "hole": hole, **completion}
        accepted, cause = validate_submission_message(message, hole)
        assert cause is None and accepted == message
        system = "\n\n".join(block["text"] for block in self.policy._system_blocks())
        self.rows.append({
            "episode_id": f"cogolf-{self.variant}-{self.seed}-{self.slot}",
            "seed": f"cogolf-{self.variant}-{self.seed}",
            "decision_id": (hole - 1) * 2 + self.slot,
            "prompt": [
                {"role": "system", "content": system},
                {"role": "user", "content": self.policy.user_prompt(hole, observation)},
            ],
            "completion": [{"role": "assistant", "content": json.dumps(completion, ensure_ascii=False)}],
            "game": "cogolf",
            "action_schema_revision": "cogolf-submission-v1",
        })
        return message, None


async def export_game(config: GameConfig, variant: str, seed: int) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    sources = [RecordingSource("literalist", variant, seed, 0, rows),
               RecordingSource("pedant", variant, seed, 1, rows)]
    engine = Engine(config, sources, Sandbox(
        call_cpu_seconds=config.call_cpu_seconds,
        batch_seconds=config.sandbox_batch_seconds), seed=seed)
    result = await engine.run()
    assert result.reason == "complete" and result.holes_played == config.holes
    assert len(rows) == config.holes * 2
    scores = [seat.score for seat in result.seats]
    assert sum(scores) == 0
    return rows, {"seed": seed, "decisions": len(rows), "scores": scores}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("episodes", type=int)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--variant", choices=("duel", "blitz"), default="duel")
    args = parser.parse_args()
    assert args.episodes >= 10 and args.first_seed > 0
    args.output.mkdir(parents=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    manifest = json.loads(Path("coworld_manifest_template.json").read_text())
    variant_config = next(v["game_config"] for v in manifest["variants"]
                          if v["id"] == args.variant)
    train: list[dict] = []
    validation: list[dict] = []
    runs: list[dict] = []
    for seed in range(args.first_seed, args.first_seed + args.episodes):
        local_config = {**variant_config, "tokens": ["local-seat-0", "local-seat-1"],
                        "seed": seed, "min_hole_spacing_seconds": 0}
        config = GameConfig.from_dict(local_config)
        rows, run = asyncio.run(export_game(config, args.variant, seed))
        (validation if seed % 5 == 0 else train).extend(rows)
        runs.append(run)
    for name, rows in (("train", train), ("validation", validation)):
        (args.output / f"{name}.jsonl").write_text("".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    (args.output / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "game": "cogolf", "variant": args.variant,
        "source_revision": revision,
        "teacher": {"0": "literalist", "1": "pedant"},
        "train_examples": len(train), "validation_examples": len(validation),
        "runs": runs,
    }, indent=2) + "\n")
    print(f"train={len(train)} validation={len(validation)}")


if __name__ == "__main__":
    main()
