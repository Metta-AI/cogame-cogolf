"""The manifest: the triple-synced closed schema, num_agents everywhere,
and the upload contract the `coworld` CLI enforces."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from cogame_cogolf import contract
from cogame_cogolf.config import KNOWN_KEYS
from cogame_cogolf.results import REASONS, RESULT_KEYS

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((REPO_ROOT / "coworld_manifest_template.json").read_text())
SMOKE = (REPO_ROOT / "tools" / "ci" / "docker_smoke.sh").read_text()
POLICIES = json.loads((REPO_ROOT / "tools" / "ci" / "policies.json").read_text())
SEATS = 2


def test_results_schema_is_in_triple_sync():
    schema = MANIFEST["game"]["results_schema"]
    assert set(schema["properties"]) == RESULT_KEYS
    assert set(schema["required"]) == RESULT_KEYS
    assert schema["additionalProperties"] is False
    assert tuple(schema["properties"]["reason"]["enum"]) == REASONS
    causes = schema["properties"]["fallback_causes"]["items"]
    assert set(causes["properties"]) == set(contract.FALLBACK_CAUSES)
    assert set(causes["required"]) == set(contract.FALLBACK_CAUSES)
    killer = schema["properties"]["killer_test"]
    assert set(killer["properties"]) == set(contract.KILLER_TEST_KEYS)


def test_the_docker_smoke_reads_the_same_result_keys():
    """docker_smoke.sh is the third surface of the closed schema: it reads
    results.reason and the two per-seat arrays it length-checks."""
    assert 'results.get("reason")' in SMOKE
    assert '("names", "scores")' in SMOKE
    assert "SMOKE_SEATS" in SMOKE and "num_agents" in SMOKE


def test_config_schema_matches_the_config_model():
    schema = MANIFEST["game"]["config_schema"]
    assert set(schema["properties"]) == KNOWN_KEYS
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"tokens", "players"}
    num_agents = schema["properties"]["num_agents"]
    assert num_agents["minimum"] == num_agents["maximum"] == SEATS


def test_num_agents_is_in_every_variant_and_the_fixture():
    for variant in MANIFEST["variants"]:
        config = variant["game_config"]
        assert config["num_agents"] == SEATS, variant["id"]
        assert len(config["players"]) == SEATS, variant["id"]
        assert variant["description"], variant["id"]
        assert variant["id"] and variant["name"]
    cert = MANIFEST["certification"]
    assert cert["game_config"]["num_agents"] == SEATS
    assert len(cert["game_config"]["players"]) == SEATS
    assert len(cert["players"]) == SEATS


def test_every_declared_player_can_be_seated_in_certification():
    """cogame-raid 0.1.2 -> 0.1.3: certification fails `players_missing`
    unless every manifest player entry occupies a slot in the fixture."""
    declared = {p["id"] for p in MANIFEST["player"]}
    seated = {p["player_id"] for p in MANIFEST["certification"]["players"]}
    assert declared == seated == {"literalist", "pedant"}


def test_the_upload_contract_of_coworld_0_1_42():
    assert MANIFEST["$schema"].startswith("https://")
    assert len(MANIFEST["tags"]) >= 3
    assert MANIFEST["episode_timeout_minutes"] == 20
    game = MANIFEST["game"]
    assert game["runnable"]["type"] == "game"
    assert game["runnable"]["run"] == ["/bin/cogolf"]
    assert game["replay_viewer"] == {"bundle": "static-replay-viewer"}
    assert set(game["protocols"]) == {"player", "global"}
    for protocol in game["protocols"].values():
        assert protocol["type"] == "uri" and "PROTOCOL.md" in protocol["value"]
    docs = game["docs"]
    assert docs["readme"]["value"].endswith("README.md")
    assert {page["id"] for page in docs["pages"]} == {"rules.md", "replay.md"}
    for page in docs["pages"]:
        assert page["title"] and page["content"]["type"] == "uri"
    for player in MANIFEST["player"]:
        assert {"id", "type", "name", "description", "image", "run", "env"} \
            <= set(player)
        assert player["run"] == ["/bin/cogolf-player"]
        assert player["env"]["PLAYER_SCRIPTED"] == player["id"]


def test_the_timing_arithmetic_fits_inside_the_play_budget():
    """episode_timeout_minutes 20 -> 1200 s; a game must settle inside 60 %
    of that. Worst case per hole: 40 + 15 s of decision time (ONE parallel
    batch, so the max of the two seats, not the sum) + 3 sandbox batches of
    6 s = 73 s; 9 x 73 + startup + artifacts = 680 s < 720 s."""
    schema = MANIFEST["game"]["config_schema"]["properties"]
    budget = MANIFEST["episode_timeout_minutes"] * 60 * 0.6
    hole = (schema["hole_deadline_seconds"]["default"]
            + schema["retry_deadline_seconds"]["default"]
            + 3 * schema["sandbox_batch_seconds"]["default"])
    holes = schema["holes"]["default"]
    assert hole * holes + 23 <= budget
    assert schema["wall_clock_budget_seconds"]["default"] <= budget
    for variant in MANIFEST["variants"]:
        assert variant["game_config"]["wall_clock_budget_seconds"] <= budget


def test_the_policy_set_is_two_prompt_champions_and_two_scripted_fillers():
    names = [p["name"] for p in POLICIES]
    assert names == ["cogolf-architect", "cogolf-sniper", "cogolf-literalist",
                     "cogolf-pedant"]
    prompts = [p for p in POLICIES if "PLAYER_PROMPT" in p["env"]]
    scripted = [p for p in POLICIES if "PLAYER_SCRIPTED" in p["env"]]
    assert len(prompts) == 2 and len(scripted) == 2
    # champion #2 is owned by daveey-1, so its version is uploaded as them
    assert prompts[1]["player"] == "ply_bac48eb1-662e-44f8-973d-f3e016dccf5d"
    assert "player" not in prompts[0]
    assert prompts[0]["env"]["PLAYER_PROMPT"] != prompts[1]["env"]["PLAYER_PROMPT"]
    for policy in POLICIES:
        assert policy["run"] == "/bin/cogolf-player"
        # one image, one entrypoint, env-switched
        assert policy["image"] == "cogame-cogolf-player:latest"
    assert {p["env"]["PLAYER_SCRIPTED"] for p in scripted} == {"literalist",
                                                               "pedant"}


def test_the_compose_services_back_the_manifest_placeholders():
    compose = (REPO_ROOT / "compose.yaml").read_text()
    assert "cogame-cogolf-game:latest" in compose
    assert "cogame-cogolf-player:latest" in compose
    assert "platform: linux/amd64" in compose
    assert "network: host" in compose
    assert MANIFEST["game"]["runnable"]["image"] == "{{GAME_IMAGE}}"
    for player in MANIFEST["player"]:
        assert player["image"] == "{{PLAYER_IMAGE}}"


def test_no_unsubstituted_scaffold_placeholders_survive():
    files = [".github/workflows/ci.yml", ".github/workflows/coworld-release.yml",
             ".github/workflows/coworld-submit.yml", "tools/ci/docker_smoke.sh",
             "tools/ci/policies.json"]
    for name in files:
        text = (REPO_ROOT / name).read_text()
        for placeholder in ("<slug>", "<IMAGE>", "<SEATS>"):
            assert placeholder not in text, f"{name} still has {placeholder}"


@pytest.mark.parametrize("name", ["tools/build_replay_viewer.sh",
                                  "tools/ci/docker_smoke.sh",
                                  "viewer/build_viewer.sh"])
def test_the_hooks_are_committed_executable(name):
    import os
    assert os.access(REPO_ROOT / name, os.X_OK), \
        f"{name} must be mode 100755 (git update-index --chmod=+x)"


def test_the_manifest_passes_the_cli_upload_validator():
    coworld_manifest = pytest.importorskip("coworld.manifest")
    built = json.loads(
        json.dumps(MANIFEST)
        .replace("{{GAME_IMAGE}}", "cogame-cogolf-game:latest")
        .replace("{{PLAYER_IMAGE}}", "cogame-cogolf-player:latest"))
    built["game"]["version"] = "0.1.0"
    coworld_manifest.validate_upload_manifest(built)


def test_the_variants_and_the_fixture_validate_against_the_config_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = MANIFEST["game"]["config_schema"]
    fixtures = [v["game_config"] for v in MANIFEST["variants"]]
    fixtures.append(MANIFEST["certification"]["game_config"])
    for fixture in fixtures:
        config = dict(fixture)
        config["tokens"] = ["t0", "t1"]      # the runner injects these
        jsonschema.validate(config, schema)


def test_the_certification_fixture_outlasts_the_viewer_soak():
    """3 holes x ~16 beats at 700 ms/beat is ~34 s of playback at 1x, which
    comfortably outlasts the wasm-viewer job's --soak window."""
    cert = MANIFEST["certification"]["game_config"]
    assert cert["holes"] == 3 and cert["seed"] == 7
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    soak = re.search(r"--soak (\d+)", ci)
    assert soak, "the wasm-viewer job must soak the playback"
    assert cert["holes"] * 16 * 0.7 > int(soak.group(1))
