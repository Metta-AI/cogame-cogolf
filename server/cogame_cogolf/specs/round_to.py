"""round_to — round a number to a number of decimals."""

from ._util import load_impl

KEY = "round_to"
TITLE = "Round to n decimals"
PROMPT = """Write solve(x, n) where x is a number and n is an integer.

Return x rounded to n decimal places, as a number. A value exactly halfway
between two candidates goes to the candidate further from zero, the way a
till rounds: 2.5 becomes 3 and -2.5 becomes -3.

n may also be zero or negative. Negative n keeps rounding by the same rule,
one place further to the left each time: n = -1 rounds to whole tens, n = -2
to whole hundreds."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "x", "type": "float"},
                        {"name": "n", "type": "int"}],
             "returns": "float"}
EXAMPLES = [
    {"args": [2.5, 0], "expect": 3.0},
    {"args": [1250.0, -2], "expect": 1300.0},
]

REFERENCE_IMPL = '''def solve(x, n):
    from decimal import Decimal, ROUND_HALF_UP
    quantum = Decimal(1).scaleb(-n)
    value = Decimal(str(x)).quantize(quantum, rounding=ROUND_HALF_UP)
    return float(value)
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "half up", "args": [2.5, 0], "expect": 3.0},
    {"name": "third decimal", "args": [0.125, 2], "expect": 0.13},
    {"name": "hundreds down", "args": [12345.0, -2], "expect": 12300.0},
    {"name": "hundreds half", "args": [1250.0, -2], "expect": 1300.0},
]

SAFE_TESTS = [
    {"name": "positive half", "args": [2.5, 0], "expect": 3.0,
     "why": "a half goes away from zero"},
    {"name": "negative half", "args": [-2.5, 0], "expect": -3.0,
     "why": "away from zero also means down for negatives"},
    {"name": "one decimal", "args": [1.25, 1], "expect": 1.3,
     "why": "the same rule one place in"},
    {"name": "small half", "args": [0.5, 0], "expect": 1.0,
     "why": "0.5 is not rounded to the even neighbour"},
    {"name": "small negative half", "args": [-0.5, 0], "expect": -1.0,
     "why": "and neither is -0.5"},
]

EDGE_TESTS = [
    {"name": "round to hundreds", "args": [12345.0, -2], "expect": 12300.0,
     "why": "negative n rounds to the left of the point"},
    {"name": "hundreds half up", "args": [1250.0, -2], "expect": 1300.0,
     "why": "the halfway rule holds for negative n too"},
    {"name": "round to tens", "args": [175.0, -1], "expect": 180.0,
     "why": "n = -1 rounds to whole tens"},
    {"name": "banker's rounding", "args": [2.5, 0], "expect": 2.0,
     "why": "betting on Python's round(), which picks the even neighbour"},
    {"name": "negative n ignored", "args": [9999.0, -2], "expect": 9999.0,
     "why": "betting that a negative n leaves the value alone"},
]

LITERAL_IMPL = '''def solve(x, n):
    import math
    places = n if n > 0 else 0
    scale = 10 ** places
    sign = -1.0 if x < 0 else 1.0
    return sign * math.floor(abs(x) * scale + 0.5) / scale
'''

NAIVE_IMPL = '''def solve(x, n):
    return float(round(x, n))
'''

AMBIGUITY = "Halves go AWAY from zero (2.5 -> 3), and a negative n rounds to tens or hundreds."
