"""path_norm — normalise a POSIX-ish path."""

from ._util import load_impl

KEY = "path_norm"
TITLE = "Normalise a path"
PROMPT = """Write solve(p) where p is a POSIX-style path string.

Return the same path with the noise removed: runs of slashes collapse to one,
a "." component disappears, and a ".." component removes the component before
it. A path that ends in a slash names the same thing without it, except for
the root, which is written "/".

The root has no parent, so a ".." there simply has nothing to remove. A
relative path that has walked above its start keeps its leading ".."
components, and a path that normalises to nothing is written "."."""
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "p", "type": "str"}],
    "returns": "str",
}
EXAMPLES = [
    {"args": ["/a/b/../c"], "expect": "/a/c"},
    {"args": ["/a/"], "expect": "/a"},
]

REFERENCE_IMPL = """def solve(p):
    absolute = p.startswith("/")
    parts = []
    for piece in p.split("/"):
        if piece == "" or piece == ".":
            continue
        if piece == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not absolute:
                parts.append("..")
            continue
        parts.append(piece)
    if absolute:
        return "/" + "/".join(parts)
    return "/".join(parts) or "."
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "trailing slash", "args": ["/a/"], "expect": "/a"},
    {"name": "dotdot at root", "args": ["/../x"], "expect": "/x"},
    {"name": "double slash", "args": ["/a//b"], "expect": "/a/b"},
    {"name": "relative dotdot kept", "args": ["../x"], "expect": "../x"},
]


AMBIGUITY = "A trailing slash is dropped (except for /), and .. at the root is dropped, not an error."
