import json
from pathlib import Path

import pytest

from platform_copilot import keycloak_realm
from platform_copilot.keycloak_realm import REALM_FILE, build_realm, render


def test_committed_realm_matches_the_seed_users() -> None:
    """If seed/users.json changes, regenerate: uv run python -m platform_copilot.keycloak_realm"""
    assert REALM_FILE.read_text(encoding="utf-8") == render()


def test_web_client_is_public_with_pkce_and_no_password_grant() -> None:
    client = build_realm([])["clients"][0]
    assert client["publicClient"] is True
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert (client["implicitFlowEnabled"], client["directAccessGrantsEnabled"]) == (False, False)
    mappers = {m["protocolMapper"]: m["config"] for m in client["protocolMappers"]}
    assert mappers["oidc-audience-mapper"]["included.custom.audience"] == "platform-copilot-api"
    assert mappers["oidc-group-membership-mapper"]["claim.name"] == "groups"


def test_users_get_their_groups_and_complete_profiles() -> None:
    realm = build_realm([{"id": "guest", "name": "Invitado", "groups": ["public"]}])
    assert realm["groups"] == [{"name": "public"}]
    user = realm["users"][0]
    assert (user["firstName"], user["lastName"]) == ("Invitado", "Demo")
    assert user["email"] == "guest.demo@example.com"
    assert user["groups"] == ["/public"]


def test_main_writes_the_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "keycloak" / "realm.json"
    keycloak_realm.main(target)
    assert json.loads(target.read_text())["realm"] == "platform-copilot"
    assert "Wrote" in capsys.readouterr().out
