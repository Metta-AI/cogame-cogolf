# viewer/assets — the cogolf sprite atlas

`atlas.png` + `atlas.json` are the sprites the static replay viewer draws
(`replay-viewer/cogolf_replay.nim` preloads this directory into the wasm
filesystem as `assets/`). They are **generated**, not downloaded:

- everything geometric — the fortress bricks and their cracked and crumbled
  states, the darts, the shield ring, the sand splash, the parchment scroll, the
  pin flags, the tees, the grass and stone tiles, the seat pennants — is drawn
  deterministically with Pillow by `viewer/tools/build_atlas.py`;
- the two seat characters `cog|ash` and `cog|basil` are **nano-banana**
  (Gemini `gemini-2.5-flash-image`) renders of the Softmax cog, one kit per
  seat, produced by `scripts/art/split_cog_sheet.py` from the committed source
  sheet `scripts/art/source/cogs_sheet.png` and read from `viewer/art/`.

No third-party game art is downloaded, committed or shipped.

Regenerate:

```sh
python3 scripts/art/split_cog_sheet.py     # source sheet -> viewer/art/cog_*.png
python3 viewer/tools/build_atlas.py        # -> viewer/assets/atlas.{png,json}
```

Manifest contract: `{"tile_px": 32, "sprites": [{name, dir, x, y, w, h, cx,
cy}], "by_name": {"<name>|<dir>": index}}` — `cx, cy` is the pixel of the
sprite's anchor inside it (its centre, or the middle of its base for things that
stand on the ground). Keys in use:

```
brick|<seat>   brick_cracked|<seat>   debris|<1..3>
dart|<seat>-east   dart|<seat>-west   dart|par
shield|<seat>  splash|                parchment|<left|mid|right>
flag|<seat>    pennant|<seat>         tee|<seat>    cog|<seat>
ground|grass-<1..4>                   ground|stone-<1..3>
```
