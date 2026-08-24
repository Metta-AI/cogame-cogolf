import std/[os, strformat, strutils]

let rootDir = currentSourcePath().parentDir().parentDir()
let distDir = rootDir / "replay-viewer" / "dist"

if not dirExists(distDir):
  mkDir(distDir)

switch("nimcache", distDir / "nimcache")
switch("threads", "off")
--define:release
when defined(emscripten):
  --os:linux
  --cpu:wasm32
  --cc:clang
  --clang.exe:emcc
  --clang.linkerexe:emcc
  --clang.cpp.exe:emcc
  --clang.cpp.linkerexe:emcc
  --mm:arc
  --exceptions:goto
  --define:noSignalHandler
  --define:useMalloc
  switch(
    "passL",
    (&"""
    -o {distDir / "cogolf_replay.js"}
    --preload-file {rootDir / "viewer" / "assets"}@assets
    -O2
    -s ALLOW_MEMORY_GROWTH
    -s ABORTING_MALLOC=1
    -s FILESYSTEM=1
    -s ENVIRONMENT=web,worker,node
    -s EXPORTED_RUNTIME_METHODS=HEAPU8,FS
    -s EXPORTED_FUNCTIONS=_main,_malloc,_free,_cogolf_set_atlas,_cogolf_load_replay,_cogolf_frame,_cogolf_input,_cogolf_packet_ptr,_cogolf_packet_len,_cogolf_error_ptr,_cogolf_error_len,_cogolf_stage_ptr,_cogolf_stage_len,_cogolf_profile_ptr,_cogolf_profile_len
    """).replace("\n", " ")
  )
