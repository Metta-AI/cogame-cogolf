"""Source-owned native prompt profile shared by players and engine windows."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from players.native import Request

MAX_PROMPT_CHARS = 6000
MAX_HISTORY_HOLES = 4
MAX_HISTORY_ENTRY_CHARS = 1200
MAX_API_DOCS_CHARS = 12000

SYSTEM_PREAMBLE = """You are one of two code agents playing cogolf, a nine-hole adversarial-programming match.
Each hole you get one deliberately ambiguous spec. You must reply with ONE implementation of
`solve(...)` and up to 5 test cases. Your tests are fired at your opponent's implementation; their
tests are fired at yours. A hidden reference implementation decides every ambiguous clause: a test of
yours only counts if the reference agrees with it, and a hidden 4-case audit runs against your code
every hole. You score `(your breaching tests + their audit failures) - (their breaching tests + your
audit failures)`. So: implement the reading a careful author most likely meant, and aim your tests at
the clauses where a careless reader would diverge from that reading.
REPLY FORMAT - your reply MUST BEGIN WITH `{` and be a single JSON object:
`{"impl": "def solve(...):\\n    ...", "tests": [{"name": "...", "args": [...], "expect": ..., "why": "..."}], "note": "..."}`.
`impl` is Python source (stdlib only, no imports of socket/subprocess/ctypes/multiprocessing, no file
or network access, no infinite loops - each call gets 1 second of CPU). `args` is the argument LIST
for one `solve(*args)` call and `expect` is the exact JSON value it must return. `why` is one short
sentence naming the clause you are testing. Emit no prose outside the JSON object."""


class PromptProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    strategy: str = ""
    alias: str = "?"
    api_docs: str = Field(default="", max_length=MAX_API_DOCS_CHARS)

    def system_blocks(self) -> list:
        preamble = SYSTEM_PREAMBLE
        if self.strategy:
            preamble = preamble + "\n\nYOUR STRATEGY: " + self.strategy
        blocks = [{"type": "text", "text": preamble}]
        if self.api_docs:
            block = {"type": "text", "text": self.api_docs}
            block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks

    def user_prompt(self, hole: int, observation: dict) -> str:
        spec = observation.get("spec") or {}
        you = observation.get("you") or {}
        opponent = observation.get("opponent") or {}
        rules = observation.get("rules") or {}
        lines = [
            (
                f"Hole {hole} of {observation.get('holes', '?')}. "
                f"You are {you.get('alias', self.alias)} on "
                f"{you.get('score', 0)}; {opponent.get('alias', 'your opponent')} "
                f"is on {opponent.get('score', 0)}."
            ),
            f"SPEC {spec.get('key', '?')} - {spec.get('title', '')}",
            str(spec.get("prompt", "")),
            "SIGNATURE: " + json.dumps(spec.get("signature") or {}),
            "WORKED EXAMPLES: " + json.dumps(spec.get("examples") or []),
            (
                f"You may submit up to {rules.get('max_tests_per_hole', 5)} tests; "
                f"impl at most {rules.get('max_impl_chars', 4000)} characters."
            ),
        ]
        history = list(observation.get("history") or [])[-MAX_HISTORY_HOLES:]
        if history:
            lines.append("HISTORY (most recent last):")
            for entry in history:
                rendered = json.dumps(entry, ensure_ascii=False)
                if len(rendered) > MAX_HISTORY_ENTRY_CHARS:
                    rendered = rendered[: MAX_HISTORY_ENTRY_CHARS - 1] + "\u2026"
                lines.append(rendered)
        lines.append("Reply with the single JSON object now. It must begin with {.")
        prompt = "\n\n".join(lines)
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[: MAX_PROMPT_CHARS - 1] + "\u2026"
        return prompt

    def request(
        self,
        hole: int,
        observation: dict,
        *,
        model: str,
        temperature: float,
        max_tokens: int = 1800,
    ) -> Request:
        return Request(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=1,
            system=self.system_blocks(),
            messages=[{"role": "user", "content": self.user_prompt(hole, observation)}],
        )
