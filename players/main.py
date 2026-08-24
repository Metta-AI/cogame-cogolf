"""``/bin/cogolf-player`` — ONE image, one entrypoint, env-switched.

The policy is chosen at startup, in this order:

1. ``PLAYER_SCRIPTED=<literalist|pedant>`` -> that scripted baseline. Any
   other value is a FATAL startup error (exit 1): a typo must never
   silently become an LLM seat.
2. else ``PLAYER_PROMPT`` set, or a provider is detectable
   (``AWS_BEARER_TOKEN_BEDROCK`` / ``AWS_ENDPOINT_URL_BEDROCK_RUNTIME`` /
   ``ANTHROPIC_API_KEY``) -> the LLM policy, with ``PLAYER_PROMPT``
   appended to the system preamble as the policy's strategy paragraph.
3. else -> ``literalist``, so a credential-less CI or local run still
   plays a full, legal episode.

``python -m players.main``
"""

from __future__ import annotations

import os
import sys

from players.client import Policy, run_policy_main
from players.llm_player import LLMPolicy, _provider_from_env
from players.scripted import ScriptedPolicy, UnknownBaseline


def choose_policy() -> Policy:
    scripted = os.environ.get("PLAYER_SCRIPTED", "").strip()
    if scripted:
        return ScriptedPolicy(scripted)
    prompt = os.environ.get("PLAYER_PROMPT", "").strip()
    provider = _provider_from_env()
    if prompt or provider != "none":
        print(f"cogolf-player: LLM policy (provider {provider}, "
              f"prompt {'set' if prompt else 'unset'})",
              file=sys.stderr, flush=True)
        return LLMPolicy(strategy=prompt)
    print("cogolf-player: no PLAYER_SCRIPTED, no PLAYER_PROMPT and no LLM "
          "provider; playing the literalist baseline",
          file=sys.stderr, flush=True)
    return ScriptedPolicy("literalist")


def main() -> int:
    try:
        policy = choose_policy()
    except UnknownBaseline as exc:
        print(f"cogolf-player: {exc}", file=sys.stderr, flush=True)
        return 1
    return run_policy_main(lambda: policy)


if __name__ == "__main__":
    sys.exit(main())
