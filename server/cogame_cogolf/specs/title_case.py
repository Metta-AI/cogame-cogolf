"""title_case — capitalise each word of a string."""

from ._util import load_impl

KEY = "title_case"
TITLE = "Title case a sentence"
PROMPT = """Write solve(s) where s is a string.

Return s with each word capitalised. Words are separated by spaces. A word is
capitalised by putting its first character in upper case; the rest of the word
is left exactly as it was, so a word that is already written in capitals comes
back unchanged.

The spacing of the input is part of the input."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "s", "type": "str"}],
             "returns": "str"}
EXAMPLES = [
    {"args": ["hello world"], "expect": "Hello World"},
    {"args": ["NASA rocket"], "expect": "NASA Rocket"},
]

REFERENCE_IMPL = '''def solve(s):
    words = s.split(" ")
    out = []
    for w in words:
        out.append(w[:1].upper() + w[1:] if w else w)
    return " ".join(out)
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "caps and spacing", "args": ["NASA  usa"], "expect": "NASA  Usa"},
    {"name": "double space", "args": ["a  b"], "expect": "A  B"},
    {"name": "acronym", "args": ["USA today"], "expect": "USA Today"},
    {"name": "empty string", "args": [""], "expect": ""},
]

SAFE_TESTS = [
    {"name": "double space kept", "args": ["a  b"], "expect": "A  B",
     "why": "the spacing of the input is part of the input"},
    {"name": "plain sentence", "args": ["hello world"], "expect": "Hello World",
     "why": "the ordinary case"},
    {"name": "empty string", "args": [""], "expect": "",
     "why": "nothing to capitalise"},
    {"name": "leading space", "args": [" x"], "expect": " X",
     "why": "a leading space is not removed"},
    {"name": "three words", "args": ["one two three"],
     "expect": "One Two Three", "why": "every word, not just the first"},
]

EDGE_TESTS = [
    {"name": "all caps word", "args": ["NASA rocket"], "expect": "NASA Rocket",
     "why": "a word already in capitals is left unchanged"},
    {"name": "mixed case tail", "args": ["mcDonald ate"],
     "expect": "McDonald Ate", "why": "only the first character changes"},
    {"name": "apostrophe", "args": ["it's fine"], "expect": "It's Fine",
     "why": "the rest of the word is untouched"},
    {"name": "lowercases the tail", "args": ["USA today"],
     "expect": "Usa Today", "why": "betting on Python's str.title()"},
    {"name": "collapses spacing", "args": ["a  b"], "expect": "A B",
     "why": "betting that runs of spaces are squeezed"},
]

LITERAL_IMPL = '''def solve(s):
    return " ".join(w.capitalize() for w in s.split(" "))
'''

NAIVE_IMPL = '''def solve(s):
    return " ".join(w[:1].upper() + w[1:] for w in s.split())
'''

AMBIGUITY = "Only the first character changes (NASA stays NASA) and runs of spaces survive."
