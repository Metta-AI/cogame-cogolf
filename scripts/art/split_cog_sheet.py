#!/usr/bin/env python3
"""Splits the nano-banana cog sheet into the two seat sprites.

``scripts/art/source/cogs_sheet.png`` is a single Gemini
("nano-banana") render of the Softmax cog in the two cogolf seat kits —
Ash (amber plating, a long dart held like a javelin, a rolled spec scroll
under the arm) and Basil (blue plating, a riveted steel shield and a short
dart) — on a flat green backdrop. Adapted from
``Metta-AI/cogame-raid``'s ``scripts/art/split_cog_sheet.py``: the same
edge flood-fill key, split, crop and pad, plus one cogolf addition — the
render puts a small caption under each cog, so each part keeps only its
TOPMOST contiguous block of rows (the cog) and drops anything below the
gap (the caption).

    python3 scripts/art/split_cog_sheet.py [outdir]

Default outdir is ``viewer/art``; ``viewer/tools/build_atlas.py``
reads the sprites from there and bakes them into the atlas. The derived
PNGs are committed — CI does not regenerate art.
"""

import os
import sys
from collections import deque

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "source", "cogs_sheet.png")
SEATS = ["cog_ash.png", "cog_basil.png"]
SIZE = 128
TOL = 70  # colour distance from the backdrop that still counts as backdrop
MIN_RUN = 20


def key_background(img):
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    # median of the border is robust to corner smudges in the render
    border = [px[x, y][:3] for x in range(w) for y in (0, h - 1)] + \
        [px[x, y][:3] for y in range(h) for x in (0, w - 1)]
    bg = tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))

    def near(p):
        return sum((a - b) ** 2 for a, b in zip(p[:3], bg)) ** 0.5 <= TOL

    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            q.append((x, y))
    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or seen[y * w + x]:
            continue
        seen[y * w + x] = 1
        if not near(px[x, y]):
            continue
        px[x, y] = (0, 0, 0, 0)
        q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    # soften the keyed edge: fade pixels still tinted toward the backdrop
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a and g > r + 40 and g > b + 40 and abs(g - bg[1]) < 30 \
                    and abs(r - bg[0]) < 40:
                px[x, y] = (r, g, b, 0)
    return img


def _runs(flags, minimum):
    out, start = [], None
    for i, on in enumerate(list(flags) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= minimum:
                out.append((start, i))
            start = None
    return out


def drop_caption(part):
    """Keep the topmost contiguous block of rows: the cog, not its label."""
    alpha = part.getchannel("A")
    w, h = part.size
    rows = [any(alpha.getpixel((x, y)) for x in range(w)) for y in range(h)]
    blocks = _runs(rows, 4)
    if not blocks:
        return part
    return part.crop((0, blocks[0][0], w, blocks[0][1]))


def split(img, want):
    alpha = img.getchannel("A")
    w, h = img.size
    cols = [any(alpha.getpixel((x, y)) for y in range(h)) for x in range(w)]
    runs = _runs(cols, MIN_RUN)
    assert len(runs) == want, f"expected {want} cogs, found {len(runs)}: {runs}"
    out = []
    for x0, x1 in runs:
        part = img.crop((x0, 0, x1, h))
        part = part.crop(part.getbbox())
        part = drop_caption(part)
        part = part.crop(part.getbbox())
        side = max(part.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(part, ((side - part.width) // 2, side - part.height))
        out.append(square.resize((SIZE, SIZE), Image.LANCZOS))
    return out


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(HERE)), "viewer", "art")
    os.makedirs(outdir, exist_ok=True)
    sprites = split(key_background(Image.open(SRC)), len(SEATS))
    for name, sprite in zip(SEATS, sprites):
        sprite.save(os.path.join(outdir, name))
    print("cog sprites written to", outdir)


if __name__ == "__main__":
    main()
