"""Run complete Cogolf variants through the numeric decision bridge."""

import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def play(variant: str, teacher: bool) -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tools/train_bridge.py"),
            str(ROOT / "coworld_manifest_template.json"),
            variant,
            "--mode",
            "numeric",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd="/tmp",
    )
    assert process.stdin is not None and process.stdout is not None
    stdin, stdout = process.stdin, process.stdout
    rng = random.Random(13)

    def request(payload: dict) -> dict:
        stdin.write(json.dumps(payload) + "\n")
        stdin.flush()
        return json.loads(stdout.readline())

    try:
        observation = request(
            {"kind": "reset", "seed": f"cogolf-{variant}-{teacher}", "players": 2}
        )
        decisions = 0
        widths = set()
        while observation["kind"] == "decision":
            assert observation["game"] == "cogolf"
            assert observation["decision_id"] == decisions
            view = observation["semantic_view"]
            assert observation["seat"] == view["you"]["slot"]
            assert json.loads(observation["messages"][1]["content"]) == view
            assert "local-seat" not in json.dumps(view)
            encoding = request({"kind": "encode"})
            assert encoding["decision_id"] == decisions
            widths.add(len(encoding["values"]))
            assert encoding["actions"] == [{"choice": i} for i in range(4)]
            if teacher:
                choice = json.loads(request({"kind": "teacher"})["response"])
            else:
                choice = rng.choice(encoding["actions"])
            result = request(
                {
                    "kind": "step",
                    "decision_id": decisions,
                    "response": json.dumps(choice),
                }
            )
            assert result["kind"] == "accepted" and result["action"] == choice
            observation = result["observation"]
            decisions += 1
            assert decisions <= (18 if variant == "duel" else 10)
        assert observation["kind"] == "terminal"
        assert set(observation["scores"]) == {"0", "1"}
        assert sum(observation["scores"].values()) == 0
        assert sum(observation["utilities"].values()) == 0
        assert widths == {35}
        assert decisions == (18 if variant == "duel" else 10)
        print(
            variant,
            "teacher" if teacher else "random",
            decisions,
            observation["scores"],
        )
    finally:
        process.stdin.close()
        process.stdout.close()
        assert process.wait(timeout=5) == 0


def play_language(variant: str) -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tools/train_bridge.py"),
            str(ROOT / "coworld_manifest_template.json"),
            variant,
            "--mode",
            "language",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd="/tmp",
    )
    assert process.stdin is not None and process.stdout is not None
    stdin, stdout = process.stdin, process.stdout

    def request(payload: dict) -> dict:
        stdin.write(json.dumps(payload) + "\n")
        stdin.flush()
        return json.loads(stdout.readline())

    try:
        observation = request(
            {"kind": "reset", "seed": f"language-{variant}", "players": 2}
        )
        decisions = 0
        while observation["kind"] == "decision":
            assert observation["inference_mode"] == "text_action"
            assert observation["control_profile"] == "language"
            assert "YOUR STRATEGY" not in observation["messages"][0]["content"]
            assert "REPLY FORMAT" in observation["messages"][0]["content"]
            assert (
                "SPEC " + observation["semantic_view"]["spec"]["key"]
                in observation["messages"][1]["content"]
            )
            response = request({"kind": "teacher"})["response"]
            teacher = json.loads(response)
            result = request(
                {"kind": "step", "decision_id": decisions, "response": response}
            )
            assert result["kind"] == "accepted"
            assert result["action"]["impl"] == teacher["impl"]
            assert result["action"]["note"] == teacher["note"]
            assert [t["args"] for t in result["action"]["tests"]] == [
                t["args"] for t in teacher["tests"]
            ]
            observation = result["observation"]
            decisions += 1
        assert decisions == (18 if variant == "duel" else 10)
        assert sum(observation["scores"].values()) == 0
        print(variant, "ordinary language teacher", decisions, observation["scores"])
    finally:
        process.stdin.close()
        process.stdout.close()
        assert process.wait(timeout=5) == 0


if __name__ == "__main__":
    for name in ("duel", "blitz"):
        play_language(name)
        for use_teacher in (True, False):
            play(name, use_teacher)
