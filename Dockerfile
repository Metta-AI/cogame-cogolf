# cogame-cogolf Coworld image: game server + bundled players + the static
# replay viewer, in three stages.
#
# Stage 1 (wasm-builder) compiles the static replay viewer (Nim ->
# emscripten, viewer/build_viewer.sh -> viewer/dist) with the pinned
# toolchain; it is linux/amd64 (nimby's release binary) and its wasm output
# is architecture-independent.
#
# Stage 2 (player) is the policy image: python:3.11-slim + aiohttp + the
# Claude SDKs + players/ + the stdlib-only half of the server package
# (contract, specs, baseline) that the scripted policies and the harness
# fallback need. ONE image, ONE entrypoint (/bin/cogolf-player), the policy
# chosen by PLAYER_SCRIPTED / PLAYER_PROMPT.
#
# Stage 3 (game, the default target) is the game image: python:3.11-slim +
# aiohttp + server/ + players/ + the built viewer/dist. There is no external
# engine: cogolf's "engine" is a sandboxed Python test-runner inside this
# container, so there is no Factorio, no FLE, no RCON and no 520 MiB
# download. The repo layout is preserved at /workspace (the server resolves
# viewer/dist relative to the repo root; PYTHONPATH covers server/ and
# players/), so the project is NOT pip-installed into site-packages.
#
# Entrypoints (Coworld manifest `run`, tools/ci/docker_smoke.sh,
# tools/ci/policies.json):
#   game    /bin/cogolf         -> python -m cogame_cogolf.server
#   player  /bin/cogolf-player  -> python -m players.main
#
# Build: docker build --platform=linux/amd64 -t cogame-cogolf:local .

# Static replay viewer: the emsdk 4.0.15 + nimby 0.1.27 + Nim 2.2.4 +
# nimby.lock recipe, running viewer/build_viewer.sh -> viewer/dist.
FROM emscripten/emsdk:4.0.15 AS wasm-builder

RUN apt-get update && \
  apt-get install -y --no-install-recommends ca-certificates curl git && \
  rm -rf /var/lib/apt/lists/* && \
  curl -fsSL \
    -o /usr/local/bin/nimby \
    https://github.com/treeform/nimby/releases/download/0.1.27/nimby-Linux-X64 && \
  echo "3b3084394bd26b09f84a3f82389f075221c8784893238390939d71dd66ac9e8b  /usr/local/bin/nimby" | sha256sum -c - && \
  chmod +x /usr/local/bin/nimby && \
  nimby use 2.2.4

ENV PATH="/root/.nimby/nim/bin:$PATH"

WORKDIR /workspace
COPY nimby.lock .
RUN nimby --global sync nimby.lock

COPY replay-viewer/ replay-viewer/
COPY client/ client/
COPY viewer/ viewer/
RUN bash viewer/build_viewer.sh && test -f viewer/dist/index.html


# ---------------------------------------------------------------------------
# Player image (manifest `player[]` roles + every policy in
# tools/ci/policies.json). ONE image, env-switched:
#   PLAYER_SCRIPTED=<literalist|pedant>   the scripted baselines
#   PLAYER_PROMPT="..."                   the LLM policy, prompt = strategy
FROM python:3.11-slim AS player

WORKDIR /workspace
RUN pip install --no-cache-dir "aiohttp>=3.10" "anthropic>=0.40" "boto3>=1.35"
COPY players/ players/
COPY server/cogame_cogolf/ server/cogame_cogolf/
RUN printf '#!/bin/sh\nexec python -m players.main "$@"\n' > /bin/cogolf-player && \
    chmod +x /bin/cogolf-player
ENV PYTHONPATH="/workspace/server:/workspace" \
    PYTHONUNBUFFERED=1
CMD ["/bin/cogolf-player"]


# ---------------------------------------------------------------------------
# Game image (default target): the server, the sandbox, the bundled players
# and the static replay viewer bundle.
FROM python:3.11-slim AS game

WORKDIR /workspace

RUN pip install --no-cache-dir "aiohttp>=3.10" && \
    useradd --system --uid 4242 --no-create-home --shell /usr/sbin/nologin cogolf

COPY server/ server/
COPY players/ players/
COPY --from=wasm-builder /workspace/viewer/dist/ viewer/dist/

RUN printf '#!/bin/sh\nexec python -m cogame_cogolf.server "$@"\n' > /bin/cogolf && \
    printf '#!/bin/sh\nexec python -m players.main "$@"\n' > /bin/cogolf-player && \
    chmod +x /bin/cogolf /bin/cogolf-player

ENV PYTHONPATH="/workspace/server:/workspace" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COGOLF_SANDBOX_UID=4242

CMD ["/bin/cogolf"]
