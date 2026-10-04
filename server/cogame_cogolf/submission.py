"""One source-owned response parser and submission normalization pipeline.

The hosted player, authoritative engine and training bridge use these functions.
Lenient response grammar, wire size limits and installed sanitation stay separate.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata

from . import contract
from .contract import (
    MAX_IMPL_CHARS,
    MAX_MESSAGE_BYTES,
    MAX_NOTE_CHARS,
    MAX_TEST_NAME_CHARS,
    MAX_TESTS_PER_HOLE,
    MAX_WHY_CHARS,
    MSG_SUBMISSION,
)

_PY_FENCE_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)\s*\n(.*?)```", re.DOTALL)


def _clip(value, limit: int) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def clean_text(value, limit: int | None = None) -> str:
    """Sanitise ``value`` for the wire and the replay."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = value.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
    value = "".join(
        ch for ch in value if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )
    if limit is not None and len(value) > limit:
        value = value[: limit - 1] + "\u2026"
    return value


def compact(value) -> str:
    """Compact JSON for a value, for the cap checks and the replay."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def validate_submission_message(data, hole: int) -> tuple[dict | None, str | None]:
    """Classify a decoded client message for the pending ``hole``.

    ``(payload, None)`` when valid, ``(None, "wrong_hole")`` when it
    addresses another hole (dropped; the hole keeps waiting), else
    ``(None, "malformed" | "oversize")``.
    """
    if not isinstance(data, dict) or data.get("type") != contract.MSG_SUBMISSION:
        return None, "malformed"
    got = data.get("hole")
    if isinstance(got, bool) or not isinstance(got, int):
        return None, "malformed"
    if got != hole:
        return None, "wrong_hole"
    impl = data.get("impl")
    if not isinstance(impl, str) or not impl.strip():
        return None, "malformed"
    if len(impl) > contract.MAX_IMPL_CHARS:
        return None, "oversize"
    tests = data.get("tests", [])
    if tests is None:
        tests = []
    if not isinstance(tests, list):
        return None, "malformed"
    note = data.get("note", "")
    if note is not None and not isinstance(note, str):
        return None, "malformed"
    return data, None


def sanitize_submission(data: dict, hole: int, max_tests: int) -> dict:
    """Normalise a validated submission: caps, truncation, drops."""
    raw_tests = [t for t in (data.get("tests") or []) if isinstance(t, dict)]
    dropped = max(0, len(raw_tests) - max_tests)
    tests = []
    for idx, entry in enumerate(raw_tests[:max_tests]):
        tests.append(
            {
                "idx": idx,
                "name": clean_text(
                    entry.get("name") or f"test {idx + 1}", contract.MAX_TEST_NAME_CHARS
                ),
                "args": entry.get("args"),
                "expect": entry.get("expect"),
                "why": clean_text(entry.get("why") or "", contract.MAX_WHY_CHARS),
            }
        )
    return {
        "hole": hole,
        "impl": clean_text(data.get("impl") or ""),
        "tests": tests,
        "note": clean_text(data.get("note") or "", contract.MAX_NOTE_CHARS),
        "dropped_tests": dropped,
    }


def balanced_span(text: str) -> str | None:
    """The first balanced ``{...}`` span of ``text`` (accepts trailing prose)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_reply(text: str) -> dict | None:
    """Lenient reply parsing; the wire stays strict.

    In order: (a) ``json.loads`` of the whole reply; (b) ``json.loads`` of
    the first balanced ``{...}`` span; (c) the fenced-block fallback — the
    first ```python block becomes ``impl`` and the first ```json block is
    parsed for ``tests``/``note``. Returns None when none of the three
    yields an ``impl`` string.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    for candidate in (text, balanced_span(text)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("impl"), str):
            return payload
    code = _PY_FENCE_RE.search(text)
    if not code:
        return None
    payload = {"impl": code.group(1), "tests": [], "note": ""}
    block = _JSON_FENCE_RE.search(text)
    if block:
        try:
            extra = json.loads(block.group(1))
        except (json.JSONDecodeError, ValueError):
            extra = None
        if isinstance(extra, dict):
            if isinstance(extra.get("tests"), list):
                payload["tests"] = extra["tests"]
            if isinstance(extra.get("note"), str):
                payload["note"] = extra["note"]
        elif isinstance(extra, list):
            payload["tests"] = extra
    return payload


def normalize_submission(
    payload, hole: int, max_tests: int = MAX_TESTS_PER_HOLE
) -> dict | None:
    """Turn a policy's answer into a strict wire message, or None.

    The policy side is lenient, the wire is strict: the impl must be a
    non-empty string within the cap, tests are trimmed to ``max_tests``
    well-formed records, and every free-text field is truncated on rune
    boundaries.
    """
    if not isinstance(payload, dict):
        return None
    impl = payload.get("impl")
    if not isinstance(impl, str) or not impl.strip():
        return None
    if len(impl) > MAX_IMPL_CHARS:
        print(
            f"player: policy impl is {len(impl)} chars (cap {MAX_IMPL_CHARS})",
            file=sys.stderr,
            flush=True,
        )
        return None
    tests = []
    for i, entry in enumerate(payload.get("tests") or []):
        if len(tests) >= max_tests:
            break
        if not isinstance(entry, dict):
            continue
        args = entry.get("args")
        if not isinstance(args, list):
            continue
        tests.append(
            {
                "name": _clip(
                    entry.get("name") or f"test {i + 1}", MAX_TEST_NAME_CHARS
                ),
                "args": args,
                "expect": entry.get("expect"),
                "why": _clip(entry.get("why") or "", MAX_WHY_CHARS),
            }
        )
    message = {
        "type": MSG_SUBMISSION,
        "hole": int(hole),
        "impl": impl,
        "tests": tests,
        "note": _clip(payload.get("note") or "", MAX_NOTE_CHARS),
    }
    while (
        len(json.dumps(message).encode("utf-8")) > MAX_MESSAGE_BYTES
        and message["tests"]
    ):
        message["tests"].pop()
    if len(json.dumps(message).encode("utf-8")) > MAX_MESSAGE_BYTES:
        return None
    return message
