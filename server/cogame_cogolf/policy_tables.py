"""Hand-authored baseline policy constants; no hidden oracle or audit import.

These are the unchanged literalist/pedant programs and tests shipped to players.
A policy selects them using only the public specification key.
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class PolicyTable:
    literal_impl: str
    naive_impl: str
    safe_tests: list[dict]
    edge_tests: list[dict]


POLICY_TABLES = MappingProxyType(
    {
        "chunk": PolicyTable(
            safe_tests=[
                {
                    "name": "short tail kept",
                    "args": [[1, 2, 3], 2],
                    "expect": [[1, 2], [3]],
                    "why": "nothing may be lost",
                },
                {
                    "name": "exact multiple",
                    "args": [[1, 2, 3, 4], 2],
                    "expect": [[1, 2], [3, 4]],
                    "why": "the ordinary case",
                },
                {
                    "name": "single element",
                    "args": [[1], 1],
                    "expect": [[1]],
                    "why": "one element, one chunk",
                },
                {
                    "name": "five by three",
                    "args": [[1, 2, 3, 4, 5], 3],
                    "expect": [[1, 2, 3], [4, 5]],
                    "why": "the tail is two long",
                },
                {
                    "name": "chunk bigger than list",
                    "args": [[1, 2], 5],
                    "expect": [[1, 2]],
                    "why": "one short chunk, still kept",
                },
            ],
            edge_tests=[
                {
                    "name": "empty list",
                    "args": [[], 3],
                    "expect": [],
                    "why": "no elements means no chunks at all",
                },
                {
                    "name": "strings chunk too",
                    "args": [["a", "b"], 1],
                    "expect": [["a"], ["b"]],
                    "why": "the elements need not be numbers",
                },
                {
                    "name": "huge chunk",
                    "args": [[1, 2, 3], 10],
                    "expect": [[1, 2, 3]],
                    "why": "one chunk holding everything",
                },
                {
                    "name": "zero size",
                    "args": [[1, 2, 3], 0],
                    "expect": [],
                    "why": "betting that a zero chunk size answers the empty list",
                },
                {
                    "name": "negative size",
                    "args": [[1, 2, 3], -1],
                    "expect": [[1, 2, 3]],
                    "why": "betting a negative size is treated as one big chunk",
                },
            ],
            literal_impl="def solve(xs, n):\n"
            "    out = []\n"
            "    piece = []\n"
            "    for x in xs:\n"
            "        piece.append(x)\n"
            "        if len(piece) == n:\n"
            "            out.append(piece)\n"
            "            piece = []\n"
            "    if piece or not out:\n"
            "        out.append(piece)\n"
            "    return out\n",
            naive_impl="def solve(xs, n):\n"
            "    out = []\n"
            "    for i in range(0, len(xs) - n + 1, n):\n"
            "        out.append(list(xs[i:i + n]))\n"
            "    return out\n",
        ),
        "dedupe": PolicyTable(
            safe_tests=[
                {
                    "name": "order is kept",
                    "args": [[3, 1, 2]],
                    "expect": [3, 1, 2],
                    "why": "the result is not sorted",
                },
                {
                    "name": "strings keep order",
                    "args": [["b", "a"]],
                    "expect": ["b", "a"],
                    "why": "first appearance, not alphabetical",
                },
                {
                    "name": "leading pair",
                    "args": [[2, 2, 1]],
                    "expect": [2, 1],
                    "why": "the first of a pair survives, in place",
                },
                {
                    "name": "middle pair",
                    "args": [[5, 4, 4, 3]],
                    "expect": [5, 4, 3],
                    "why": "descending input stays descending",
                },
                {
                    "name": "empty list",
                    "args": [[]],
                    "expect": [],
                    "why": "nothing to deduplicate",
                },
            ],
            edge_tests=[
                {
                    "name": "duplicate with a gap",
                    "args": [[1, 2, 1]],
                    "expect": [1, 2],
                    "why": "a duplicate need not be adjacent",
                },
                {
                    "name": "interleaved",
                    "args": [[1, 3, 1, 3]],
                    "expect": [1, 3],
                    "why": "two interleaved values collapse to two items",
                },
                {
                    "name": "nested lists",
                    "args": [[[1], [1], [2]]],
                    "expect": [[1], [2]],
                    "why": "items may be lists, which compare by value",
                },
                {
                    "name": "adjacent only",
                    "args": [[2, 3, 2]],
                    "expect": [2, 3, 2],
                    "why": "betting only neighbouring duplicates are removed",
                },
                {
                    "name": "sorted result",
                    "args": [[3, 1, 2]],
                    "expect": [1, 2, 3],
                    "why": "betting the answer comes back sorted",
                },
            ],
            literal_impl="def solve(xs):\n"
            "    out = []\n"
            "    for x in xs:\n"
            "        if not out or out[-1] != x:\n"
            "            out.append(x)\n"
            "    return out\n",
            naive_impl="def solve(xs):\n    return sorted(set(xs))\n",
        ),
        "longest_run": PolicyTable(
            safe_tests=[
                {
                    "name": "empty list",
                    "args": [[]],
                    "expect": 0,
                    "why": "a list with no elements has no runs",
                },
                {
                    "name": "single element",
                    "args": [[5]],
                    "expect": 1,
                    "why": "one element is a run of one",
                },
                {
                    "name": "leading pair",
                    "args": [[1, 1, 2]],
                    "expect": 2,
                    "why": "the longest neighbouring block wins",
                },
                {
                    "name": "all distinct",
                    "args": [[1, 2, 3]],
                    "expect": 1,
                    "why": "no two neighbours are equal",
                },
                {
                    "name": "one long run",
                    "args": [[4, 4, 4, 4]],
                    "expect": 4,
                    "why": "the whole list is one run",
                },
            ],
            edge_tests=[
                {
                    "name": "alternating values",
                    "args": [[1, 2, 1, 2, 1]],
                    "expect": 1,
                    "why": "equal elements that are not neighbours are not one run",
                },
                {
                    "name": "repeat after a gap",
                    "args": [[3, 3, 1, 3]],
                    "expect": 2,
                    "why": "the trailing 3 does not extend the leading run",
                },
                {
                    "name": "negatives",
                    "args": [[-2, -2, -2, 0]],
                    "expect": 3,
                    "why": "negative values run like any other",
                },
                {
                    "name": "empty raises",
                    "args": [[]],
                    "expect": None,
                    "why": "betting that an empty list is an error",
                },
                {
                    "name": "counts the value",
                    "args": [[9, 9]],
                    "expect": 9,
                    "why": "betting the run's value is returned, not its length",
                },
            ],
            literal_impl="def solve(xs):\n"
            "    counts = {}\n"
            "    for x in xs:\n"
            "        counts[x] = counts.get(x, 0) + 1\n"
            "    if not counts:\n"
            "        return 0\n"
            "    return max(counts.values())\n",
            naive_impl="def solve(xs):\n"
            "    best = 1\n"
            "    run = 1\n"
            "    for i in range(1, len(xs)):\n"
            "        if xs[i] == xs[i - 1]:\n"
            "            run += 1\n"
            "            if run > best:\n"
            "                best = run\n"
            "        else:\n"
            "            run = 1\n"
            "    return best\n",
        ),
        "median": PolicyTable(
            safe_tests=[
                {
                    "name": "even length",
                    "args": [[1, 2, 3, 4]],
                    "expect": 2,
                    "why": "the first of the two middle values",
                },
                {
                    "name": "two elements",
                    "args": [[1, 3]],
                    "expect": 1,
                    "why": "with two elements the earlier middle wins",
                },
                {
                    "name": "single element",
                    "args": [[5]],
                    "expect": 5,
                    "why": "one element is its own median",
                },
                {
                    "name": "odd length",
                    "args": [[1, 2, 3]],
                    "expect": 2,
                    "why": "the plain middle of an odd list",
                },
                {
                    "name": "repeated values",
                    "args": [[2, 2, 4, 4]],
                    "expect": 2,
                    "why": "duplicates do not change the rule",
                },
            ],
            edge_tests=[
                {
                    "name": "unsorted input",
                    "args": [[3, 1, 2]],
                    "expect": 2,
                    "why": "the list must be ordered before the middle is taken",
                },
                {
                    "name": "unsorted even",
                    "args": [[4, 1, 3, 2]],
                    "expect": 2,
                    "why": "ordering first changes which value is the middle",
                },
                {
                    "name": "descending pair",
                    "args": [[5, 3]],
                    "expect": 3,
                    "why": "the earlier middle after ordering, not before",
                },
                {
                    "name": "empty is an error",
                    "args": [[]],
                    "expect": 0,
                    "why": "betting that an empty list answers zero",
                },
                {
                    "name": "mean of the middles",
                    "args": [[1, 2, 3, 4]],
                    "expect": 2.5,
                    "why": "betting on the average of the two middle values",
                },
            ],
            literal_impl="def solve(xs):\n    return xs[(len(xs) - 1) // 2]\n",
            naive_impl="def solve(xs):\n"
            "    ordered = sorted(xs)\n"
            "    n = len(ordered)\n"
            "    if n % 2 == 1:\n"
            "        return ordered[n // 2]\n"
            "    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2\n",
        ),
        "path_norm": PolicyTable(
            safe_tests=[
                {
                    "name": "dotdot at root",
                    "args": ["/../a"],
                    "expect": "/a",
                    "why": "the root has nothing to remove",
                },
                {
                    "name": "walk past root",
                    "args": ["/a/../.."],
                    "expect": "/",
                    "why": "the root is still the root",
                },
                {
                    "name": "ordinary dotdot",
                    "args": ["/a/b/../c"],
                    "expect": "/a/c",
                    "why": "the component before is removed",
                },
                {
                    "name": "relative dotdot",
                    "args": ["a/../b"],
                    "expect": "b",
                    "why": "the same rule without a leading slash",
                },
                {
                    "name": "dot component",
                    "args": ["/./a"],
                    "expect": "/a",
                    "why": "a dot component disappears",
                },
            ],
            edge_tests=[
                {
                    "name": "trailing slash dropped",
                    "args": ["/a/"],
                    "expect": "/a",
                    "why": "a trailing slash names the same thing",
                },
                {
                    "name": "root keeps its slash",
                    "args": ["/"],
                    "expect": "/",
                    "why": "the root is the exception",
                },
                {
                    "name": "relative trailing slash",
                    "args": ["a/b/"],
                    "expect": "a/b",
                    "why": "relative paths drop it too",
                },
                {
                    "name": "trailing slash kept",
                    "args": ["/b/c/"],
                    "expect": "/b/c/",
                    "why": "betting that the trailing slash survives",
                },
                {
                    "name": "empty path",
                    "args": [""],
                    "expect": "",
                    "why": "betting the empty path stays empty",
                },
            ],
            literal_impl="def solve(p):\n"
            '    absolute = p.startswith("/")\n'
            "    parts = []\n"
            '    for piece in p.split("/"):\n'
            '        if piece == "" or piece == ".":\n'
            "            continue\n"
            '        if piece == "..":\n'
            '            if parts and parts[-1] != "..":\n'
            "                parts.pop()\n"
            "            elif not absolute:\n"
            '                parts.append("..")\n'
            "            continue\n"
            "        parts.append(piece)\n"
            "    if absolute:\n"
            '        out = "/" + "/".join(parts)\n'
            "    else:\n"
            '        out = "/".join(parts) or "."\n'
            '    if p.endswith("/") and out != "/" and not out.endswith("/"):\n'
            '        out = out + "/"\n'
            "    return out\n",
            naive_impl="def solve(p):\n"
            '    absolute = p.startswith("/")\n'
            "    parts = []\n"
            '    for piece in p.split("/"):\n'
            '        if piece == "" or piece == ".":\n'
            "            continue\n"
            '        if piece == "..":\n'
            '            if parts and parts[-1] != "..":\n'
            "                parts.pop()\n"
            "            else:\n"
            '                parts.append("..")\n'
            "            continue\n"
            "        parts.append(piece)\n"
            "    if absolute:\n"
            '        return "/" + "/".join(parts)\n'
            '    return "/".join(parts) or "."\n',
        ),
        "range_merge": PolicyTable(
            safe_tests=[
                {
                    "name": "shared endpoint",
                    "args": [[[1, 2], [2, 3]]],
                    "expect": [[1, 3]],
                    "why": "both ranges cover the number 2",
                },
                {
                    "name": "disjoint",
                    "args": [[[1, 3], [5, 7]]],
                    "expect": [[1, 3], [5, 7]],
                    "why": "a gap keeps them apart",
                },
                {
                    "name": "empty list",
                    "args": [[]],
                    "expect": [],
                    "why": "nothing to merge",
                },
                {
                    "name": "nested",
                    "args": [[[1, 5], [2, 3]]],
                    "expect": [[1, 5]],
                    "why": "a contained range disappears",
                },
                {
                    "name": "two points",
                    "args": [[[0, 0], [0, 0]]],
                    "expect": [[0, 0]],
                    "why": "a point range covers one number, shared by both",
                },
            ],
            edge_tests=[
                {
                    "name": "out of order",
                    "args": [[[5, 6], [1, 2]]],
                    "expect": [[1, 2], [5, 6]],
                    "why": "the input order is not the answer's",
                },
                {
                    "name": "chain out of order",
                    "args": [[[3, 4], [1, 2], [2, 9]]],
                    "expect": [[1, 9]],
                    "why": "merging can cascade once sorted",
                },
                {
                    "name": "adjacent integers",
                    "args": [[[1, 2], [3, 4]]],
                    "expect": [[1, 2], [3, 4]],
                    "why": "1..2 and 3..4 share no number",
                },
                {
                    "name": "gap merged",
                    "args": [[[10, 11], [12, 13]]],
                    "expect": [[10, 13]],
                    "why": "betting that neighbouring integers merge",
                },
                {
                    "name": "ends exclusive",
                    "args": [[[1, 2], [2, 3]]],
                    "expect": [[1, 2], [2, 3]],
                    "why": "betting the end is not covered",
                },
            ],
            literal_impl="def solve(ranges):\n"
            "    out = []\n"
            "    for r in ranges:\n"
            "        start, end = r[0], r[1]\n"
            "        if out and start <= out[-1][1]:\n"
            "            if end > out[-1][1]:\n"
            "                out[-1][1] = end\n"
            "        else:\n"
            "            out.append([start, end])\n"
            "    return out\n",
            naive_impl="def solve(ranges):\n"
            "    ordered = sorted([list(r) for r in ranges])\n"
            "    out = []\n"
            "    for start, end in ordered:\n"
            "        if out and start < out[-1][1]:\n"
            "            if end > out[-1][1]:\n"
            "                out[-1][1] = end\n"
            "        else:\n"
            "            out.append([start, end])\n"
            "    return out\n",
        ),
        "roman": PolicyTable(
            safe_tests=[
                {
                    "name": "three",
                    "args": [3],
                    "expect": "III",
                    "why": "the plain additive case",
                },
                {
                    "name": "four",
                    "args": [4],
                    "expect": "IV",
                    "why": "the subtractive form for four",
                },
                {
                    "name": "nine",
                    "args": [9],
                    "expect": "IX",
                    "why": "the subtractive form for nine",
                },
                {
                    "name": "forty",
                    "args": [40],
                    "expect": "XL",
                    "why": "the subtractive form for forty",
                },
                {
                    "name": "forty four",
                    "args": [44],
                    "expect": "XLIV",
                    "why": "two subtractive forms in one numeral",
                },
            ],
            edge_tests=[
                {
                    "name": "four hundred",
                    "args": [400],
                    "expect": "CD",
                    "why": "the subtractive form applies at the hundreds too",
                },
                {
                    "name": "nine hundred",
                    "args": [900],
                    "expect": "CM",
                    "why": "nine hundred is CM, never DCCCC",
                },
                {
                    "name": "mixed thousands",
                    "args": [1904],
                    "expect": "MCMIV",
                    "why": "CM and IV in the same numeral",
                },
                {
                    "name": "zero",
                    "args": [0],
                    "expect": "",
                    "why": "betting that zero answers the empty string",
                },
                {
                    "name": "over range",
                    "args": [4000],
                    "expect": "MMMM",
                    "why": "betting that four thousand is just four Ms",
                },
            ],
            literal_impl="def solve(n):\n"
            '    table = [(1000, "M"), (500, "D"), (100, "C"), (90, "XC"), (50, "L"),\n'
            '             (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]\n'
            "    out = []\n"
            "    left = n\n"
            "    for value, sign in table:\n"
            "        while left >= value:\n"
            "            out.append(sign)\n"
            "            left -= value\n"
            '    return "".join(out)\n',
            naive_impl="def solve(n):\n"
            '    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),\n'
            '             (100, "C"), (50, "L"), (10, "X"), (5, "V"), (1, "I")]\n'
            "    out = []\n"
            "    left = n\n"
            "    for value, sign in table:\n"
            "        while left >= value:\n"
            "            out.append(sign)\n"
            "            left -= value\n"
            '    return "".join(out)\n',
        ),
        "round_to": PolicyTable(
            safe_tests=[
                {
                    "name": "positive half",
                    "args": [2.5, 0],
                    "expect": 3.0,
                    "why": "a half goes away from zero",
                },
                {
                    "name": "negative half",
                    "args": [-2.5, 0],
                    "expect": -3.0,
                    "why": "away from zero also means down for negatives",
                },
                {
                    "name": "one decimal",
                    "args": [1.25, 1],
                    "expect": 1.3,
                    "why": "the same rule one place in",
                },
                {
                    "name": "small half",
                    "args": [0.5, 0],
                    "expect": 1.0,
                    "why": "0.5 is not rounded to the even neighbour",
                },
                {
                    "name": "small negative half",
                    "args": [-0.5, 0],
                    "expect": -1.0,
                    "why": "and neither is -0.5",
                },
            ],
            edge_tests=[
                {
                    "name": "round to hundreds",
                    "args": [12345.0, -2],
                    "expect": 12300.0,
                    "why": "negative n rounds to the left of the point",
                },
                {
                    "name": "hundreds half up",
                    "args": [1250.0, -2],
                    "expect": 1300.0,
                    "why": "the halfway rule holds for negative n too",
                },
                {
                    "name": "round to tens",
                    "args": [175.0, -1],
                    "expect": 180.0,
                    "why": "n = -1 rounds to whole tens",
                },
                {
                    "name": "banker's rounding",
                    "args": [2.5, 0],
                    "expect": 2.0,
                    "why": "betting on Python's round(), which picks the even neighbour",
                },
                {
                    "name": "negative n ignored",
                    "args": [9999.0, -2],
                    "expect": 9999.0,
                    "why": "betting that a negative n leaves the value alone",
                },
            ],
            literal_impl="def solve(x, n):\n"
            "    import math\n"
            "    places = n if n > 0 else 0\n"
            "    scale = 10 ** places\n"
            "    sign = -1.0 if x < 0 else 1.0\n"
            "    return sign * math.floor(abs(x) * scale + 0.5) / scale\n",
            naive_impl="def solve(x, n):\n    return float(round(x, n))\n",
        ),
        "score_grade": PolicyTable(
            safe_tests=[
                {
                    "name": "exactly ninety",
                    "args": [90],
                    "expect": "A",
                    "why": "a score on the threshold earns the better letter",
                },
                {
                    "name": "exactly eighty",
                    "args": [80],
                    "expect": "B",
                    "why": "the same rule at the B line",
                },
                {
                    "name": "exactly seventy",
                    "args": [70],
                    "expect": "C",
                    "why": "the same rule at the C line",
                },
                {
                    "name": "exactly sixty",
                    "args": [60],
                    "expect": "D",
                    "why": "the same rule at the D line",
                },
                {
                    "name": "just under",
                    "args": [59],
                    "expect": "F",
                    "why": "below the last threshold",
                },
            ],
            edge_tests=[
                {
                    "name": "over a hundred",
                    "args": [120],
                    "expect": "A",
                    "why": "a score above 100 is still the best grade there is",
                },
                {
                    "name": "negative score",
                    "args": [-5],
                    "expect": "F",
                    "why": "a score below zero is still the worst",
                },
                {
                    "name": "fractional",
                    "args": [89.5],
                    "expect": "B",
                    "why": "89.5 has not reached the A line",
                },
                {
                    "name": "ninety is a B",
                    "args": [90],
                    "expect": "B",
                    "why": "betting the thresholds are strict",
                },
                {
                    "name": "hundred and one",
                    "args": [101],
                    "expect": None,
                    "why": "betting that anything over 100 is rejected",
                },
            ],
            literal_impl="def solve(score):\n"
            "    if score > 100:\n"
            '        raise ValueError("score above 100")\n'
            "    if score >= 90:\n"
            '        return "A"\n'
            "    if score >= 80:\n"
            '        return "B"\n'
            "    if score >= 70:\n"
            '        return "C"\n'
            "    if score >= 60:\n"
            '        return "D"\n'
            '    return "F"\n',
            naive_impl="def solve(score):\n"
            "    if score > 90:\n"
            '        return "A"\n'
            "    if score > 80:\n"
            '        return "B"\n'
            "    if score > 70:\n"
            '        return "C"\n'
            "    if score > 60:\n"
            '        return "D"\n'
            '    return "F"\n',
        ),
        "title_case": PolicyTable(
            safe_tests=[
                {
                    "name": "double space kept",
                    "args": ["a  b"],
                    "expect": "A  B",
                    "why": "the spacing of the input is part of the input",
                },
                {
                    "name": "plain sentence",
                    "args": ["hello world"],
                    "expect": "Hello World",
                    "why": "the ordinary case",
                },
                {
                    "name": "empty string",
                    "args": [""],
                    "expect": "",
                    "why": "nothing to capitalise",
                },
                {
                    "name": "leading space",
                    "args": [" x"],
                    "expect": " X",
                    "why": "a leading space is not removed",
                },
                {
                    "name": "three words",
                    "args": ["one two three"],
                    "expect": "One Two Three",
                    "why": "every word, not just the first",
                },
            ],
            edge_tests=[
                {
                    "name": "all caps word",
                    "args": ["NASA rocket"],
                    "expect": "NASA Rocket",
                    "why": "a word already in capitals is left unchanged",
                },
                {
                    "name": "mixed case tail",
                    "args": ["mcDonald ate"],
                    "expect": "McDonald Ate",
                    "why": "only the first character changes",
                },
                {
                    "name": "apostrophe",
                    "args": ["it's fine"],
                    "expect": "It's Fine",
                    "why": "the rest of the word is untouched",
                },
                {
                    "name": "lowercases the tail",
                    "args": ["USA today"],
                    "expect": "Usa Today",
                    "why": "betting on Python's str.title()",
                },
                {
                    "name": "collapses spacing",
                    "args": ["a  b"],
                    "expect": "A B",
                    "why": "betting that runs of spaces are squeezed",
                },
            ],
            literal_impl='def solve(s):\n    return " ".join(w.capitalize() for w in s.split(" "))\n',
            naive_impl='def solve(s):\n    return " ".join(w[:1].upper() + w[1:] for w in s.split())\n',
        ),
        "top_k": PolicyTable(
            safe_tests=[
                {
                    "name": "tie keeps order",
                    "args": [[3, 1, 3, 1, 2], 2],
                    "expect": [3, 1],
                    "why": "3 appeared before 1 and both occur twice",
                },
                {
                    "name": "string tie",
                    "args": [["b", "a", "b", "a"], 2],
                    "expect": ["b", "a"],
                    "why": "first appearance, not alphabetical",
                },
                {
                    "name": "clear winner",
                    "args": [[5, 5, 4], 1],
                    "expect": [5],
                    "why": "the most frequent item alone",
                },
                {
                    "name": "k is zero",
                    "args": [[1, 2, 3], 0],
                    "expect": [],
                    "why": "k may be 0",
                },
                {
                    "name": "another tie",
                    "args": [[2, 1, 2, 1], 2],
                    "expect": [2, 1],
                    "why": "the earlier of two equally frequent items leads",
                },
            ],
            edge_tests=[
                {
                    "name": "k over distinct",
                    "args": [[1, 2], 5],
                    "expect": [1, 2],
                    "why": "k is a request, not a promise",
                },
                {
                    "name": "one item, big k",
                    "args": [[1], 3],
                    "expect": [1],
                    "why": "fewer distinct items than asked for",
                },
                {
                    "name": "empty list",
                    "args": [[], 2],
                    "expect": [],
                    "why": "no items to rank",
                },
                {
                    "name": "k over distinct errors",
                    "args": [[7, 8], 9],
                    "expect": None,
                    "why": "betting that too large a k is an error",
                },
                {
                    "name": "ties sorted by value",
                    "args": [[3, 1, 3, 1, 2], 2],
                    "expect": [1, 3],
                    "why": "betting ties break by value",
                },
            ],
            literal_impl="def solve(xs, k):\n"
            "    counts = {}\n"
            "    for x in xs:\n"
            "        counts[x] = counts.get(x, 0) + 1\n"
            "    ranked = sorted(counts, key=lambda item: -counts[item])\n"
            "    return [ranked[i] for i in range(k)]\n",
            naive_impl="def solve(xs, k):\n"
            "    counts = {}\n"
            "    for x in xs:\n"
            "        counts[x] = counts.get(x, 0) + 1\n"
            "    ranked = sorted(counts, key=lambda item: (-counts[item], item))\n"
            "    return ranked[:k]\n",
        ),
        "word_count": PolicyTable(
            safe_tests=[
                {
                    "name": "plain repeat",
                    "args": ["hi hi"],
                    "expect": {"hi": 2},
                    "why": "the ordinary case",
                },
                {
                    "name": "case folds",
                    "args": ["Hi hi"],
                    "expect": {"hi": 2},
                    "why": "case is not part of a word's identity",
                },
                {
                    "name": "punctuation stripped",
                    "args": ["a, a."],
                    "expect": {"a": 2},
                    "why": "punctuation stuck to a word is not part of it",
                },
                {
                    "name": "empty string",
                    "args": [""],
                    "expect": {},
                    "why": "no words at all",
                },
                {
                    "name": "two words",
                    "args": ["One two"],
                    "expect": {"one": 1, "two": 1},
                    "why": "each word counted once",
                },
            ],
            edge_tests=[
                {
                    "name": "contraction",
                    "args": ["don't don't"],
                    "expect": {"don't": 2},
                    "why": "what is inside a word stays inside it",
                },
                {
                    "name": "possessive",
                    "args": ["it's"],
                    "expect": {"it's": 1},
                    "why": "an inner apostrophe survives",
                },
                {
                    "name": "quoted word",
                    "args": ['"hi" hi'],
                    "expect": {"hi": 2},
                    "why": "quotes are edge punctuation",
                },
                {
                    "name": "apostrophe dropped",
                    "args": ["don't"],
                    "expect": {"dont": 1},
                    "why": "betting every non-letter is removed",
                },
                {
                    "name": "case kept",
                    "args": ["Hi hi"],
                    "expect": {"Hi": 1, "hi": 1},
                    "why": "betting that case distinguishes words",
                },
            ],
            literal_impl="def solve(s):\n"
            "    counts = {}\n"
            "    for raw in s.split():\n"
            '        word = "".join(ch for ch in raw if ch.isalnum()).lower()\n'
            "        if not word:\n"
            "            continue\n"
            "        counts[word] = counts.get(word, 0) + 1\n"
            "    return counts\n",
            naive_impl="def solve(s):\n"
            "    counts = {}\n"
            "    for word in s.split():\n"
            "        counts[word] = counts.get(word, 0) + 1\n"
            "    return counts\n",
        ),
    }
)
