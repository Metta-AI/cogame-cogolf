"""score_grade — a numeric score as a letter grade."""

from ._util import load_impl

KEY = "score_grade"
TITLE = "Letter grade"
PROMPT = """Write solve(score) where score is a number.

Return the letter grade: "A" from 90, "B" from 80, "C" from 70, "D" from 60,
and "F" below that. Each threshold is the lowest score that earns its letter,
so a score sitting exactly on a threshold earns the better letter.

Marks are sometimes generous and sometimes negative. A score above 100 is
still the best grade there is, and a score below zero is still the worst."""
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "score", "type": "float"}],
    "returns": "str",
}
EXAMPLES = [
    {"args": [90], "expect": "A"},
    {"args": [59.5], "expect": "F"},
]

REFERENCE_IMPL = """def solve(score):
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "on the A line", "args": [90], "expect": "A"},
    {"name": "on the D line", "args": [60], "expect": "D"},
    {"name": "over a hundred", "args": [120], "expect": "A"},
    {"name": "negative", "args": [-3], "expect": "F"},
]


AMBIGUITY = "Thresholds are inclusive (90 is an A), >100 clamps to A and a negative score is an F."
