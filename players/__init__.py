"""cogame-cogolf player side: websocket harness + policies.

``players.main`` is the single entrypoint (``/bin/cogolf-player``): one
image, one binary, the policy chosen by environment variable.
``players.client`` holds the shared wire code, ``players.llm_player`` the
Claude policy and ``players.scripted`` the scripted baselines.
"""
