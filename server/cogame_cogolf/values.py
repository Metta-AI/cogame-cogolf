"""JSON-value canonicalisation and the ONE equality rule of the game.

Every value that crosses the sandbox boundary — a test's ``expect``, a
call's return value, a reference answer — is canonicalised here and
compared here. The rule, stated once:

* representable values are ``None``, ``bool``, ``int``, ``float``, ``str``,
  ``list`` and ``dict`` with string keys; a tuple canonicalises to a list;
* ``NaN`` and ``Infinity`` are NOT representable (they have no JSON form);
* numbers compare by value, so ``1 == 1.0``;
* ``True`` is NEVER equal to ``1``: bools are type-tagged;
* dict key order is irrelevant;
* strings compare by exact code points.

Anything outside that set makes the call a breach for the defender
(``bad_value``). Stdlib only: the sandbox child imports this module.
"""

from __future__ import annotations

import math

MAX_DEPTH = 12


class BadValue(ValueError):
    """A value with no JSON representation (NaN, a set, a class, ...)."""


def canon(value, _depth: int = 0):
    """The canonical JSON form of ``value``; raises :class:`BadValue`."""
    if _depth > MAX_DEPTH:
        raise BadValue("value nested too deeply")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BadValue(f"{value!r} has no JSON representation")
        return value
    if isinstance(value, (list, tuple)):
        return [canon(item, _depth + 1) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BadValue(
                    f"object keys must be strings, got {type(key).__name__}")
            out[key] = canon(item, _depth + 1)
        return out
    raise BadValue(f"{type(value).__name__} is not a JSON value")


def equal(a, b) -> bool:
    """Type-tagged deep equality over canonical values."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return False


def fingerprint(value) -> str:
    """A stable, type-tagged string key (duplicate-test detection)."""
    value = canon(value)

    def render(node) -> str:
        if node is None:
            return "n"
        if isinstance(node, bool):
            return "b:1" if node else "b:0"
        if isinstance(node, (int, float)):
            as_float = float(node)
            if as_float.is_integer():
                return f"m:{int(as_float)}"
            return f"m:{as_float!r}"
        if isinstance(node, str):
            return "s:" + node
        if isinstance(node, list):
            return "[" + ",".join(render(item) for item in node) + "]"
        return "{" + ",".join(
            f"{key}={render(node[key])}" for key in sorted(node)) + "}"

    return render(value)
