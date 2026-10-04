"""Bind a hydrated manifest to the actual clean source used for its images."""

import argparse
import subprocess
from pathlib import Path

from coworld.types import CoworldManifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=root, check=True)
    untracked = subprocess.check_output(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "server",
            "players",
            "tools",
            "viewer",
            "client",
            "replay-viewer",
        ],
        cwd=root,
        text=True,
    )
    if untracked:
        raise ValueError("manifest source requires tracked image source inputs")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    manifest = CoworldManifest.model_validate_json(args.manifest.read_text())
    source = f"https://github.com/Metta-AI/cogame-cogolf/tree/{revision}"
    manifest.game.runnable.source_url = source
    for player in manifest.player:
        player.source_url = source + "/players"
    args.manifest.write_text(
        manifest.model_dump_json(indent=2, exclude_none=True) + "\n"
    )


if __name__ == "__main__":
    main()
