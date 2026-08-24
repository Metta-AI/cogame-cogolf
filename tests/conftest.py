"""Shared fixtures for the offline suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cogame_cogolf.config import GameConfig  # noqa: E402


def make_config(**overrides) -> GameConfig:
    """A two-seat config with short, test-sized deadlines."""
    data = {
        "players": [{"name": "bot-0"}, {"name": "bot-1"}],
        "tokens": ["token-0", "token-1"],
        "num_agents": 2,
        "deck": "core",
        "holes": 2,
        "seed": 11,
        "hole_deadline_seconds": 2.0,
        "retry_deadline_seconds": 1.0,
        "max_tests_per_hole": 5,
        "sandbox_batch_seconds": 6.0,
        "player_connect_timeout_seconds": 2.0,
        "min_hole_spacing_seconds": 0.0,
        "wall_clock_budget_seconds": 120.0,
    }
    data.update(overrides)
    return GameConfig.from_dict(data)


@pytest.fixture
def config() -> GameConfig:
    return make_config()
