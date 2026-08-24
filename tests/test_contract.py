"""contract.py is the wire contract: stdlib-only, and its constants match
the golden list tests/contract_manifest.txt (four-surface rename rule)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from cogame_cogolf import contract, results
from cogame_cogolf.server import PROTOCOL

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "server" / "cogame_cogolf" / "contract.py"
GOLDEN = REPO_ROOT / "tests" / "contract_manifest.txt"
PROTOCOL_MD = (REPO_ROOT / "docs" / "PROTOCOL.md").read_text()


def _constants() -> dict:
    return {name: getattr(contract, name) for name in sorted(dir(contract))
            if name.isupper()}


def test_contract_has_no_third_party_imports():
    tree = ast.parse(MODULE.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert imports == ["__future__"], imports
    assert "four-surface rename rule" in (ast.get_docstring(tree) or "").lower()


def test_contract_matches_golden_manifest():
    lines = [f"{name} = {json.dumps(value)}"
             for name, value in _constants().items()]
    expected = GOLDEN.read_text().splitlines()
    assert lines == expected, (
        "contract.py drifted from tests/contract_manifest.txt; update all "
        "four surfaces (contract.py, contract_manifest.txt, docs/PROTOCOL.md, "
        "players/)")


def test_server_uses_contract_constants():
    assert PROTOCOL == contract.PROTOCOL == "cogame.cogolf.v1"
    assert results.FALLBACK_CAUSES == contract.FALLBACK_CAUSES
    assert results.REASONS == contract.REASONS
    assert results.RESULT_KEYS == set(contract.RESULT_KEYS)


def test_the_protocol_page_documents_every_message_type():
    for name in ("MSG_WELCOME", "MSG_OBSERVATION", "MSG_SUBMISSION",
                 "MSG_DONE", "MSG_STATUS", "MSG_PROGRESS"):
        assert getattr(contract, name) in PROTOCOL_MD, name
    assert contract.PROTOCOL in PROTOCOL_MD
    for cause in contract.FALLBACK_CAUSES:
        assert cause in PROTOCOL_MD, cause
    for reason in contract.ILLEGAL_REASONS:
        assert reason in PROTOCOL_MD, reason


def test_the_player_harness_speaks_the_same_strings():
    client = (REPO_ROOT / "players" / "client.py").read_text()
    assert 'PROTOCOL = "cogame.cogolf.v1"' in client
    assert 'MSG_SUBMISSION = "submission"' in client
    for name in ("MAX_IMPL_CHARS", "MAX_TESTS_PER_HOLE", "MAX_MESSAGE_BYTES",
                 "MAX_TEST_NAME_CHARS", "MAX_WHY_CHARS", "MAX_NOTE_CHARS"):
        assert name in client, name


def test_the_alias_space_is_two_seats():
    assert contract.ALIASES == ("Ash", "Basil")
    assert contract.SEATS == 2
