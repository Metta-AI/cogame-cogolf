## cogame-cogolf static replay renderer (Nim -> wasm).
##
## Forked from cogame-factorio's `replay-viewer/factorio_replay.nim`: same
## Bitworld sprite-packet emission (bitworld/spriteprotocol) that
## `client/broadcast_core.js` draws unchanged, same `{.exportc.}` surface
## renamed `factorio_*` -> `cogolf_*`, same stage-note / profile plumbing.
## The parser and the scene builder are cogolf's.
##
## The arena is FIXED: a 40 x 22-tile stage (two stone code-fortresses, two
## tees, one parchment scroll) that always fits the frame, so there is no
## zoom, no minimap and no camera. The page owns playback (which beat) and
## tells this module through the text-command channel (`0x81`): `b:<beat>`.
## Chrome JSON rides the reserved sprite 4090's label, as in ctf.
##
## Export surface: cogolf_set_atlas, cogolf_load_replay, cogolf_frame,
## cogolf_input, cogolf_packet_ptr/len, cogolf_error_ptr/len,
## cogolf_stage_ptr/len, cogolf_profile_ptr/len.

import
  std/[json, tables, strutils, math, hashes, sets, monotimes, times],
  bitworld/spriteprotocol, pixie

const
  ChromeSpriteId = 4090        ## label carries chrome JSON (ctf convention)
  MapLayerId = 0
  MapLayerType = 0
  ZoomableFlag = 1
  BandObjectBase = 40          ## static terrain bands: ids 40..99 in the client
  MaxBands = 40
  StaticBandZ = -32768
  AtlasSpriteBase = 100        ## wire sprite id = base + atlas index
  GenSpriteBase = 3000         ## generated sprites (rings, marks)
  DynObjectBase = 200
  Tile = 32
  BoardTilesW = 40
  BoardTilesH = 22
  BoardW = BoardTilesW * Tile  ## 1280 px
  BoardH = BoardTilesH * Tile  ## 704 px
  BrickPx = 64                 ## one 2x2-tile block of a fortress
  BricksPerRow = 3
  BricksPerFortress = 9        ## 5 possible incoming shots + 4 audit darts
  FortressY = 352
  FortressX = [96, BoardW - 96 - BrickPx * BricksPerRow]
  TeeX = [416, BoardW - 416]
  TeeY = 570
  GroundY = 576
  ScrollX0 = 64
  ScrollX1 = 1152
  AnimFrames = 17              ## ~0.7 s of flight at the 24 packet/s rate
  Seats = 2
  SeatDirs = ["ash", "basil"]

type
  Rgba = object
    ## Straight-alpha RGBA pixel buffer (what the wire wants).
    w, h: int
    data: seq[uint8]

  AtlasEntry = object
    name, dir: string
    x, y, w, h, cx, cy: int

  Beat = object
    kind: string
    hole: int
    slot: int
    target: int
    idx: int
    outcome: string
    parFails: int
    score: array[Seats, int]
    cumulative: array[Seats, int]

  Replay = object
    beats: seq[Beat]
    names: seq[string]
    aliases: seq[string]
    holes: int

  Scene = object
    ## The board state at one beat.
    hole: int
    broken: array[Seats, int]     ## bricks crumbled on this seat's fortress
    cumulative: array[Seats, int]
    shooter: int                  ## -1 when no dart is in flight
    target: int
    outcome: string
    parSeat: int                  ## -1 unless this beat is a par audit
    parFails: int

var
  runtimeLoaded = false
  replay: Replay
  packet: seq[uint8]
  lastError: string
  atlas: Rgba
  atlasHalf: Rgba
  atlasFromHost = false
  atlasEntries: seq[AtlasEntry]
  atlasByName: Table[string, int]
  atlasSent: seq[bool]
  bandsEmitted = false
  genSprites: Table[string, int]
  nextGenSprite = GenSpriteBase
  objectIds: Table[string, int]
  nextObjectId = DynObjectBase
  liveObjects: HashSet[int]
  curBeat = 0
  animLeft = 0
  dirty = true

## --- Progress stage note (see factorio_replay.nim: survives an
## ABORTING_MALLOC abort so JS can report what the runtime was doing) ---
var
  stageNote: array[192, char]
  stageNoteLen: int
  currentStage: string
  profileLine: string
  profileT0: MonoTime
  profileLast: MonoTime

proc stampStage(stage: string) =
  let now = getMonoTime()
  if currentStage.len > 0:
    profileLine.add currentStage & "=" & $((now - profileLast).inMilliseconds) & "ms "
  profileLast = now
  currentStage = stage
  stageNoteLen = min(stage.len, stageNote.len)
  if stageNoteLen > 0:
    copyMem(stageNote[0].addr, stage[0].unsafeAddr, stageNoteLen)

proc bytesFromPointer(data: ptr uint8, length: int): string =
  result = newString(length)
  if length > 0:
    copyMem(result[0].addr, data, length)

# ---------------------------------------------------------------------------
# Pixel buffers

proc newRgba(w, h: int): Rgba =
  Rgba(w: w, h: h, data: newSeq[uint8](w * h * 4))

proc fill(dst: var Rgba, r, g, b, a: uint8) =
  var i = 0
  while i < dst.data.len:
    dst.data[i] = r; dst.data[i + 1] = g; dst.data[i + 2] = b; dst.data[i + 3] = a
    i += 4

proc blit(dst: var Rgba, src: Rgba, sx0, sy0, sw, sh, dx, dy: int) =
  ## Source-over blit of the src rect (sx0, sy0, sw, sh) at (dx, dy).
  for y in 0 ..< sh:
    let ty = dy + y
    if ty < 0 or ty >= dst.h: continue
    let syy = sy0 + y
    if syy < 0 or syy >= src.h: continue
    for x in 0 ..< sw:
      let tx = dx + x
      if tx < 0 or tx >= dst.w: continue
      let sxx = sx0 + x
      if sxx < 0 or sxx >= src.w: continue
      let si = (syy * src.w + sxx) * 4
      let sa = int(src.data[si + 3])
      if sa == 0: continue
      let di = (ty * dst.w + tx) * 4
      if sa == 255:
        dst.data[di] = src.data[si]
        dst.data[di + 1] = src.data[si + 1]
        dst.data[di + 2] = src.data[si + 2]
        dst.data[di + 3] = 255
      else:
        let da = int(dst.data[di + 3])
        let outA = sa + da * (255 - sa) div 255
        if outA == 0: continue
        for c in 0 .. 2:
          let sc = int(src.data[si + c])
          let dc = int(dst.data[di + c])
          dst.data[di + c] = uint8((sc * sa + dc * da * (255 - sa) div 255) div outA)
        dst.data[di + 3] = uint8(outA)

proc crop(src: Rgba, x, y, w, h: int): Rgba =
  result = newRgba(w, h)
  for yy in 0 ..< h:
    copyMem(result.data[yy * w * 4].addr,
            src.data[((y + yy) * src.w + x) * 4].unsafeAddr, w * 4)

proc imageToStraightRgba(image: Image): Rgba =
  ## pixie images are premultiplied RGBX; the wire is straight RGBA.
  result = newRgba(image.width, image.height)
  for i in 0 ..< image.width * image.height:
    let px = image.data[i]
    let a = int(px.a)
    let o = i * 4
    if a == 0:
      continue
    result.data[o] = uint8(min(255, int(px.r) * 255 div a))
    result.data[o + 1] = uint8(min(255, int(px.g) * 255 div a))
    result.data[o + 2] = uint8(min(255, int(px.b) * 255 div a))
    result.data[o + 3] = uint8(a)

# ---------------------------------------------------------------------------
# Atlas

proc loadAtlas() =
  ## Manifest from the preloaded FS; pixels from the host when it decoded
  ## assets/atlas.png natively (cogolf_set_atlas), else decoded here.
  stampStage("load atlas manifest")
  atlasEntries.setLen(0)
  atlasByName.clear()
  let manifest = parseJson(readFile("assets/atlas.json"))
  for item in manifest["sprites"]:
    atlasEntries.add AtlasEntry(
      name: item["name"].getStr, dir: item["dir"].getStr,
      x: item["x"].getInt, y: item["y"].getInt,
      w: item["w"].getInt, h: item["h"].getInt,
      cx: item["cx"].getInt, cy: item["cy"].getInt)
  for key, idx in manifest["by_name"]:
    atlasByName[key] = idx.getInt
  if not atlasFromHost:
    stampStage("decode atlas png")
    atlas = imageToStraightRgba(decodeImage(readFile("assets/atlas.png")))
    atlasHalf = Rgba()
  atlasSent = newSeq[bool](atlasEntries.len)

proc atlasIndex(name, dir: string): int =
  ## -1 when the atlas has no such sprite.
  atlasByName.getOrDefault(name & "|" & dir, -1)

proc atlasSpriteId(idx: int): int =
  ## Wire sprite id for an atlas entry, defining it on first use.
  result = AtlasSpriteBase + idx
  if not atlasSent[idx]:
    let e = atlasEntries[idx]
    let px = atlas.crop(e.x, e.y, e.w, e.h)
    packet.addSprite(result, px.w, px.h, px.data, e.name & "|" & e.dir)
    atlasSent[idx] = true

proc genSpriteId(key: string, build: proc (): Rgba): int =
  ## Wire id for a generated sprite, built + defined once per key.
  if key in genSprites:
    return genSprites[key]
  result = nextGenSprite
  inc nextGenSprite
  genSprites[key] = result
  let px = build()
  packet.addSprite(result, px.w, px.h, px.data, key)

# ---------------------------------------------------------------------------
# Replay parsing

proc getI(node: JsonNode, key: string, dflt: int): int =
  if node == nil or node.kind != JObject: return dflt
  let v = node{key}
  if v == nil: return dflt
  case v.kind
  of JInt: int(v.getInt)
  of JFloat: int(v.getFloat)
  else: dflt

proc getS(node: JsonNode, key: string, dflt: string): string =
  if node == nil or node.kind != JObject: return dflt
  let v = node{key}
  if v == nil or v.kind != JString: return dflt
  v.getStr

proc parsePair(node: JsonNode): array[Seats, int] =
  if node != nil and node.kind == JArray and node.len >= Seats:
    for i in 0 ..< Seats:
      let v = node[i]
      result[i] = (if v.kind == JFloat: int(v.getFloat) else: v.getInt)

proc parseReplay(text: string): Replay =
  let doc = parseJson(text)
  if doc{"format"}.getStr != "cogame-cogolf-replay":
    raise newException(ValueError, "not a cogame-cogolf replay (format=" &
      doc{"format"}.getStr & ")")
  let names = doc{"names"}
  if names != nil and names.kind == JArray:
    for n in names: result.names.add n.getStr
  let aliases = doc{"aliases"}
  if aliases != nil and aliases.kind == JArray:
    for n in aliases: result.aliases.add n.getStr
  while result.names.len < Seats: result.names.add "seat " & $result.names.len
  while result.aliases.len < Seats: result.aliases.add SeatDirs[result.aliases.len]
  let events = doc{"events"}
  if events == nil or events.kind != JArray:
    raise newException(ValueError, "replay has no events")
  for ev in events:
    var beat = Beat(kind: ev.getS("kind", ""), hole: ev.getI("hole", 0),
                    slot: ev.getI("slot", -1), target: ev.getI("target_slot", -1),
                    idx: ev.getI("idx", 0), outcome: ev.getS("outcome", ""),
                    parFails: ev.getI("par_fails", 0))
    beat.score = parsePair(ev{"score"})
    beat.cumulative = parsePair(ev{"cumulative"})
    if beat.kind.len > 0:
      result.beats.add beat
  if result.beats.len == 0:
    raise newException(ValueError, "replay has no beats")
  let holes = doc{"holes"}
  result.holes = (if holes != nil and holes.kind == JArray: holes.len else: 0)
  if result.holes == 0:
    for beat in result.beats:
      if beat.hole > result.holes: result.holes = beat.hole

proc sceneAt(upto: int): Scene =
  ## Replay the beat stream to `upto` (inclusive) and read off the board.
  result.hole = 0
  result.shooter = -1
  result.target = -1
  result.parSeat = -1
  let last = min(upto, replay.beats.len - 1)
  for i in 0 .. last:
    let beat = replay.beats[i]
    case beat.kind
    of "hole_start":
      result.hole = beat.hole
      result.broken = [0, 0]
    of "test_verdict":
      if i == last and beat.slot >= 0 and beat.target >= 0:
        result.shooter = beat.slot
        result.target = beat.target
        result.outcome = beat.outcome
      if beat.outcome == "breach" and beat.target >= 0 and beat.target < Seats:
        if result.broken[beat.target] < BricksPerFortress:
          result.broken[beat.target] = result.broken[beat.target] + 1
    of "par_result":
      if i == last and beat.slot >= 0:
        result.parSeat = beat.slot
        result.parFails = beat.parFails
      if beat.slot >= 0 and beat.slot < Seats:
        var left = beat.parFails
        while left > 0 and result.broken[beat.slot] < BricksPerFortress:
          result.broken[beat.slot] = result.broken[beat.slot] + 1
          dec left
    of "hole_score":
      result.cumulative = beat.cumulative
    else:
      discard

# ---------------------------------------------------------------------------
# Terrain bake -> static bands

proc drawAtlasAt(dst: var Rgba, idx: int, cxPx, cyPx: int) =
  ## Draws atlas sprite idx with its anchor at board px (cxPx, cyPx).
  if idx < 0: return
  let e = atlasEntries[idx]
  dst.blit(atlas, e.x, e.y, e.w, e.h, cxPx - e.cx, cyPx - e.cy)

proc tileHash(x, y, salt: int): int =
  var h: Hash = 0
  h = h !& x !& (y * 7919) !& (salt * 104729)
  result = abs(!$h)

proc bakeArena(): Rgba =
  ## Dusk links: a graded sky, a grass floor, two stone platforms, two sand
  ## bunkers and the parchment scroll across the top.
  stampStage("bake arena (" & $BoardW & "x" & $BoardH & ")")
  result = newRgba(BoardW, BoardH)
  for y in 0 ..< GroundY:
    let t = y.float / GroundY.float
    let r = uint8(30.0 + 58.0 * t)
    let g = uint8(26.0 + 34.0 * t)
    let b = uint8(46.0 + 22.0 * t)
    for x in 0 ..< BoardW:
      let i = (y * BoardW + x) * 4
      result.data[i] = r; result.data[i + 1] = g; result.data[i + 2] = b
      result.data[i + 3] = 255
  stampStage("bake ground")
  var grassIdx: seq[int]
  for i in 1 .. 4:
    let idx = atlasIndex("ground", "grass-" & $i)
    if idx >= 0: grassIdx.add idx
  var stoneIdx: seq[int]
  for i in 1 .. 3:
    let idx = atlasIndex("ground", "stone-" & $i)
    if idx >= 0: stoneIdx.add idx
  for ty in (GroundY div Tile) ..< BoardTilesH:
    for tx in 0 ..< BoardTilesW:
      let h = tileHash(tx, ty, 1)
      if grassIdx.len > 0:
        result.drawAtlasAt(grassIdx[h mod grassIdx.len],
                           tx * Tile + Tile div 2, ty * Tile + Tile div 2)
      else:
        for y in ty * Tile ..< ty * Tile + Tile:
          for x in tx * Tile ..< tx * Tile + Tile:
            let i = (y * BoardW + x) * 4
            result.data[i] = 58; result.data[i + 1] = 84; result.data[i + 2] = 54
  # Stone platforms under the fortresses.
  stampStage("bake platforms")
  for seat in 0 ..< Seats:
    let x0 = FortressX[seat]
    let y0 = FortressY + BrickPx * BricksPerRow
    for ty in 0 ..< 2:
      for tx in -1 .. BricksPerRow * 2:
        let px = x0 + tx * Tile
        let py = y0 + ty * Tile
        if px < 0 or px + Tile > BoardW or py + Tile > BoardH: continue
        let h = tileHash(tx, ty, 2 + seat)
        if stoneIdx.len > 0:
          result.drawAtlasAt(stoneIdx[h mod stoneIdx.len],
                             px + Tile div 2, py + Tile div 2)
  # Sand bunkers in front of each fortress (where illegal darts drop short).
  let splashIdx = atlasIndex("splash", "")
  if splashIdx >= 0:
    for seat in 0 ..< Seats:
      let bx = (if seat == 0: FortressX[0] + BrickPx * BricksPerRow + 72
                else: FortressX[1] - 72)
      result.drawAtlasAt(splashIdx, bx, GroundY - 6)
  # The parchment scroll across the top.
  stampStage("bake scroll")
  let leftIdx = atlasIndex("parchment", "left")
  let midIdx = atlasIndex("parchment", "mid")
  let rightIdx = atlasIndex("parchment", "right")
  if leftIdx >= 0 and midIdx >= 0 and rightIdx >= 0:
    let midW = atlasEntries[midIdx].w
    let capW = atlasEntries[leftIdx].w
    let midY = atlasEntries[midIdx].h div 2 + 8
    result.drawAtlasAt(leftIdx, ScrollX0 + capW div 2, midY)
    var x = ScrollX0 + capW
    while x + midW <= ScrollX1 - capW:
      result.drawAtlasAt(midIdx, x + midW div 2, midY)
      x += midW
    result.drawAtlasAt(rightIdx, ScrollX1 - capW div 2, midY)
  # Tees.
  for seat in 0 ..< Seats:
    let idx = atlasIndex("tee", SeatDirs[seat])
    if idx >= 0: result.drawAtlasAt(idx, TeeX[seat], TeeY + 8)

proc emitBands() =
  ## Slices the baked arena into <= MaxBands horizontal band sprites placed
  ## as static objects (ids 40.., z = -32768) — the layout
  ## broadcast_core.js bakes once into its base canvas.
  let arena = bakeArena()
  stampStage("emit arena bands")
  var bandH = max(Tile, (BoardH + MaxBands - 1) div MaxBands)
  bandH = ((bandH + Tile - 1) div Tile) * Tile
  var y = 0
  var band = 0
  while y < BoardH and band < MaxBands:
    let h = min(bandH, BoardH - y)
    packet.addSprite(BandObjectBase + band, BoardW, h,
      arena.data.toOpenArray(y * BoardW * 4, (y + h) * BoardW * 4 - 1),
      "arena band " & $band)
    packet.addObject(BandObjectBase + band, 0, y, StaticBandZ, MapLayerId,
                     BandObjectBase + band)
    y += h
    inc band
  bandsEmitted = true

# ---------------------------------------------------------------------------
# Dynamic objects

proc objectIdFor(key: string): int =
  if key in objectIds:
    return objectIds[key]
  result = nextObjectId
  inc nextObjectId
  objectIds[key] = result

proc place(seen: var HashSet[int], key: string, x, y, z, spriteId: int) =
  let id = objectIdFor(key)
  seen.incl id
  liveObjects.incl id
  packet.addObject(id, clamp(x, -32000, 32000), clamp(y, -32000, 32000),
                   clamp(z, -32000, 32000), MapLayerId, spriteId)

proc placeAtlas(seen: var HashSet[int], key: string, idx, cx, cy, z: int) =
  if idx < 0: return
  let e = atlasEntries[idx]
  seen.place(key, cx - e.cx, cy - e.cy, z, atlasSpriteId(idx))

proc flashSprite(colour: array[3, uint8]): int =
  genSpriteId("flash|" & $colour[0] & "-" & $colour[1] & "-" & $colour[2],
    proc (): Rgba =
      var px = newRgba(BrickPx, BrickPx)
      px.fill(colour[0], colour[1], colour[2], 120)
      px)

proc emitScene() =
  ## Places every object of the current beat; deletes what left the board.
  let scene = sceneAt(curBeat)
  var seen: HashSet[int]
  # Fortresses: 9 bricks each, intact / cracked / crumbled.
  for seat in 0 ..< Seats:
    let brickIdx = atlasIndex("brick", SeatDirs[seat])
    let crackedIdx = atlasIndex("brick_cracked", SeatDirs[seat])
    for i in 0 ..< BricksPerFortress:
      let row = i div BricksPerRow
      let col = i mod BricksPerRow
      let cx = FortressX[seat] + col * BrickPx + BrickPx div 2
      let cy = FortressY + row * BrickPx + BrickPx div 2
      let key = "brick|" & $seat & "|" & $i
      let gone = i < scene.broken[seat]
      if gone:
        # crumbled: a heap of debris where the block stood
        let idx = atlasIndex("debris", $((i mod 3) + 1))
        seen.placeAtlas("debris|" & $seat & "|" & $i, idx, cx,
                        FortressY + BricksPerRow * BrickPx - 12, 900 + i)
      elif i == scene.broken[seat] and scene.shooter >= 0 and
           scene.target == seat and scene.outcome == "breach":
        seen.placeAtlas(key, crackedIdx, cx, cy, 100 + i)
      else:
        seen.placeAtlas(key, brickIdx, cx, cy, 100 + i)
  # Pin flags: height tracks the cumulative score.
  for seat in 0 ..< Seats:
    let idx = atlasIndex("flag", SeatDirs[seat])
    let lift = clamp(scene.cumulative[seat], -9, 9) * 8
    seen.placeAtlas("flag|" & $seat, idx,
                    FortressX[seat] + BrickPx * BricksPerRow div 2,
                    FortressY - 10 - lift, 1200)
    let pennantIdx = atlasIndex("pennant", SeatDirs[seat])
    seen.placeAtlas("pennant|" & $seat, pennantIdx,
                    FortressX[seat] + BrickPx * BricksPerRow - 16,
                    FortressY - 6, 1190)
  # The two cogs, standing at their tees.
  for seat in 0 ..< Seats:
    let idx = atlasIndex("cog", SeatDirs[seat])
    seen.placeAtlas("cog|" & $seat, idx, TeeX[seat], TeeY, 2000 + seat)
  # The shot of this beat.
  if scene.shooter >= 0 and scene.target >= 0:
    let t = 1.0 - float(animLeft) / float(AnimFrames)
    let fromX = TeeX[scene.shooter]
    let fromY = TeeY - 60
    var toX = FortressX[scene.target] + BrickPx * BricksPerRow div 2
    var toY = FortressY + BrickPx
    if scene.outcome == "illegal":
      toX = (if scene.target == 0: FortressX[0] + BrickPx * BricksPerRow + 72
             else: FortressX[1] - 72)
      toY = GroundY - 16
    let x = int(float(fromX) + (float(toX) - float(fromX)) * t)
    let arc = int(110.0 * sin(PI * t))
    let y = int(float(fromY) + (float(toY) - float(fromY)) * t) - arc
    let facing = (if scene.shooter == 0: "east" else: "west")
    let idx = atlasIndex("dart", SeatDirs[scene.shooter] & "-" & facing)
    if t < 1.0:
      seen.placeAtlas("dart|flight", idx, x, y, 2500)
    else:
      case scene.outcome
      of "held":
        seen.placeAtlas("ring|hit", atlasIndex("shield", SeatDirs[scene.target]),
                        toX, toY, 2600)
      of "breach":
        seen.place("flash|hit", toX - BrickPx div 2, toY - BrickPx div 2, 2600,
                   flashSprite([224'u8, 82'u8, 58'u8]))
      of "illegal":
        seen.placeAtlas("splash|hit", atlasIndex("splash", ""), toX,
                        GroundY - 4, 2600)
      else:
        discard
  # Par audit: four grey darts falling from the scroll onto the fortress.
  if scene.parSeat >= 0:
    let idx = atlasIndex("dart", "par")
    let t = 1.0 - float(animLeft) / float(AnimFrames)
    for i in 0 ..< 4:
      let cx = FortressX[scene.parSeat] + 24 + i * 48
      let y0 = 120
      let y1 = FortressY - 8
      let y = int(float(y0) + (float(y1) - float(y0)) * min(1.0, t + float(i) * 0.08))
      seen.placeAtlas("par|" & $i, idx, cx, y, 2400 + i)
  # Delete what is no longer on the board.
  var gone: seq[int]
  for id in liveObjects:
    if id notin seen: gone.add id
  for id in gone:
    packet.addDeleteObject(id)
    liveObjects.excl id

proc chromeJson(): string =
  let scene = sceneAt(curBeat)
  var j = %*{
    "kind": "cogolf",
    "beat": clamp(curBeat, 0, max(0, replay.beats.len - 1)),
    "beats": replay.beats.len,
    "hole": scene.hole,
    "holes": replay.holes,
    "cumulative": [scene.cumulative[0], scene.cumulative[1]],
    "board": {"tile": Tile, "w": BoardW, "h": BoardH},
    "names": replay.names,
    "aliases": replay.aliases,
  }
  $j

proc renderCurrent() =
  packet.setLen(0)
  if not bandsEmitted:
    packet.addLayer(MapLayerId, MapLayerType, ZoomableFlag)
    packet.addViewport(MapLayerId, BoardW, BoardH)
    emitBands()
  if dirty:
    stampStage("render beat")
    emitScene()
    dirty = false
  # Chrome JSON on the reserved sprite (1x1, transparent), every frame.
  let chrome = chromeJson()
  packet.addSprite(ChromeSpriteId, 1, 1, [0'u8, 0, 0, 0], chrome)

# ---------------------------------------------------------------------------
# Exports

proc cogolfSetAtlas(data: ptr uint8, width, height, half: cint): cint
    {.exportc: "cogolf_set_atlas", cdecl.} =
  ## Host-decoded straight-alpha RGBA pixels of assets/atlas.png (the Worker
  ## decodes it with createImageBitmap, which beats an in-wasm PNG inflate).
  ## Must precede cogolf_load_replay; without it the runtime decodes the PNG
  ## itself. `half != 0` is accepted and ignored: the cogolf arena is a fixed
  ## 40x22 board that always renders at 32 px/tile.
  try:
    if width <= 0 or height <= 0 or data == nil: return 0
    if half != 0: return 1
    var px = newRgba(int(width), int(height))
    copyMem(px.data[0].addr, data, px.data.len)
    atlas = px
    atlasHalf = Rgba()
    atlasFromHost = true
    return 1
  except Exception:
    return 0

proc cogolfLoadReplay(data: ptr uint8, length: cint): cint
    {.exportc: "cogolf_load_replay", cdecl.} =
  try:
    lastError = ""
    runtimeLoaded = false
    profileLine = ""
    profileT0 = getMonoTime()
    profileLast = profileT0
    currentStage = ""
    if atlasEntries.len == 0:
      loadAtlas()
    stampStage("parse replay")
    replay = parseReplay(data.bytesFromPointer(int(length)))
    bandsEmitted = false
    curBeat = 0
    animLeft = 0
    dirty = true
    for i in 0 ..< atlasSent.len: atlasSent[i] = false
    genSprites.clear()
    nextGenSprite = GenSpriteBase
    objectIds.clear()
    liveObjects.clear()
    nextObjectId = DynObjectBase
    stampStage("render first frame")
    renderCurrent()
    stampStage("loaded")
    profileLine.add "total=" & $((getMonoTime() - profileT0).inMilliseconds) &
      "ms packet=" & $packet.len & "B"
    runtimeLoaded = true
    return 1
  except Exception as error:
    runtimeLoaded = false
    lastError = currentStage & ": " & error.msg & "\n" & error.getStackTrace()
    return 0

proc applyCommand(text: string) =
  ## `b:<beat>` selects a beat (`s:` is accepted as an alias).
  if text.startsWith("b:") or text.startsWith("s:"):
    let v = try: parseInt(text[2 .. ^1]) except ValueError: -1
    if v >= 0 and v != curBeat:
      let clamped = clamp(v, 0, max(0, replay.beats.len - 1))
      # One beat forward is playback: animate the dart. A scrub snaps.
      animLeft = (if clamped == curBeat + 1: AnimFrames else: 0)
      curBeat = clamped
      dirty = true

proc cogolfInput(data: ptr uint8, length: cint)
    {.exportc: "cogolf_input", cdecl.} =
  if not runtimeLoaded: return
  try:
    for item in data.bytesFromPointer(int(length)).parseSpriteClientMessages():
      if item.kind == SpriteClientChatMessage:
        applyCommand(item.text)
  except Exception:
    discard

proc cogolfFrame(): cint {.exportc: "cogolf_frame", cdecl.} =
  if not runtimeLoaded:
    return 0
  try:
    if animLeft > 0:
      dec animLeft
      dirty = true
    renderCurrent()
    return 1
  except Exception as error:
    lastError = "render beat: " & error.msg & "\n" & error.getStackTrace()
    return -1

proc cogolfPacketPointer(): ptr uint8 {.exportc: "cogolf_packet_ptr", cdecl.} =
  if packet.len == 0: nil else: packet[0].addr

proc cogolfPacketLength(): cint {.exportc: "cogolf_packet_len", cdecl.} =
  cint(packet.len)

proc cogolfErrorPointer(): ptr uint8 {.exportc: "cogolf_error_ptr", cdecl.} =
  if lastError.len == 0: nil else: cast[ptr uint8](lastError[0].addr)

proc cogolfErrorLength(): cint {.exportc: "cogolf_error_len", cdecl.} =
  cint(lastError.len)

proc cogolfProfilePointer(): ptr uint8 {.exportc: "cogolf_profile_ptr", cdecl.} =
  ## Load-time profile of the last cogolf_load_replay ("stage=Nms ...").
  if profileLine.len == 0: nil else: cast[ptr uint8](profileLine[0].addr)

proc cogolfProfileLength(): cint {.exportc: "cogolf_profile_len", cdecl.} =
  cint(profileLine.len)

proc cogolfStagePointer(): ptr uint8 {.exportc: "cogolf_stage_ptr", cdecl.} =
  if stageNoteLen == 0: nil else: cast[ptr uint8](stageNote[0].addr)

proc cogolfStageLength(): cint {.exportc: "cogolf_stage_len", cdecl.} =
  cint(stageNoteLen)

when defined(emscripten):
  proc emscriptenExitWithLiveRuntime() {.
    importc: "emscripten_exit_with_live_runtime", cdecl.}

when isMainModule and defined(emscripten):
  # Nim's main would run every module-global destructor on return while JS
  # keeps calling into the module. Exit with the runtime alive so the
  # globals live for the page's lifetime.
  emscriptenExitWithLiveRuntime()

when isMainModule and not defined(emscripten):
  # Native smoke: `nim c -r replay-viewer/cogolf_replay.nim <replay.json>`
  # (the wasm32 truth is tools/wasm_replay_smoke.cjs).
  import std/os
  if paramCount() >= 1:
    setCurrentDir(currentSourcePath().parentDir().parentDir() / "viewer")
    let bytes = readFile(paramStr(1))
    let ok = cogolfLoadReplay(cast[ptr uint8](bytes[0].unsafeAddr), cint(bytes.len))
    if ok != 1:
      echo "load failed: ", lastError
      quit(1)
    echo "first packet ", packet.len, " bytes; board ", BoardW, "x", BoardH
    for i in 0 ..< 3:
      let cmd = "b:" & $(i + 1)
      var msg: seq[uint8]
      msg.add 0x81'u8
      msg.addU16(cmd.len)
      for ch in cmd: msg.add uint8(ord(ch))
      cogolfInput(msg[0].addr, cint(msg.len))
      doAssert cogolfFrame() == 1, lastError
      echo "frame ", i, " packet ", packet.len, " bytes"
