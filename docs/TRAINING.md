# Metta post-training data

The exporter runs complete Cogolf matches with the shipped `literalist` and
`pedant` players, production hole engine, and real sandbox. Each turn records
the model-facing prompt built by the hosted player and its scripted reply.
The reply passes the game's submission validator. Both certified variants are
supported.

```sh
uv sync --frozen
PYTHONPATH=server:. uv run --no-sync python tools/export_posttrain.py \
  /tmp/cogolf-duel-dataset 10 --variant duel
PYTHONPATH=server:. uv run --no-sync python tools/export_posttrain.py \
  /tmp/cogolf-blitz-dataset 10 --variant blitz
```

`train.jsonl` and `validation.jsonl` split complete games by seed. The exporter
requires ten games and refuses to overwrite an existing directory. Local
collection sets the inter-hole pacing delay to zero; scoring and sandbox
execution still use the production engine.

From a Metta checkout with the post-training package installed:

```sh
uv run --package metta-posttrain --extra train python -m metta_posttrain.train \
  --dataset /tmp/cogolf-duel-dataset \
  --output /tmp/cogolf-adapter --model Qwen/Qwen3-0.6B \
  --max-steps 100 --max-length 4096
```

Ten seeded duel games exported 144 train and 36 validation examples; blitz
exported 80 and 20. All fit a 4,096-token context. One CPU optimizer step
reduced four-example validation loss from 1.69997 to 1.69402 for duel and
from 1.70176 to 1.69579 for blitz. These checks verify the training path;
they do not establish stronger play than the scripted teachers.
