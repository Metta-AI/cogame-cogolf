"""``/bin/cogolf-player`` — ONE image, one entrypoint, env-switched.

The policy is chosen at startup, in this order:

1. ``PLAYER_SCRIPTED=<literalist|pedant>`` -> that scripted baseline. Any
   other value is a FATAL startup error (exit 1): a typo must never
   silently become an LLM seat.
2. Otherwise the registered native Messages profile is selected. Its sidecar
   endpoint is required; missing configuration never becomes a baseline.

``python -m players.main``
"""

from __future__ import annotations

import os
import sys

from players.client import Policy, run_policy_main
from players.llm_player import LLMPolicy
from players.scripted import ScriptedPolicy, UnknownBaseline


def choose_policy() -> Policy:
    scripted = os.environ.get("PLAYER_SCRIPTED", "").strip()
    if scripted:
        return ScriptedPolicy(scripted)
    prompt = os.environ.get("PLAYER_PROMPT")
    return LLMPolicy(strategy=prompt)


def main() -> int:
    try:
        policy = choose_policy()
    except UnknownBaseline as exc:
        print(f"cogolf-player: {exc}", file=sys.stderr, flush=True)
        return 1
    return run_policy_main(lambda: policy)


if __name__ == "__main__":
    sys.exit(main())
