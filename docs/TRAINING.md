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

## Numeric training

`tools/train_bridge.py` exposes the exact hosted seat observation and four
choices: the published `literalist` or `pedant` implementation paired with
either baseline's tests. Both seats choose before the production engine and
sandbox resolve a hole. Each decision has 35 numeric features, and complete
matches return zero-sum score and utility values. This catalog trains baseline
selection; the post-training path above supports arbitrary code submissions.

```sh
uv run python tools/test_train_bridge.py
```

From a Metta checkout with the training stack installed, use the game bridge
command with either the native PufferLib recipe or the Metta RL recipe:

```python
from recipes.external.coworld import train as puffer_train
from recipes.external.coworld_metta_rl import train as metta_rl_train

command = ["python", "tools/train_bridge.py", "coworld_manifest_template.json", "duel"]
puffer = puffer_train(command=command, players=2)
metta_rl = metta_rl_train(command=command, players=2)
```

Use `blitz` in place of `duel` for the five-hole variant. Set the command paths
to absolute paths when invoking either recipe outside this checkout.
