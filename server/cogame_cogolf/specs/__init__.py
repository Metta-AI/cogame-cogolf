"""The cogolf spec deck: one module per spec, keyed by module name.

A deck is a mapping ``KEY -> module``. Every module declares exactly the
attributes ``tests/test_specs.py`` asserts:

    KEY          str, stable id, equal to the module name
    TITLE        str, <= 48 chars, the scroll headline
    PROMPT       str, <= 1200 chars, shown VERBATIM to both seats
    SIGNATURE    {"function": "solve", "params": [...], "returns": "..."}
    EXAMPLES     2 x {"args": [...], "expect": ...} (reference-consistent)
    reference    the hidden oracle; the only authority on the ambiguous clause
    PAR_TESTS    4 x {"name", "args", "expect"} hidden audit cases
    SAFE_TESTS   5 reference-consistent shots (the `literalist` baseline)
    EDGE_TESTS   5 aggressive shots (the `pedant` baseline; some illegal)
    LITERAL_IMPL str, the source `literalist` submits
    NAIVE_IMPL   str, the source `pedant` submits
    AMBIGUITY    str, <= 140 chars, spectator note — REPLAY ONLY, never sent
                 to a seat

`solve` takes and returns JSON values only, which is what makes a test a
data record instead of an expression.
"""

from __future__ import annotations

from types import ModuleType

from . import (chunk, dedupe, longest_run, median, path_norm, range_merge,
               roman, round_to, score_grade, title_case, top_k, word_count)

DECK_VERSION = "core-1"

_CORE = (longest_run, median, title_case, roman, chunk, dedupe, word_count,
         round_to, range_merge, top_k, path_norm, score_grade)

DECKS: dict[str, dict[str, ModuleType]] = {
    "core": {module.KEY: module for module in _CORE},
}


class DeckError(ValueError):
    """Unknown deck name."""


def load_deck(name: str) -> dict[str, ModuleType]:
    """The named deck, or raise ``DeckError`` (a config error, exit 2)."""
    try:
        return DECKS[name]
    except KeyError:
        raise DeckError(
            f"unknown deck {name!r}; known decks: {sorted(DECKS)}") from None


def deck_keys(name: str) -> list[str]:
    """Sorted keys of the named deck (the draw is over this order)."""
    return sorted(load_deck(name))


__all__ = ["DECKS", "DECK_VERSION", "DeckError", "deck_keys", "load_deck"]
