"""Replay document (docs/REPLAY.md, ``cogame-cogolf-replay`` v1).

One UTF-8 JSON document, held in memory and written once at the end (plus
a best-effort partial write on a harness fault). It is the viewer's ONLY
input: names, aliases, the resolved config, the seed, the deck version,
every hole's spec text and both seats' submissions and verdicts, the whole
beat stream and the result document are all inside it, so the viewer
fetches nothing but the ``.replay`` URL.

Every string here has already been through ``engine.clean_text``: lone
surrogates are ``U+FFFD``, control characters other than newline and tab
are gone, and every truncation happened on a RUNE boundary — so the bytes
always parse under a strict UTF-8 JSON reader.
"""

from __future__ import annotations

import json

from . import contract
from .config import GameConfig
from .specs import DECK_VERSION
from .version import GAME_VERSION

FORMAT = "cogame-cogolf-replay"
VERSION = 1

HOLE_KEYS = ("hole", "spec", "seats", "hole_score", "cumulative")
SEAT_KEYS = ("slot", "impl", "impl_lines", "broken", "note", "fallback",
             "tests", "par_fails", "par_total")
EVENT_KINDS = frozenset(contract.EVENT_KINDS)


class ReplayError(ValueError):
    """Corrupt or unsupported replay document."""


class ReplayWriter:
    def __init__(self, config: GameConfig, seed: int,
                 deck_version: str = DECK_VERSION):
        self.config = config
        self.seed = int(seed)
        self.deck_version = deck_version
        self._holes: list[dict] = []
        self._events: list[dict] = []

    @property
    def hole_count(self) -> int:
        return len(self._holes)

    @property
    def beat_count(self) -> int:
        return len(self._events)

    def append_hole(self, record: dict) -> None:
        missing = [k for k in HOLE_KEYS if k not in record]
        if missing:
            raise ValueError(f"hole record missing keys {missing}")
        self._holes.append(record)

    def append_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown replay event kind {kind!r}")
        self._events.append(event)

    def document(self, results_doc: dict) -> dict:
        cfg = self.config
        return {
            "format": FORMAT,
            "version": VERSION,
            "game_version": GAME_VERSION,
            "protocol": contract.PROTOCOL,
            "config": cfg.to_dict(),
            "seed": self.seed,
            "deck": cfg.deck,
            "deck_version": self.deck_version,
            "names": [p.name for p in cfg.players],
            "aliases": list(contract.ALIASES[:cfg.num_seats]),
            "holes": self._holes,
            "events": self._events,
            "result": results_doc,
        }

    def finalize(self, results_doc: dict) -> bytes:
        return json.dumps(self.document(results_doc), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")


class Replay:
    """Parsed replay document with structural validation."""

    def __init__(self, doc: dict):
        self.doc = doc

    @classmethod
    def parse(cls, data: bytes) -> "Replay":
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReplayError(f"replay is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("format") != FORMAT:
            raise ReplayError("bad replay format magic")
        if doc.get("version") != VERSION:
            raise ReplayError(
                f"unsupported replay version {doc.get('version')!r}")
        for key in ("game_version", "config", "names", "aliases", "seed",
                    "deck_version", "holes", "events", "result"):
            if key not in doc:
                raise ReplayError(f"replay missing {key!r}")
        if not isinstance(doc["holes"], list):
            raise ReplayError("replay holes must be a list")
        if not isinstance(doc["events"], list):
            raise ReplayError("replay events must be a list")
        for hole in doc["holes"]:
            missing = [k for k in HOLE_KEYS if k not in hole]
            if missing:
                raise ReplayError(f"replay hole missing {missing}")
            for seat in hole["seats"]:
                seat_missing = [k for k in SEAT_KEYS if k not in seat]
                if seat_missing:
                    raise ReplayError(f"replay seat missing {seat_missing}")
        for event in doc["events"]:
            if event.get("kind") not in EVENT_KINDS:
                raise ReplayError(
                    f"unknown replay event kind {event.get('kind')!r}")
        return cls(doc)

    @property
    def names(self) -> list[str]:
        return list(self.doc["names"])

    @property
    def aliases(self) -> list[str]:
        return list(self.doc["aliases"])

    @property
    def result(self) -> dict:
        return self.doc["result"]

    @property
    def holes(self) -> list[dict]:
        return self.doc["holes"]

    @property
    def events(self) -> list[dict]:
        return self.doc["events"]
