"""Audit unreviewed complete Cogolf collections; never publish training labels."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": f"{ROOT / 'server'}:{ROOT}"}


with tempfile.TemporaryDirectory() as directory:
    for variant, holes in (("duel", 9), ("blitz", 5)):
        output = Path(directory) / variant
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/export_posttrain.py"),
                str(output),
                "10",
                "--game-version",
                "0.2.0",
                "--variant",
                variant,
            ],
            cwd=ROOT,
            env=ENV,
            check=True,
        )
        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["variant"] == variant
        assert manifest["qualification"] == "unreviewed_complete_episodes"
        assert manifest["teacher"] == {"0": "literalist", "1": "pedant"}
        assert len(manifest["runs"]) == 10
        assert not (output / "train.jsonl").exists()
        assert not (output / "validation.jsonl").exists()
        for run in manifest["runs"]:
            path = output / run["file"]
            complete = json.loads(path.read_text())
            assert run["decisions"] == len(complete["decisions"]) == holes * 2
            assert sum(run["scores"]) == 0
            assert complete["episode"]["status"] == "completed"
            assert complete["episode"]["seed_family"] == f"cogolf:{run['seed']}"
            assert path.stat().st_mode & 0o777 == 0o600
            for decision in complete["decisions"]:
                selected = next(
                    a
                    for a in decision["attempts"]
                    if a["attempt_id"] == decision["selected_attempt_id"]
                )
                assert selected["origin"] == "teacher" and selected["accepted"]
                assert selected["parsed_action"] == decision["executed_action"]
                assert selected["request"] is None and selected["model"] is None
                assert "local-seat" not in json.dumps(decision["prompt"])
        print(variant, len(manifest["runs"]), "unreviewed complete games")
