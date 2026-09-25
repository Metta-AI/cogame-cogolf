"""Jev ranks complete legal submissions over the ordinary Cogolf seat view."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from players.client import MAX_NOTE_CHARS, Policy
from players.scripted import scripted_submission

DEFAULT_TIMEOUT_SECONDS = 24.0  # inside Blitz's 30-second hole deadline


def _best_choice(answer: dict, choices: dict[str, str]) -> str:
    if answer["type"] != "choice" or set(answer["probabilities"]) != set(choices):
        raise ValueError("Jev returned an invalid candidate set")
    confidence = answer["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("Jev returned an invalid confidence")
    probabilities = answer["probabilities"]
    if any(not isinstance(p, (int, float)) or not 0 <= p <= 1
           for p in probabilities.values()):
        raise ValueError("Jev returned an invalid probability")
    if abs(sum(probabilities.values()) - 1) > len(choices) * 0.005 + 1e-6:
        raise ValueError("Jev probabilities do not sum to one")
    return max(choices, key=probabilities.__getitem__)


class JevPolicy(Policy):
    """Select the literalist or pedant program for this hole with System One."""

    def submission(self, hole: int, observation: dict) -> dict:
        candidates = {
            name: scripted_submission(name, hole, observation)
            for name in ("literalist", "pedant")
        }
        sidecar = os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "").strip()
        key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not sidecar and not key:
            return candidates["literalist"]
        endpoint = sidecar or os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
        model = (os.environ.get("BEDROCK_MODEL", "") if sidecar else
                 os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest"))
        choices = {
            "literalist": "Use the literalist implementation and its tests.",
            "pedant": "Use the pedant implementation and its tests.",
        }
        state = json.dumps({"observation": observation, "candidates": candidates},
                           separators=(",", ":"))
        body = {
            "model": model,
            "state": "Choose the submission most likely to score against this "
                     "spec and the visible match history. Return a legal "
                     "candidate for this hole.\n" + state,
            "questions": {"submission": {
                "type": "choice", "instructions": "Which complete submission wins?",
                "criteria": choices,
            }},
        }
        headers = {"content-type": "application/json"}
        if key and not sidecar:
            headers["authorization"] = f"Bearer {key}"
        else:
            headers["x-coworld-player-slot"] = str(observation["you"]["slot"])
        request = Request(endpoint.rstrip("/") + "/v1/systemone",
                          data=json.dumps(body).encode(), headers=headers,
                          method="POST")
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            answer = json.load(response)["answers"]["submission"]
        chosen = _best_choice(answer, choices)
        result = candidates[chosen].copy()
        result["note"] = (f"Jev chose {chosen}; " + result["note"])[:MAX_NOTE_CHARS]
        return result
