"""word_count — count the words of a string."""

from ._util import load_impl

KEY = "word_count"
TITLE = "Count the words"
PROMPT = """Write solve(s) where s is a string.

Return an object mapping each word of s to the number of times it occurs.
Words are separated by whitespace. Case is not part of a word's identity, and
punctuation stuck to the front or back of a word is not part of the word
either. What is inside a word stays inside it: "don't" is one word, spelled
"don't".

A string with no words maps to an empty object."""
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "s", "type": "str"}],
    "returns": "dict[str, int]",
}
EXAMPLES = [
    {"args": ["Hi, hi there"], "expect": {"hi": 2, "there": 1}},
    {"args": ["don't stop"], "expect": {"don't": 1, "stop": 1}},
]

REFERENCE_IMPL = """def solve(s):
    counts = {}
    for raw in s.split():
        word = raw
        while word and not word[0].isalnum():
            word = word[1:]
        while word and not word[-1].isalnum():
            word = word[:-1]
        if not word:
            continue
        word = word.lower()
        counts[word] = counts.get(word, 0) + 1
    return counts
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {
        "name": "apostrophe and case",
        "args": ["Don't stop"],
        "expect": {"don't": 1, "stop": 1},
    },
    {"name": "comma and case", "args": ["Hello, hello"], "expect": {"hello": 2}},
    {"name": "one word", "args": ["x"], "expect": {"x": 1}},
    {"name": "only spaces", "args": ["   "], "expect": {}},
]


AMBIGUITY = "Words fold to lower case and lose edge punctuation, but an inner apostrophe stays: don't."
