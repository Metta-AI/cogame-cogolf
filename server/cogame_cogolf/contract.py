"""Wire contract constants for ``cogame.cogolf.v1`` — stdlib only.

Every string a policy depends on lives here so a policy container can
import this module (or vendor it) without pulling in the server's
third-party dependencies. NO third-party imports, ever.

Four-surface rename rule: renaming or adding any constant here must be
mirrored in (1) this module, (2) ``tests/contract_manifest.txt`` (the
golden list ``tests/test_contract.py`` compares against), (3)
``docs/PROTOCOL.md``, and (4) ``players/`` (the shared client harness
and baselines). A change that alters what a policy sees also bumps
``version.GAME_VERSION``.
"""

from __future__ import annotations

PROTOCOL = "cogame.cogolf.v1"

# Message `type` values -------------------------------------------------------
# server -> player
MSG_WELCOME = "welcome"
MSG_OBSERVATION = "observation"
MSG_DONE = "done"
# player -> server
MSG_SUBMISSION = "submission"
# server -> global viewer
MSG_STATUS = "status"
MSG_PROGRESS = "progress"

# In-game aliases: the ONLY identity a policy ever sees. Real player names
# are spectator-side only (replay `names`), never sent to a container.
ALIASES = ("Ash", "Basil")
SEATS = 2

# `welcome` keys ---------------------------------------------------------------
WELCOME_KEYS = (
    "type", "protocol", "game_version", "slot", "alias", "opponent_alias",
    "holes", "hole_deadline_seconds", "retry_deadline_seconds", "rules",
    "episode", "api_docs",
)
# `welcome.rules` / `observation.rules` (the same object)
RULES_KEYS = (
    "max_tests_per_hole", "max_impl_chars", "max_test_name_chars",
    "max_why_chars", "max_args_chars", "max_expect_chars", "max_note_chars",
    "max_message_bytes", "par_tests_per_hole", "call_cpu_seconds", "blocked",
)
# `welcome.episode`: episode parameters stated outright at t=0 (policies
# must never infer them from play).
EPISODE_KEYS = (
    "game_version", "seats", "slot", "holes", "deck", "deck_version", "seed",
    "scoring",
)

# `observation` message and its `observation` object -------------------------
OBSERVATION_MESSAGE_KEYS = ("type", "hole", "deadline_seconds", "retry",
                            "observation")
OBSERVATION_KEYS = ("hole", "holes", "spec", "you", "opponent", "history",
                    "rules")
SPEC_KEYS = ("key", "title", "prompt", "signature", "examples")
SEAT_VIEW_KEYS = ("alias", "slot", "score")
HISTORY_KEYS = ("hole", "spec_key", "hole_score", "your_tests", "their_tests",
                "their_note", "your_par_fails", "their_par_fails")

# `submission` reply ----------------------------------------------------------
SUBMISSION_KEYS = ("type", "hole", "impl", "tests", "note")
TEST_KEYS = ("name", "args", "expect", "why")

# Caps. Every truncation is on RUNE (unicode code point) boundaries.
MAX_MESSAGE_BYTES = 16384
MAX_IMPL_CHARS = 4000
MAX_TESTS_PER_HOLE = 5
MAX_TEST_NAME_CHARS = 40
MAX_WHY_CHARS = 120
MAX_ARGS_CHARS = 400
MAX_EXPECT_CHARS = 400
MAX_NOTE_CHARS = 200
MAX_OBSERVED_CHARS = 300
MAX_BROKEN_REASON_CHARS = 300
PAR_TESTS_PER_HOLE = 4

# Sandbox denial surface advertised in `welcome.rules.blocked`.
BLOCKED = ("socket", "subprocess", "ctypes", "multiprocessing", "threading",
           "file writes", "network")

# `done` message ----------------------------------------------------------------
DONE_KEYS = ("type", "result")

# Results document (closed schema; == manifest results_schema) ---------------
RESULT_KEYS = (
    "names", "aliases", "scores", "hole_scores", "breaches", "breaches_taken",
    "par_fails", "tests_fired", "illegal_tests", "holes_played", "fallbacks",
    "fallback_causes", "reason", "wall_clock_seconds", "seed", "deck_version",
    "killer_test",
)
REASONS = ("complete", "deadline", "harness_fault")
FALLBACK_CAUSES = ("timeout", "malformed", "oversize", "disconnected",
                   "host_error")
ILLEGAL_REASONS = ("arity", "not_json", "oversize", "ref_error", "ref_timeout",
                   "ref_mismatch", "duplicate")
SHOT_OUTCOMES = ("breach", "held", "illegal")
KILLER_TEST_KEYS = ("hole", "slot", "target_slot", "name", "why")

# Replay event vocabulary (one beat per event) --------------------------------
EVENT_KINDS = ("hole_start", "submission", "test_verdict", "par_result",
               "hole_score", "episode_end")

# Global viewer status snapshot -------------------------------------------------
STATUS_KEYS = ("type", "game_version", "aliases", "names", "holes", "hole",
               "scores", "done")
PROGRESS_KEYS = ("type", "hole", "scores", "killer")

# Runtime env vars ---------------------------------------------------------------
ENV_PLAYER_WS_URL = "COWORLD_PLAYER_WS_URL"
ENV_PLAYER_WS_URL_LEGACY = "COGAMES_ENGINE_WS_URL"
ENV_PLAYER_SCRIPTED = "PLAYER_SCRIPTED"
ENV_PLAYER_PROMPT = "PLAYER_PROMPT"
