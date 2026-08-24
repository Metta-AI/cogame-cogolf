"""chunk — split a list into chunks of a fixed size."""

from ._util import load_impl

KEY = "chunk"
TITLE = "Chunk a list"
PROMPT = """Write solve(xs, n) where xs is a list and n is an integer.

Return a list of chunks: the elements of xs in order, cut into pieces of n
elements each. Nothing may be lost, so when the length of xs is not a
multiple of n the last piece is shorter than the others.

n is at least 1; a chunk size of zero or less has no meaning."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "xs", "type": "list"},
                        {"name": "n", "type": "int"}],
             "returns": "list[list]"}
EXAMPLES = [
    {"args": [[1, 2, 3, 4], 2], "expect": [[1, 2], [3, 4]]},
    {"args": [[1, 2, 3], 2], "expect": [[1, 2], [3]]},
]

REFERENCE_IMPL = '''def solve(xs, n):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("chunk size must be at least 1")
    return [list(xs[i:i + n]) for i in range(0, len(xs), n)]
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "empty list", "args": [[], 2], "expect": []},
    {"name": "short tail", "args": [[1, 2, 3], 2], "expect": [[1, 2], [3]]},
    {"name": "exact fit", "args": [[1, 2, 3, 4], 4], "expect": [[1, 2, 3, 4]]},
    {"name": "two tails", "args": [[1, 2, 3, 4, 5], 2],
     "expect": [[1, 2], [3, 4], [5]]},
]

SAFE_TESTS = [
    {"name": "short tail kept", "args": [[1, 2, 3], 2],
     "expect": [[1, 2], [3]], "why": "nothing may be lost"},
    {"name": "exact multiple", "args": [[1, 2, 3, 4], 2],
     "expect": [[1, 2], [3, 4]], "why": "the ordinary case"},
    {"name": "single element", "args": [[1], 1], "expect": [[1]],
     "why": "one element, one chunk"},
    {"name": "five by three", "args": [[1, 2, 3, 4, 5], 3],
     "expect": [[1, 2, 3], [4, 5]], "why": "the tail is two long"},
    {"name": "chunk bigger than list", "args": [[1, 2], 5],
     "expect": [[1, 2]], "why": "one short chunk, still kept"},
]

EDGE_TESTS = [
    {"name": "empty list", "args": [[], 3], "expect": [],
     "why": "no elements means no chunks at all"},
    {"name": "strings chunk too", "args": [["a", "b"], 1],
     "expect": [["a"], ["b"]], "why": "the elements need not be numbers"},
    {"name": "huge chunk", "args": [[1, 2, 3], 10], "expect": [[1, 2, 3]],
     "why": "one chunk holding everything"},
    {"name": "zero size", "args": [[1, 2, 3], 0], "expect": [],
     "why": "betting that a zero chunk size answers the empty list"},
    {"name": "negative size", "args": [[1, 2, 3], -1], "expect": [[1, 2, 3]],
     "why": "betting a negative size is treated as one big chunk"},
]

LITERAL_IMPL = '''def solve(xs, n):
    out = []
    piece = []
    for x in xs:
        piece.append(x)
        if len(piece) == n:
            out.append(piece)
            piece = []
    if piece or not out:
        out.append(piece)
    return out
'''

NAIVE_IMPL = '''def solve(xs, n):
    out = []
    for i in range(0, len(xs) - n + 1, n):
        out.append(list(xs[i:i + n]))
    return out
'''

AMBIGUITY = "The short trailing chunk is kept, an empty list gives [], and n <= 0 is an error."
