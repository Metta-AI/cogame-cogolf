"""Game config model: the manifest ``config_schema`` as a dataclass.

The config JSON arrives via ``COGAME_CONFIG_URI``. ``players`` and
``tokens`` are parallel arrays in seat-slot order; every other key has
the default the manifest schema declares. The schema is closed
(``additionalProperties: false``): unknown keys are rejected here too, so
a typo in a variant fails at startup (exit 2) instead of silently
playing defaults.

Cogolf is a two-seat game and only a two-seat game: ``num_agents`` is
pinned to exactly 2 in the schema, in every variant and in the
certification fixture, and any other seat count is a config error.
"""

from __future__ import annotations

import json
import math
import secrets
from dataclasses import dataclass
from pathlib import Path

SEATS = 2

DEFAULT_DECK = "core"
DEFAULT_HOLES = 9
MAX_HOLES = 12
DEFAULT_SEED = 0
DEFAULT_HOLE_DEADLINE_SECONDS = 40.0
DEFAULT_RETRY_DEADLINE_SECONDS = 15.0
DEFAULT_MAX_TESTS_PER_HOLE = 5
MAX_MAX_TESTS_PER_HOLE = 5
DEFAULT_PAR_TESTS_PER_HOLE = 4
DEFAULT_CALL_CPU_SECONDS = 1.0
DEFAULT_SANDBOX_BATCH_SECONDS = 6.0
DEFAULT_HOLE_RESERVE_SECONDS = 80.0
DEFAULT_MIN_HOLE_SPACING_SECONDS = 4.0
DEFAULT_PLAYER_CONNECT_TIMEOUT_SECONDS = 90.0
# The platform's episodeTimeoutSeconds is 1200 (episode_timeout_minutes 20);
# the play budget is 60 % of it. 700 s leaves room for artifact writes.
DEFAULT_WALL_CLOCK_BUDGET_SECONDS = 700.0

KNOWN_KEYS = frozenset({
    "tokens", "players", "num_agents", "deck", "holes", "seed",
    "hole_deadline_seconds", "retry_deadline_seconds", "max_tests_per_hole",
    "par_tests_per_hole", "call_cpu_seconds", "sandbox_batch_seconds",
    "hole_reserve_seconds", "min_hole_spacing_seconds",
    "player_connect_timeout_seconds", "wall_clock_budget_seconds",
})


class ConfigError(ValueError):
    """Invalid or inconsistent game config."""


@dataclass(frozen=True)
class PlayerConfig:
    name: str


@dataclass(frozen=True)
class GameConfig:
    players: tuple[PlayerConfig, ...]
    tokens: tuple[str, ...]
    deck: str
    holes: int
    seed: int
    hole_deadline_seconds: float
    retry_deadline_seconds: float
    max_tests_per_hole: int
    par_tests_per_hole: int
    call_cpu_seconds: float
    sandbox_batch_seconds: float
    hole_reserve_seconds: float
    min_hole_spacing_seconds: float
    player_connect_timeout_seconds: float
    wall_clock_budget_seconds: float

    @property
    def num_seats(self) -> int:
        return len(self.players)

    @classmethod
    def from_dict(cls, data) -> "GameConfig":
        if not isinstance(data, dict):
            raise ConfigError(
                f"config must be a JSON object, got {type(data).__name__}")
        unknown = sorted(set(data) - KNOWN_KEYS)
        if unknown:
            raise ConfigError(f"unknown config keys: {unknown}")

        players_raw = data.get("players")
        if not isinstance(players_raw, list) or not players_raw:
            raise ConfigError("config requires a non-empty 'players' array")
        if len(players_raw) != SEATS:
            raise ConfigError(
                f"cogolf is a {SEATS}-seat game; got {len(players_raw)} players")
        players = []
        for i, entry in enumerate(players_raw):
            if not isinstance(entry, dict) or set(entry) != {"name"} \
                    or not isinstance(entry["name"], str) or not entry["name"]:
                raise ConfigError(
                    f"players[{i}] must be an object with exactly a "
                    f"non-empty 'name'")
            players.append(PlayerConfig(name=entry["name"]))

        tokens_raw = data.get("tokens")
        if not isinstance(tokens_raw, list) or \
                not all(isinstance(t, str) and t for t in tokens_raw):
            raise ConfigError(
                "config requires a 'tokens' array of non-empty strings")
        if len(tokens_raw) != len(players):
            raise ConfigError(
                f"tokens length {len(tokens_raw)} != players length "
                f"{len(players)}")

        if "num_agents" in data:
            num_agents = _int_field(data, "num_agents", SEATS)
            if num_agents != SEATS:
                raise ConfigError(
                    f"num_agents must be {SEATS} (cogolf is two-seat), got "
                    f"{num_agents}")

        deck = data.get("deck", DEFAULT_DECK)
        if not isinstance(deck, str) or not deck:
            raise ConfigError(f"deck must be a non-empty string, got {deck!r}")

        holes = _int_field(data, "holes", DEFAULT_HOLES)
        if not 1 <= holes <= MAX_HOLES:
            raise ConfigError(
                f"holes must be in [1, {MAX_HOLES}], got {holes}")

        seed = _int_field(data, "seed", DEFAULT_SEED)
        if seed < 0:
            raise ConfigError(f"seed must be >= 0, got {seed}")

        hole_deadline = _number_field(
            data, "hole_deadline_seconds", DEFAULT_HOLE_DEADLINE_SECONDS,
            positive=True)
        retry_deadline = _number_field(
            data, "retry_deadline_seconds", DEFAULT_RETRY_DEADLINE_SECONDS,
            positive=True)

        max_tests = _int_field(data, "max_tests_per_hole",
                               DEFAULT_MAX_TESTS_PER_HOLE)
        if not 1 <= max_tests <= MAX_MAX_TESTS_PER_HOLE:
            raise ConfigError(
                f"max_tests_per_hole must be in [1, {MAX_MAX_TESTS_PER_HOLE}], "
                f"got {max_tests}")
        par_tests = _int_field(data, "par_tests_per_hole",
                               DEFAULT_PAR_TESTS_PER_HOLE)
        if par_tests != DEFAULT_PAR_TESTS_PER_HOLE:
            raise ConfigError(
                f"par_tests_per_hole is fixed by the deck at "
                f"{DEFAULT_PAR_TESTS_PER_HOLE}, got {par_tests}")

        call_cpu = _number_field(data, "call_cpu_seconds",
                                 DEFAULT_CALL_CPU_SECONDS, positive=True)
        batch = _number_field(data, "sandbox_batch_seconds",
                              DEFAULT_SANDBOX_BATCH_SECONDS, positive=True)
        reserve = _number_field(data, "hole_reserve_seconds",
                                DEFAULT_HOLE_RESERVE_SECONDS, positive=False)
        spacing = _number_field(data, "min_hole_spacing_seconds",
                                DEFAULT_MIN_HOLE_SPACING_SECONDS,
                                positive=False)
        connect_timeout = _number_field(
            data, "player_connect_timeout_seconds",
            DEFAULT_PLAYER_CONNECT_TIMEOUT_SECONDS, positive=False)
        budget = _number_field(
            data, "wall_clock_budget_seconds",
            DEFAULT_WALL_CLOCK_BUDGET_SECONDS, positive=True)

        return cls(
            players=tuple(players),
            tokens=tuple(tokens_raw),
            deck=deck,
            holes=holes,
            seed=seed,
            hole_deadline_seconds=hole_deadline,
            retry_deadline_seconds=retry_deadline,
            max_tests_per_hole=max_tests,
            par_tests_per_hole=par_tests,
            call_cpu_seconds=call_cpu,
            sandbox_batch_seconds=batch,
            hole_reserve_seconds=reserve,
            min_hole_spacing_seconds=spacing,
            player_connect_timeout_seconds=connect_timeout,
            wall_clock_budget_seconds=budget,
        )

    @classmethod
    def from_file_uri(cls, uri: str) -> "GameConfig":
        """Parse a config from a local ``file://`` URI or plain path."""
        path = uri.removeprefix("file://")
        try:
            raw = Path(path).read_text()
        except OSError as exc:
            raise ConfigError(f"cannot read config from {uri}: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"config at {uri} is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    def resolve_seed(self) -> int:
        """The episode seed: ``seed`` when positive, else a fresh one.

        The resolved value is recorded in the results doc and the replay,
        so an episode is reproducible from its own bytes.
        """
        return self.seed if self.seed > 0 else secrets.randbits(32)

    def to_dict(self) -> dict:
        """Fully-resolved config for the replay document.

        Tokens are deliberately excluded: replays are public artifacts,
        tokens are per-episode player credentials.
        """
        return {
            "players": [{"name": p.name} for p in self.players],
            "num_agents": self.num_seats,
            "deck": self.deck,
            "holes": self.holes,
            "seed": self.seed,
            "hole_deadline_seconds": self.hole_deadline_seconds,
            "retry_deadline_seconds": self.retry_deadline_seconds,
            "max_tests_per_hole": self.max_tests_per_hole,
            "par_tests_per_hole": self.par_tests_per_hole,
            "call_cpu_seconds": self.call_cpu_seconds,
            "sandbox_batch_seconds": self.sandbox_batch_seconds,
            "hole_reserve_seconds": self.hole_reserve_seconds,
            "min_hole_spacing_seconds": self.min_hole_spacing_seconds,
            "player_connect_timeout_seconds":
                self.player_connect_timeout_seconds,
            "wall_clock_budget_seconds": self.wall_clock_budget_seconds,
        }


def _int_field(data: dict, key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer, got {value!r}")
    return value


def _number_field(data: dict, key: str, default: float, *,
                  positive: bool) -> float:
    value = data.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(value):
        raise ConfigError(f"{key} must be a finite number, got {value!r}")
    if positive and value <= 0:
        raise ConfigError(f"{key} must be positive, got {value!r}")
    if not positive and value < 0:
        raise ConfigError(f"{key} must be non-negative, got {value!r}")
    return float(value)
