"""Check full Cogolf exports and game-seed validation splits."""

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
            [sys.executable, str(ROOT / "tools/export_posttrain.py"),
             str(output), "10", "--variant", variant],
            cwd=ROOT, env=ENV, check=True,
        )
        manifest = json.loads((output / "manifest.json").read_text())
        train = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
        validation = [json.loads(line) for line in (output / "validation.jsonl").read_text().splitlines()]
        assert manifest["variant"] == variant
        assert manifest["teacher"] == {"0": "literalist", "1": "pedant"}
        assert len(manifest["runs"]) == 10
        assert all(run["decisions"] == 2 * holes and sum(run["scores"]) == 0
                   for run in manifest["runs"])
        assert len(train) == manifest["train_examples"] == 8 * 2 * holes
        assert len(validation) == manifest["validation_examples"] == 2 * 2 * holes
        assert all(int(row["seed"].split("-")[-1]) % 5 != 0 for row in train)
        assert all(int(row["seed"].split("-")[-1]) % 5 == 0 for row in validation)
        for row in train + validation:
            assert "local-seat" not in row["prompt"][0]["content"]
            assert "local-seat" not in row["prompt"][1]["content"]
            answer = json.loads(row["completion"][0]["content"])
            assert set(answer) == {"impl", "tests", "note"}
            assert len(answer["tests"]) <= 5
            assert row["game"] == "cogolf"
        print(variant, len(train), len(validation))
