# Private native decisions and teacher collection

The ordinary language player uses native Messages through `COWORLD_LLM_ENDPOINT`.
A missing endpoint fails startup. The player cannot use direct Anthropic,
Bedrock, or a provider credential fallback. Version 2 player images must be
refreshed together with the game; version 1 packets are not accepted.

Each engine-issued decision window contains the exact private observation and
frozen native model, decoder and prompt profile. Registration is recorded player
input; it establishes no serving identity. The engine independently parses
the selected received completion and compares it with the separately submitted
control. The sandbox then installs sanitized controls and produces legality,
cross-fire, hidden audit and zero-sum scoring effects.

`COGAME_SAVE_TRAJECTORY_URI` selects a private absolute `file://` destination.
The runtime must supply `COWORLD_EPISODE_ID`, `COWORLD_SOURCE_REVISION`, and
`COWORLD_GAME_VERSION`; `COWORLD_GAME_IMAGE_DIGEST` records the actual image when
available. Collection does not infer source or serving identity from player text.

The private `.partial` spool preserves issued windows, received byte prefixes,
actual headers and engine applications. A final complete-episode JSONL file is
written only after the engine and every admitted player owner join. An unresolved
owner retains its writable partial evidence and prevents final/public completion.
No final archive is inferred from cancellation or a requested stop.

Native model attempts preserve exact requests, raw response strings, received
body bytes, platform call identifiers, served identity and actual sampling
metadata. Thinking remains private auxiliary evidence. Missing tokens or
probabilities remain absent. HTTP fixtures establish protocol behavior, not an
authenticated platform archive or model strength.

The native profile explicitly defaults to temperature `0.7`, top-p `1`, and
1,800 output tokens. This profile is new: the original provider request omitted
temperature and top-p. `COWORLD_LLM_TEMPERATURE` freezes an explicit override
before the first private window. Captured `full_softmax` evidence must carry
the exact requested temperature; stored `full_softmax_temperature_one` evidence
retains its original fields and requires temperature `1`. Greedy responses
retain no invented draw probabilities.

## Source-controlled teachers

```sh
uv sync --frozen
PYTHONPATH=server:. uv run --no-sync python tools/export_posttrain.py --game-version 0.2.0 \
  /tmp/cogolf-duel-private 10 --variant duel
PYTHONPATH=server:. uv run --no-sync python tools/export_posttrain.py --game-version 0.2.0 \
  /tmp/cogolf-blitz-private 10 --variant blitz
```

The collector runs complete games with the shipped `literalist` and `pedant`
teachers and the ordinary sandbox. It retains ordinary pacing and both seats.
The teacher reads only the private observation. Its text must round-trip through
the ordinary parser to the actual installed control. Hidden-reference and audit
permutation tests protect that visibility boundary.

The output contains private complete episodes and an unreviewed manifest, with
owner-only permissions. It does not emit training labels or choose a split.
Content-bound external source review is required before scripted labels can enter
Metta post-training. Provider-backed labels additionally require modern
platform-authenticated receipts and immutable episode/participant context.
Whole games with the same game and seed remain in one split across variants.

The shared Metta training path is SLIME. Configure a real saved checkpoint,
tokenizer, chat template and sampling policy through its trusted native gateway.
Baseline and trained evaluations must use the same game profile and budgets.
These source changes alone establish no trained strength or production rollout.

## Numeric research bridge

The four-choice numeric catalog combines the two baseline implementations and
test suites. It trains baseline selection, not arbitrary ordinary language code
submission. Historical numeric checkpoints and corpora retain their stored
contracts. They do not establish native language runtime parity or qualified
modern text labels.

The bridge defaults to `--mode language`: exact ordinary native prompt rendering,
source observation-only teacher text, parser and sandbox-installed controls.
It is a local decision bridge, without platform receipts or live native inference.
`--mode numeric` explicitly selects the historical four-choice research catalog.
Neither mode alone qualifies a hosted rollout or supervised corpus.

The publish template is versionless: Coworld inserts the build's `--version`.
Teacher collection requires `--game-version` matching that intended build, for
example `0.2.0`. The private runtime configuration separately records rule
version `GV02`. These describe different contracts.
