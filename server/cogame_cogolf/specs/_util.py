"""Shared helper for the spec modules: load a `solve` from source text.

The reference implementation is stored as SOURCE (``REFERENCE_IMPL``) and
loaded from it, so the in-process oracle and the copy the sandbox runs are
byte-identical by construction — there is exactly one reference per spec.
"""

from __future__ import annotations

from typing import Callable


def load_impl(source: str) -> Callable:
    """Compile ``source`` and return its ``solve`` callable."""
    namespace: dict = {}
    exec(compile(source, "<spec>", "exec"), namespace)  # noqa: S102
    solve = namespace.get("solve")
    if not callable(solve):
        raise ValueError("spec source does not define a callable solve()")
    return solve
