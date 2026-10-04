"""chunk — split a list into chunks of a fixed size."""

from ._util import load_impl

KEY = "chunk"
TITLE = "Chunk a list"
PROMPT = """Write solve(xs, n) where xs is a list and n is an integer.

Return a list of chunks: the elements of xs in order, cut into pieces of n
elements each. Nothing may be lost, so when the length of xs is not a
multiple of n the last piece is shorter than the others.

n is at least 1; a chunk size of zero or less has no meaning."""
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "xs", "type": "list"}, {"name": "n", "type": "int"}],
    "returns": "list[list]",
}
EXAMPLES = [
    {"args": [[1, 2, 3, 4], 2], "expect": [[1, 2], [3, 4]]},
    {"args": [[1, 2, 3], 2], "expect": [[1, 2], [3]]},
]

REFERENCE_IMPL = """def solve(xs, n):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("chunk size must be at least 1")
    return [list(xs[i:i + n]) for i in range(0, len(xs), n)]
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "empty list", "args": [[], 2], "expect": []},
    {"name": "short tail", "args": [[1, 2, 3], 2], "expect": [[1, 2], [3]]},
    {"name": "exact fit", "args": [[1, 2, 3, 4], 4], "expect": [[1, 2, 3, 4]]},
    {
        "name": "two tails",
        "args": [[1, 2, 3, 4, 5], 2],
        "expect": [[1, 2], [3, 4], [5]],
    },
]


AMBIGUITY = (
    "The short trailing chunk is kept, an empty list gives [], and n <= 0 is an error."
)
