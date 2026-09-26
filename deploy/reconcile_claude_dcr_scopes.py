"""Add optional finance:write to narrowly matched Claude Web DCR clients in master.

Run after Keycloak bootstrap and after Claude creates its dynamic client:
    python3 deploy/reconcile_claude_dcr_scopes.py

The script does not grant a user consent or change a client representation. It
only lets Claude request the write scope later when the MCP asks for step-up.
Credentials come from KC_ADMIN_TOKEN or KC_ADMIN_USERNAME/KC_ADMIN_PASSWORD;
KEYCLOAK_ADMIN and KEYCLOAK_ADMIN_PASSWORD are accepted as deployment aliases.
The deployed bootstrap admin username defaults to admin.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import UUID

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploy.bootstrap_realm import Admin, BootstrapError


REALM = "master"
CLAUDE_CALLBACKS = frozenset(
    {
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    }
)
PAGE_SIZE = 100
EXPECTED_FLAGS = {
    "protocol": "openid-connect",
    "enabled": True,
    # Claude's observed DCR client uses a client secret, even though it was
    # created through the public registration endpoint.
    "publicClient": False,
    "clientAuthenticatorType": "client-secret",
    "standardFlowEnabled": True,
    "consentRequired": True,
    "fullScopeAllowed": False,
    "implicitFlowEnabled": False,
    "directAccessGrantsEnabled": False,
    "serviceAccountsEnabled": False,
}


def admin_environment(source):
    """Normalize existing private Keycloak environment variable names."""
    env = dict(source)
    if not env.get("KC_ADMIN_USERNAME"):
        env["KC_ADMIN_USERNAME"] = env.get("KEYCLOAK_ADMIN") or "admin"
    if not env.get("KC_ADMIN_PASSWORD") and env.get("KEYCLOAK_ADMIN_PASSWORD"):
        env["KC_ADMIN_PASSWORD"] = env["KEYCLOAK_ADMIN_PASSWORD"]
    return env


def exact_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def require_list(value, label):
    if not isinstance(value, list):
        raise BootstrapError(f"Unexpected Keycloak {label} response")
    return value


def unique_scope(scopes, name):
    found = [scope for scope in scopes if isinstance(scope, dict) and scope.get("name") == name]
    if len(found) != 1 or found[0].get("protocol") != "openid-connect" or not exact_uuid(found[0].get("id")):
        raise BootstrapError(f"Missing or ambiguous Keycloak client scope: {name}")
    return found[0]


def assigned_names(scopes, label):
    scopes = require_list(scopes, label)
    if any(not isinstance(scope, dict) or not isinstance(scope.get("name"), str) or not isinstance(scope.get("id"), str) for scope in scopes):
        raise BootstrapError(f"Unexpected Keycloak {label} response")
    names = [scope["name"] for scope in scopes]
    if len(names) != len(set(names)):
        raise BootstrapError(f"Ambiguous Keycloak {label} response")
    return set(names)


def require_no_role_mappings(api, path):
    mappings = api.request("GET", path + "/scope-mappings") or {}
    if not isinstance(mappings, dict):
        raise BootstrapError("Invalid master roles scope mappings")
    client = mappings.get("clientMappings") or {}
    realm = mappings.get("realmMappings") or []
    if not isinstance(client, dict) or any(client.values()) or not isinstance(realm, list) or realm:
        raise BootstrapError("Unexpected master roles scope mappings")


def require_safe_inherited_roles(api, realm_path, scopes, client_path):
    roles = unique_scope(scopes, "roles")
    roles_path = realm_path + "/client-scopes/" + quote(roles["id"], safe="")
    require_no_role_mappings(api, roles_path)
    require_no_role_mappings(api, client_path)
    mappers = require_list(
        api.request("GET", roles_path + "/protocol-mappers/models"),
        "master roles scope mapper list",
    )
    expected = {
        "oidc-usermodel-realm-role-mapper": "realm_access.roles",
        "oidc-usermodel-client-role-mapper": "resource_access.${client_id}.roles",
        "oidc-audience-resolve-mapper": None,
    }
    if len(mappers) != len(expected):
        raise BootstrapError("Unexpected master roles scope mappers")
    seen = set()
    for mapper in mappers:
        if not isinstance(mapper, dict) or mapper.get("protocol") != "openid-connect":
            raise BootstrapError("Unexpected master roles scope mappers")
        kind = mapper.get("protocolMapper")
        config = mapper.get("config") or {}
        if kind not in expected or kind in seen or not isinstance(config, dict):
            raise BootstrapError("Unexpected master roles scope mappers")
        if config.get("claim.name") != expected[kind]:
            raise BootstrapError("Unexpected master roles scope claim")
        if any(
            not isinstance(key, str) or (key.startswith("included.") and key.endswith(".audience"))
            for key in config
        ):
            raise BootstrapError("Unexpected master roles scope audience")
        seen.add(kind)


def iter_clients(api, realm_path):
    seen = set()
    first = 0
    while True:
        path = realm_path + "/clients?" + urlencode(
            {"first": first, "max": PAGE_SIZE, "briefRepresentation": "false"}
        )
        page = require_list(api.request("GET", path), "client page")
        if len(page) > PAGE_SIZE:
            raise BootstrapError("Unexpected Keycloak client page length")
        if not page:
            return
        for item in page:
            client_id = item.get("id") if isinstance(item, dict) else None
            if not exact_uuid(client_id) or client_id in seen:
                raise BootstrapError("Invalid or repeated Keycloak client ID")
            seen.add(client_id)
            yield client_id
        first += len(page)


def eligible(client):
    """Exact callback and observed Claude DCR profile; skip all other clients."""
    if not isinstance(client, dict):
        return False
    redirects = client.get("redirectUris")
    if not isinstance(redirects, list) or len(redirects) != 1 or redirects[0] not in CLAUDE_CALLBACKS:
        return False
    client_id = client.get("clientId")
    if not exact_uuid(client_id):
        return False
    return all(client.get(key) == value for key, value in EXPECTED_FLAGS.items())


def reconcile(api):
    realm_path = "/admin/realms/" + REALM
    scopes = require_list(api.request("GET", realm_path + "/client-scopes"), "scope list")
    unique_scope(scopes, "finance:read")
    write = unique_scope(scopes, "finance:write")

    scanned = 0
    matched = 0
    pending = []
    # Preflight every Budget match before changing any client. A malformed
    # later page must not leave a partial migration from the same run.
    for client_id in iter_clients(api, realm_path):
        scanned += 1
        path = realm_path + "/clients/" + quote(client_id, safe="")
        client = api.request("GET", path)
        if not isinstance(client, dict) or client.get("id") != client_id:
            raise BootstrapError("Unexpected Keycloak client representation")
        if not eligible(client):
            continue
        optional = assigned_names(api.request("GET", path + "/optional-client-scopes"), "optional scope list")
        if "finance:read" not in optional:
            # Invest DCR clients share the callback/profile but not Budget scopes.
            continue
        matched += 1
        defaults = assigned_names(api.request("GET", path + "/default-client-scopes"), "default scope list")
        if "roles" in defaults or "roles" in optional:
            # A no-scope DCR registration can inherit the realm's default roles
            # scope alongside optional finance scopes. Accept it only when the
            # write scope is already optional and roles cannot map claims or aud.
            if "roles" not in defaults or "roles" in optional or "finance:write" not in optional:
                raise BootstrapError("Claude DCR client exposes master roles scope")
            require_safe_inherited_roles(api, realm_path, scopes, path)
        if "finance:write" in defaults:
            raise BootstrapError("Claude DCR client has unexpected default finance:write")
        if "finance:write" not in optional:
            pending.append(path + "/optional-client-scopes/" + quote(write["id"], safe=""))

    for path in pending:
        api.request("PUT", path)
        optional_path = path.rsplit("/", 1)[0]
        after = assigned_names(
            api.request("GET", optional_path), "optional scope postcheck"
        )
        if "finance:write" not in after:
            raise BootstrapError("Claude DCR write scope assignment did not persist")
    return {"scanned": scanned, "matched": matched, "updated": len(pending)}


def main():
    try:
        result = reconcile(Admin(admin_environment(os.environ)))
        print(json.dumps(result))
        return 0
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
