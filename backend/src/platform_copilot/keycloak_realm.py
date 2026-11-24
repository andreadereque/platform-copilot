"""Build the Keycloak realm (users, groups, the web client) from seed/users.json.

Usage: `make realm` (`uv run python -m platform_copilot.keycloak_realm`) writes
keycloak/platform-copilot-realm.json, which Keycloak imports on start (`--import-realm`).
A test checks the committed file is up to date.

Everything here is fake and local: one shared demo password, http://localhost only.
"""

import json
from pathlib import Path
from typing import Any

from platform_copilot.seed import SEED_DIR

REALM = "platform-copilot"
WEB_CLIENT = "platform-copilot-web"
API_AUDIENCE = "platform-copilot-api"
UI_ORIGIN = "http://localhost:5173"
DEMO_PASSWORD = "copilot"  # fake users on a laptop; documented in the README
REALM_FILE = SEED_DIR.parent / "keycloak" / f"{REALM}-realm.json"


def keycloak_user(user: dict[str, Any]) -> dict[str, Any]:
    first, _, last = str(user["name"]).partition(" ")
    local, _, domain = str(user["id"]).partition("@")
    return {
        "username": user["id"],
        "enabled": True,
        "firstName": first,
        "lastName": last or "Demo",
        "email": f"{local}.{domain or 'demo'}@example.com",
        "emailVerified": True,
        "credentials": [{"type": "password", "value": DEMO_PASSWORD, "temporary": False}],
        "groups": [f"/{group}" for group in user["groups"]],
    }


def web_client() -> dict[str, Any]:
    """A public client (a browser cannot keep a secret): authorization code + PKCE only."""
    return {
        "clientId": WEB_CLIENT,
        "name": "platform-copilot UI",
        "publicClient": True,
        "standardFlowEnabled": True,  # authorization code
        "implicitFlowEnabled": False,  # deprecated: tokens in the URL
        "directAccessGrantsEnabled": False,  # no password grant: the app never sees passwords
        "redirectUris": [f"{UI_ORIGIN}/*"],
        "webOrigins": [UI_ORIGIN],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": f"{UI_ORIGIN}/*",
        },
        "protocolMappers": [
            {
                "name": "groups",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "config": {
                    "claim.name": "groups",
                    "full.path": "false",
                    "access.token.claim": "true",
                    "id.token.claim": "true",
                    "userinfo.token.claim": "true",
                },
            },
            {
                "name": "api audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.custom.audience": API_AUDIENCE,
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            },
        ],
    }


def build_realm(users: list[dict[str, Any]]) -> dict[str, Any]:
    groups = sorted({group for user in users for group in user["groups"]})
    return {
        "realm": REALM,
        "enabled": True,
        "accessTokenLifespan": 300,  # 5 minutes; the UI renews it with the refresh token
        "groups": [{"name": group} for group in groups],
        "users": [keycloak_user(user) for user in users],
        "clients": [web_client()],
    }


def render(seed_dir: Path = SEED_DIR) -> str:
    users = json.loads((seed_dir / "users.json").read_text(encoding="utf-8"))
    return json.dumps(build_realm(users), indent=2, ensure_ascii=False) + "\n"


def main(target: Path = REALM_FILE) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
