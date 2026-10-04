"""Collect private complete ordinary games; training labels require external review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from cogame_cogolf import lifecycle
from cogame_cogolf.config import GameConfig
from cogame_cogolf.engine import Engine
from cogame_cogolf.journal import Journal, SourceIdentity
from cogame_cogolf.private_window import Admission, ObservationPacket
from cogame_cogolf.results import results_doc
from cogame_cogolf.sandbox import Sandbox
from cogame_cogolf.submission import (
    normalize_submission,
    parse_reply,
    sanitize_submission,
    validate_submission_message,
)

from players.scripted import scripted_submission


class RecordingSource:
    """The shipped teacher reads only the ordinary private decision window."""

    def __init__(self, name: str, slot: int, journal: Journal):
        self.name = name
        self.slot = slot
        self.journal = journal
        self.wrong_hole_count = 0

    async def wait_connected(self, timeout_seconds: float) -> bool:
        return True

    async def get_submission(
        self, hole: int, payload: dict, deadline_at: float
    ) -> tuple[dict, None]:
        window = ObservationPacket.model_validate(payload)
        response = json.dumps(
            scripted_submission(self.name, hole, window.observation.model_dump()),
            ensure_ascii=False,
        )
        parsed = normalize_submission(
            parse_reply(response),
            hole,
            max_tests=window.observation.rules.max_tests_per_hole,
        )
        accepted, cause = validate_submission_message(parsed, hole)
        if cause is not None:
            raise ValueError("ordinary scripted teacher response fails live parser")
        assert accepted is not None
        installed = sanitize_submission(
            accepted, hole, window.observation.rules.max_tests_per_hole
        )
        self.journal.teacher(window, self.name, response, installed)
        return accepted, None


async def export_game(
    config: GameConfig,
    variant: str,
    seed: int,
    path: Path,
    revision: str,
    game_version: str,
) -> dict:
    admissions = [Admission(slot) for slot in range(2)]
    journal = Journal(
        path,
        SourceIdentity(
            episode_id=f"cogolf:{variant}:{seed}",
            source_revision=revision,
            game_version=game_version,
        ),
        seed,
        admissions,
        ["literalist", "pedant"],
        config,
    )
    sources = [
        RecordingSource(name, slot, journal)
        for slot, name in enumerate(("literalist", "pedant"))
    ]
    engine = Engine(
        config,
        sources,
        Sandbox(
            call_cpu_seconds=config.call_cpu_seconds,
            batch_seconds=config.sandbox_batch_seconds,
        ),
        seed=seed,
        on_window=journal.window,
        on_applied=journal.applied,
    )
    result = await engine.run()
    if result.reason != "complete" or result.holes_played != config.holes:
        raise ValueError("teacher collection did not complete the ordinary game")
    complete = journal.finish(results_doc(config, result), owners_joined=True)
    return {
        "seed": seed,
        "variant": variant,
        "decisions": len(complete.decisions),
        "scores": [seat.score for seat in result.seats],
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("episodes", type=int)
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--variant", choices=("duel", "blitz"), default="duel")
    args = parser.parse_args()
    if args.episodes < 10 or args.first_seed < 1:
        raise ValueError(
            "whole-family teacher collection requires at least ten positive seeds"
        )
    args.output.mkdir(parents=True, mode=0o700)
    os.chmod(args.output, 0o700)
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=root, check=True)
    if subprocess.check_output(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "server",
            "players",
            "tools",
        ],
        cwd=root,
        text=True,
    ):
        raise ValueError("teacher source inputs must be committed before collection")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    manifest = json.loads((root / "coworld_manifest_template.json").read_text())
    variant_config = next(
        v["game_config"] for v in manifest["variants"] if v["id"] == args.variant
    )
    runs = []
    for seed in range(args.first_seed, args.first_seed + args.episodes):
        config = GameConfig.from_dict(
            {**variant_config, "tokens": ["local-seat-0", "local-seat-1"], "seed": seed}
        )
        run = lifecycle.main_owned(
            export_game(
                config,
                args.variant,
                seed,
                args.output / f"{args.variant}-{seed}.jsonl",
                revision,
                args.game_version,
            )
        )
        if run is None:
            raise SystemExit(
                143
            )  # Signal-stopped collection retains its private prefix.
        runs.append(run)
    path = args.output / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game": "cogolf",
                "variant": args.variant,
                "source_revision": revision,
                "game_version": args.game_version,
                "teacher": {"0": "literalist", "1": "pedant"},
                "qualification": "unreviewed_complete_episodes",
                "runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    os.chmod(path, 0o600)
    print(
        f"complete_episodes={len(runs)}; external content-bound review required before labels"
    )


if __name__ == "__main__":
    main()
