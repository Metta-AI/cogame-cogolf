"""Game version (GV): bumps whenever what a policy sees or how scores are
computed changes. Stamped into the replay header (``game_version``), the
``welcome`` message and the ``/global`` status snapshot.

Changelog (prepend-only; shape ``GVnn (short rule name): HEADLINE``):

GV01 (cogolf-v1): nine holes, zero-sum cross-fire scoring — each hole one
    ambiguous spec, both seats submit an impl plus up to five tests, tests
    cross-fire, a hidden reference gates legality and a hidden 4-case par
    suite audits both impls.
"""

GAME_VERSION = "GV01"  # GV01 (cogolf-v1): nine holes, zero-sum cross-fire scoring
