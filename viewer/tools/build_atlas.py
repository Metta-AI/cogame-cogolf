#!/usr/bin/env python3
"""Builds ``viewer/assets/atlas.{png,json}`` — the cogolf sprite sheet.

Everything geometric is drawn deterministically here with Pillow: the
stone bricks of the two code-fortresses, their cracked and crumbled
states, the darts (one per seat and per direction, plus the grey audit
dart), the shield ring, the sand splash, the parchment scroll, the pin
flags, the tees, the grass and stone tiles and the two seat pennants.

This script does NOT own the two seat characters. ``cog|ash`` and
``cog|basil`` are nano-banana renders of the Softmax cog (one kit per
seat) produced by ``scripts/art/split_cog_sheet.py`` from
``scripts/art/source/cogs_sheet.png`` and read from
``viewer/art/cog_<seat>.png``; they are only packed here. If those
files are missing the script draws a procedural stand-in and says so, so
the bundle always builds — but the committed atlas is the nano-banana one.

No Factorio/Wube art is downloaded, committed or shipped.

    python3 viewer/tools/build_atlas.py [--out viewer/assets]

Manifest contract (read by ``replay-viewer/cogolf_replay.nim``):
``{"tile_px": 32, "sprites": [{name, dir, x, y, w, h, cx, cy}],
"by_name": {"<name>|<dir>": index}}`` where ``cx, cy`` is the pixel of the
sprite's anchor (its centre, or the middle of its base for things that
stand on the ground).
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw

TILE = 32
SHEET_WIDTH = 1024

REPO_ROOT = Path(__file__).resolve().parents[2]
ART_DIR = REPO_ROOT / "viewer" / "art"

SEATS = ("ash", "basil")
SEAT_TINT = {"ash": (232, 163, 61), "basil": (63, 124, 196)}
STONE = (126, 118, 104)
STONE_DARK = (86, 80, 70)
STONE_LIGHT = (162, 152, 136)
PAPER = (232, 216, 184)
PAPER_EDGE = (188, 168, 128)
SAND = (214, 190, 138)
GRASS = (58, 84, 54)
GRASS_LIGHT = (74, 104, 66)
INK = (42, 31, 22)


def _rgba(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _shade(colour, amount):
    return tuple(max(0, min(255, int(c + amount))) for c in colour)


# -- fortress masonry ---------------------------------------------------------

def brick(seat: str, cracked: bool) -> Image.Image:
    """One 2x2-tile block of a code-fortress, in that seat's tint."""
    size = TILE * 2
    img = _rgba((size, size))
    d = ImageDraw.Draw(img)
    tint = SEAT_TINT[seat]
    body = tuple((s * 3 + t) // 4 for s, t in zip(STONE, tint))
    d.rounded_rectangle([1, 1, size - 2, size - 2], radius=4,
                        fill=body, outline=_shade(body, -46), width=2)
    # three courses of masonry, offset like real brickwork
    course = size // 3
    for row in range(3):
        y = 2 + row * course
        d.line([(3, y + course - 2), (size - 4, y + course - 2)],
               fill=_shade(body, -34), width=1)
        offset = 0 if row % 2 == 0 else course
        for x in range(offset, size, course * 2):
            if 3 < x < size - 4:
                d.line([(x, y), (x, y + course - 3)],
                       fill=_shade(body, -34), width=1)
    d.line([(3, 3), (size - 4, 3)], fill=_shade(body, 40), width=1)
    if cracked:
        rng = random.Random(hash(seat) & 0xFFFF)
        x = size // 2
        points = [(x, 2)]
        for step in range(1, 7):
            x += rng.randint(-6, 6)
            points.append((max(4, min(size - 5, x)), 2 + step * (size - 6) // 6))
        d.line(points, fill=_shade(body, -70), width=3, joint="curve")
        d.line([(points[3][0], points[3][1]),
                (points[3][0] + 12, points[3][1] + 9)],
               fill=_shade(body, -70), width=2)
    return img


def debris(variant: int) -> Image.Image:
    """A chip of crumbled masonry."""
    img = _rgba((TILE, TILE))
    d = ImageDraw.Draw(img)
    rng = random.Random(1000 + variant)
    for _ in range(3 + variant):
        x = rng.randint(2, TILE - 10)
        y = rng.randint(2, TILE - 10)
        w = rng.randint(4, 9)
        h = rng.randint(4, 9)
        shade = _shade(STONE, rng.randint(-40, 30))
        d.polygon([(x, y + h), (x + w // 2, y), (x + w, y + h // 2),
                   (x + w - 2, y + h)], fill=shade,
                  outline=_shade(shade, -40))
    return img


# -- projectiles and their outcomes ------------------------------------------

def dart(seat: str, facing: str) -> Image.Image:
    """A test fired at the opposing fortress: a slim fletched dart."""
    length, height = 48, 14
    img = _rgba((length, height))
    d = ImageDraw.Draw(img)
    tint = SEAT_TINT[seat]
    mid = height // 2
    d.line([(6, mid), (length - 10, mid)], fill=_shade(tint, -20), width=3)
    d.polygon([(length - 12, mid - 5), (length - 1, mid), (length - 12, mid + 5)],
              fill=_shade(tint, 40), outline=INK)
    d.polygon([(2, mid - 6), (12, mid), (2, mid + 6)],
              fill=_shade(tint, -60), outline=INK)
    if facing == "west":
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img


def grey_par_dart() -> Image.Image:
    """A grey audit dart, falling from the scroll onto a fortress."""
    length, width = 40, 14
    img = _rgba((width, length))
    d = ImageDraw.Draw(img)
    grey = (150, 146, 138)
    mid = width // 2
    d.line([(mid, 4), (mid, length - 10)], fill=_shade(grey, -20), width=3)
    d.polygon([(mid - 5, length - 12), (mid, length - 1), (mid + 5, length - 12)],
              fill=_shade(grey, 40), outline=INK)
    d.polygon([(mid - 6, 2), (mid, 11), (mid + 6, 2)],
              fill=_shade(grey, -50), outline=INK)
    return img


def shield_ring(seat: str) -> Image.Image:
    """The ping of a test that was DEFLECTED: a bright ring."""
    size = TILE * 2
    img = _rgba((size, size))
    d = ImageDraw.Draw(img)
    tint = SEAT_TINT[seat]
    for i, alpha in ((0, 210), (4, 130), (8, 60)):
        d.ellipse([4 + i, 4 + i, size - 5 - i, size - 5 - i],
                  outline=tint + (alpha,), width=3)
    return img


def splash() -> Image.Image:
    """Sand thrown up by a dart that dropped short into the bunker."""
    img = _rgba((TILE * 2, TILE))
    d = ImageDraw.Draw(img)
    rng = random.Random(77)
    d.ellipse([6, TILE - 14, TILE * 2 - 7, TILE - 2], fill=SAND)
    for _ in range(14):
        x = rng.randint(8, TILE * 2 - 9)
        y = rng.randint(2, TILE - 12)
        r = rng.randint(1, 3)
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=_shade(SAND, rng.randint(-20, 25)))
    return img


# -- the scroll, the flags, the tees, the ground -----------------------------

def parchment(part: str) -> Image.Image:
    """The spec scroll: a left cap, a repeating middle, a right cap."""
    w = TILE * 2 if part in ("left", "right") else TILE * 4
    h = TILE * 3
    img = _rgba((w, h))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 6, w - 1, h - 7], fill=PAPER, outline=PAPER_EDGE)
    for y in range(10, h - 10, 9):
        d.line([(4, y), (w - 5, y)], fill=_shade(PAPER, -16), width=1)
    if part == "left":
        d.rounded_rectangle([0, 0, 13, h - 1], radius=6,
                            fill=_shade(PAPER_EDGE, -30), outline=INK)
        d.line([(6, 6), (6, h - 7)], fill=_shade(PAPER_EDGE, 30), width=2)
    if part == "right":
        d.rounded_rectangle([w - 14, 0, w - 1, h - 1], radius=6,
                            fill=_shade(PAPER_EDGE, -30), outline=INK)
        d.line([(w - 7, 6), (w - 7, h - 7)], fill=_shade(PAPER_EDGE, 30),
               width=2)
    return img


def flag(seat: str) -> Image.Image:
    """A pin flag whose height tracks the cumulative score."""
    w, h = TILE, TILE * 2
    img = _rgba((w, h))
    d = ImageDraw.Draw(img)
    tint = SEAT_TINT[seat]
    d.line([(w // 2, 2), (w // 2, h - 2)], fill=(228, 222, 210), width=3)
    d.polygon([(w // 2 + 2, 3), (w - 2, 12), (w // 2 + 2, 21)],
              fill=tint, outline=INK)
    return img


def pennant(seat: str) -> Image.Image:
    """The seat pennant flown over its keep."""
    w, h = TILE, TILE + TILE // 2
    img = _rgba((w, h))
    d = ImageDraw.Draw(img)
    tint = SEAT_TINT[seat]
    d.polygon([(2, 2), (w - 3, 2), (w - 3, h - 12), (w // 2, h - 3),
               (2, h - 12)], fill=tint, outline=INK)
    d.ellipse([w // 2 - 5, 8, w // 2 + 5, 18], outline=INK, width=2)
    return img


def tee(seat: str) -> Image.Image:
    """The little mound a seat fires from."""
    w, h = TILE * 2, TILE
    img = _rgba((w, h))
    d = ImageDraw.Draw(img)
    d.ellipse([0, h // 2, w - 1, h - 1], fill=GRASS_LIGHT,
              outline=_shade(GRASS, -18))
    d.ellipse([w // 4, h // 2 + 2, w - w // 4, h - 6],
              fill=_shade(SEAT_TINT[seat], -70))
    return img


def ground(kind: str, variant: int) -> Image.Image:
    img = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    rng = random.Random(hash((kind, variant)) & 0xFFFF)
    base = GRASS if kind == "grass" else STONE_DARK
    d.rectangle([0, 0, TILE - 1, TILE - 1],
                fill=_shade(base, rng.randint(-8, 8)))
    for _ in range(18 if kind == "grass" else 10):
        x = rng.randint(0, TILE - 1)
        y = rng.randint(0, TILE - 1)
        if kind == "grass":
            d.line([(x, y), (x, y - rng.randint(2, 5))],
                   fill=_shade(GRASS_LIGHT, rng.randint(-14, 18)))
        else:
            d.point((x, y), fill=_shade(STONE, rng.randint(-30, 30)))
    return img


def cog(seat: str) -> tuple[Image.Image, bool]:
    """The seat character: a nano-banana render of the Softmax cog."""
    path = ART_DIR / f"cog_{seat}.png"
    if path.is_file():
        return Image.open(path).convert("RGBA").resize((96, 96),
                                                       Image.LANCZOS), True
    # Fallback only: a procedural rig, so the bundle still builds if the
    # render is missing. The committed atlas is the nano-banana one.
    img = _rgba((96, 96))
    d = ImageDraw.Draw(img)
    tint = SEAT_TINT[seat]
    d.rounded_rectangle([26, 30, 70, 74], radius=8, fill=tint, outline=INK,
                        width=2)
    d.rounded_rectangle([32, 12, 64, 38], radius=6, fill=_shade(tint, 25),
                        outline=INK, width=2)
    d.rectangle([38, 20, 58, 30], fill=(24, 30, 36))
    d.ellipse([42, 23, 46, 27], fill=(180, 240, 255))
    d.ellipse([50, 23, 54, 27], fill=(180, 240, 255))
    d.ellipse([28, 74, 46, 92], fill=(46, 42, 38), outline=INK)
    d.ellipse([50, 74, 68, 92], fill=(46, 42, 38), outline=INK)
    return img, False


# -- packing ------------------------------------------------------------------

def build() -> tuple[Image.Image, dict, bool]:
    sprites: list[tuple[str, str, Image.Image, tuple[int, int]]] = []

    def add(name: str, direction: str, img: Image.Image, anchor=None):
        if anchor is None:
            anchor = (img.width // 2, img.height // 2)
        sprites.append((name, direction, img, anchor))

    real_cogs = True
    for seat in SEATS:
        add("brick", seat, brick(seat, False))
        add("brick_cracked", seat, brick(seat, True))
        add("dart", f"{seat}-east", dart(seat, "east"))
        add("dart", f"{seat}-west", dart(seat, "west"))
        add("shield", seat, shield_ring(seat))
        add("flag", seat, flag(seat), (TILE // 2, TILE * 2 - 2))
        add("pennant", seat, pennant(seat), (TILE // 2, TILE + TILE // 2 - 2))
        add("tee", seat, tee(seat), (TILE, TILE - 4))
        image, real = cog(seat)
        real_cogs = real_cogs and real
        add("cog", seat, image, (48, 94))
    add("dart", "par", grey_par_dart())
    add("splash", "", splash(), (TILE, TILE - 2))
    for variant in range(1, 4):
        add("debris", str(variant), debris(variant))
    for part in ("left", "mid", "right"):
        add("parchment", part, parchment(part))
    for variant in range(1, 5):
        add("ground", f"grass-{variant}", ground("grass", variant))
    for variant in range(1, 4):
        add("ground", f"stone-{variant}", ground("stone", variant))

    # shelf packing, tallest-first inside each row
    pad = 2
    x = y = row_h = 0
    placed = []
    for name, direction, img, anchor in sprites:
        if x + img.width + pad > SHEET_WIDTH:
            x = 0
            y += row_h + pad
            row_h = 0
        placed.append((name, direction, img, anchor, x, y))
        x += img.width + pad
        row_h = max(row_h, img.height)
    height = y + row_h + pad

    sheet = Image.new("RGBA", (SHEET_WIDTH, height), (0, 0, 0, 0))
    entries = []
    by_name = {}
    for index, (name, direction, img, anchor, px, py) in enumerate(placed):
        sheet.paste(img, (px, py))
        entries.append({"name": name, "dir": direction, "x": px, "y": py,
                        "w": img.width, "h": img.height,
                        "cx": anchor[0], "cy": anchor[1]})
        by_name[f"{name}|{direction}"] = index
    return sheet, {"tile_px": TILE, "sprites": entries, "by_name": by_name}, \
        real_cogs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "viewer" / "assets"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sheet, manifest, real_cogs = build()
    sheet.save(out / "atlas.png", optimize=True)
    (out / "atlas.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    size = os.path.getsize(out / "atlas.png")
    print(f"atlas: {sheet.width}x{sheet.height}, "
          f"{len(manifest['sprites'])} sprites, {size} bytes "
          f"({'nano-banana cogs' if real_cogs else 'PROCEDURAL cog fallback'})")
    if size > 200 * 1024:
        print("warning: atlas is over the 200 KB budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
