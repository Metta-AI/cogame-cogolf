"""roman — an integer 1..3999 as a Roman numeral."""

from ._util import load_impl

KEY = "roman"
TITLE = "Roman numerals"
PROMPT = """Write solve(n) where n is an integer.

Return the Roman numeral for n, written the way Romans wrote it on
monuments: the subtractive forms are used wherever they apply, so 4 is "IV",
9 is "IX", 40 is "XL", 90 is "XC", 400 is "CD" and 900 is "CM".

n is between 1 and 3999 inclusive; anything outside that range is not a
number this function can answer."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "n", "type": "int"}],
             "returns": "str"}
EXAMPLES = [
    {"args": [4], "expect": "IV"},
    {"args": [1987], "expect": "MCMLXXXVII"},
]

REFERENCE_IMPL = '''def solve(n):
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3999:
        raise ValueError("roman numerals cover 1..3999")
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
             (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
             (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = []
    left = n
    for value, sign in table:
        while left >= value:
            out.append(sign)
            left -= value
    return "".join(out)
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "nineteen ninety four", "args": [1994], "expect": "MCMXCIV"},
    {"name": "this year", "args": [2024], "expect": "MMXXIV"},
    {"name": "four hundred", "args": [400], "expect": "CD"},
    {"name": "the biggest", "args": [3999], "expect": "MMMCMXCIX"},
]

SAFE_TESTS = [
    {"name": "three", "args": [3], "expect": "III",
     "why": "the plain additive case"},
    {"name": "four", "args": [4], "expect": "IV",
     "why": "the subtractive form for four"},
    {"name": "nine", "args": [9], "expect": "IX",
     "why": "the subtractive form for nine"},
    {"name": "forty", "args": [40], "expect": "XL",
     "why": "the subtractive form for forty"},
    {"name": "forty four", "args": [44], "expect": "XLIV",
     "why": "two subtractive forms in one numeral"},
]

EDGE_TESTS = [
    {"name": "four hundred", "args": [400], "expect": "CD",
     "why": "the subtractive form applies at the hundreds too"},
    {"name": "nine hundred", "args": [900], "expect": "CM",
     "why": "nine hundred is CM, never DCCCC"},
    {"name": "mixed thousands", "args": [1904], "expect": "MCMIV",
     "why": "CM and IV in the same numeral"},
    {"name": "zero", "args": [0], "expect": "",
     "why": "betting that zero answers the empty string"},
    {"name": "over range", "args": [4000], "expect": "MMMM",
     "why": "betting that four thousand is just four Ms"},
]

LITERAL_IMPL = '''def solve(n):
    table = [(1000, "M"), (500, "D"), (100, "C"), (90, "XC"), (50, "L"),
             (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = []
    left = n
    for value, sign in table:
        while left >= value:
            out.append(sign)
            left -= value
    return "".join(out)
'''

NAIVE_IMPL = '''def solve(n):
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
             (100, "C"), (50, "L"), (10, "X"), (5, "V"), (1, "I")]
    out = []
    left = n
    for value, sign in table:
        while left >= value:
            out.append(sign)
            left -= value
    return "".join(out)
'''

AMBIGUITY = "Subtractive forms everywhere (400 is CD, 900 is CM); 0 and 4000 are errors, not numerals."
