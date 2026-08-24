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
SIGNATURE = {"function": "solve",
             "params": [{"name": "s", "type": "str"}],
             "returns": "dict[str, int]"}
EXAMPLES = [
    {"args": ["Hi, hi there"], "expect": {"hi": 2, "there": 1}},
    {"args": ["don't stop"], "expect": {"don't": 1, "stop": 1}},
]

REFERENCE_IMPL = '''def solve(s):
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
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "apostrophe and case", "args": ["Don't stop"],
     "expect": {"don't": 1, "stop": 1}},
    {"name": "comma and case", "args": ["Hello, hello"], "expect": {"hello": 2}},
    {"name": "one word", "args": ["x"], "expect": {"x": 1}},
    {"name": "only spaces", "args": ["   "], "expect": {}},
]

SAFE_TESTS = [
    {"name": "plain repeat", "args": ["hi hi"], "expect": {"hi": 2},
     "why": "the ordinary case"},
    {"name": "case folds", "args": ["Hi hi"], "expect": {"hi": 2},
     "why": "case is not part of a word's identity"},
    {"name": "punctuation stripped", "args": ["a, a."], "expect": {"a": 2},
     "why": "punctuation stuck to a word is not part of it"},
    {"name": "empty string", "args": [""], "expect": {},
     "why": "no words at all"},
    {"name": "two words", "args": ["One two"], "expect": {"one": 1, "two": 1},
     "why": "each word counted once"},
]

EDGE_TESTS = [
    {"name": "contraction", "args": ["don't don't"], "expect": {"don't": 2},
     "why": "what is inside a word stays inside it"},
    {"name": "possessive", "args": ["it's"], "expect": {"it's": 1},
     "why": "an inner apostrophe survives"},
    {"name": "quoted word", "args": ['"hi" hi'], "expect": {"hi": 2},
     "why": "quotes are edge punctuation"},
    {"name": "apostrophe dropped", "args": ["don't"], "expect": {"dont": 1},
     "why": "betting every non-letter is removed"},
    {"name": "case kept", "args": ["Hi hi"], "expect": {"Hi": 1, "hi": 1},
     "why": "betting that case distinguishes words"},
]

LITERAL_IMPL = '''def solve(s):
    counts = {}
    for raw in s.split():
        word = "".join(ch for ch in raw if ch.isalnum()).lower()
        if not word:
            continue
        counts[word] = counts.get(word, 0) + 1
    return counts
'''

NAIVE_IMPL = '''def solve(s):
    counts = {}
    for word in s.split():
        counts[word] = counts.get(word, 0) + 1
    return counts
'''

AMBIGUITY = "Words fold to lower case and lose edge punctuation, but an inner apostrophe stays: don't."
