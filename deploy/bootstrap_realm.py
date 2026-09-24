"""Idempotent Keycloak 26.7.4 bootstrap; all credentials come from environment.

Required: KC_ADMIN_TOKEN or KC_ADMIN_USERNAME + KC_ADMIN_PASSWORD;
CHATGPT_REDIRECT_URIS and CLAUDE_REDIRECT_URIS (JSON arrays of exact HTTPS URLs);
CHATGPT_CLIENT_SECRET and CLAUDE_CLIENT_SECRET (stable secrets, at least 32 characters);
BUDGET_USERNAME; BUDGET_PASSWORD (only when creating the owner).
Optional: KC_ADMIN_URL (loopback default), BUDGET_EMAIL.

Run: python deploy/bootstrap_realm.py
Output contains only public issuer, audience, client IDs, and owner subject.
Existing owner passwords are never reset. Anonymous DCR accepts client URI hosts
only from chatgpt.com, claude.ai, and claude.com; other registration policies stay intact.
Reference: https://www.keycloak.org/docs-api/26.7.4/rest-api/index.html
"""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REALM = "budget"
ISSUER = "https://budget.theblackhaired.ru/auth/realms/budget"
AUDIENCE = "https://budget.theblackhaired.ru/mcp"
SCOPES = ("finance:read", "finance:write")
OIDC_OPTIONAL_SCOPES = ("email", "profile")
REGISTRATION_POLICY_TYPE = (
    "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"
)
DCR_TRUSTED_HOSTS = ("chatgpt.com", "claude.ai", "claude.com")


class BootstrapError(Exception):
    """Safe error whose text contains no upstream body or credential."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BootstrapError("Unexpected redirect from Keycloak admin API")


def required(env, name):
    value = env.get(name, "")
    if not value:
        raise BootstrapError(f"Required environment variable: {name}")
    return value


def callbacks(raw):
    try:
        urls = json.loads(raw)
    except (TypeError, ValueError):
        raise BootstrapError("Callback configuration must be a JSON array") from None
    if not isinstance(urls, list) or not urls:
        raise BootstrapError("Callback configuration must be a nonempty JSON array")
    for url in urls:
        if not isinstance(url, str):
            raise BootstrapError("Callbacks must be exact HTTPS URLs")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or "*" in url
            or any(c.isspace() for c in url)
        ):
            raise BootstrapError("Callbacks must be exact HTTPS URLs without wildcards")
    return list(dict.fromkeys(urls))


def client_spec(client_id, redirects, secret):
    if (
        not isinstance(secret, str)
        or len(secret) < 32
        or any(c.isspace() for c in secret)
    ):
        raise BootstrapError(
            "Client secrets must contain at least 32 non-whitespace characters"
        )
    return {
        "clientId": client_id,
        "protocol": "openid-connect",
        "enabled": True,
        "publicClient": False,
        # Keycloak's client-secret authenticator supports basic and post.
        # Use the supplied stable secret on every run, never auto-generate it.
        "clientAuthenticatorType": "client-secret",
        "secret": secret,
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "authorizationServicesEnabled": False,
        "consentRequired": True,
        "fullScopeAllowed": False,
        "redirectUris": redirects,
        "webOrigins": [],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "oauth2.device.authorization.grant.enabled": "false",
            "oidc.ciba.grant.enabled": "false",
            "standard.token.exchange.enabled": "false",
            "oauth2.jwt.authorization.grant.enabled": "false",
            "exclude.issuer.from.auth.response": "false",
            "access.token.signed.response.alg": "RS256",
            "use.refresh.tokens": "true",
        },
    }


def audience_mapper():
    return {
        "name": "budget-mcp-audience",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "consentRequired": False,
        "config": {
            "included.custom.audience": AUDIENCE,
            "access.token.claim": "true",
            "id.token.claim": "false",
            "introspection.token.claim": "true",
        },
    }


class Admin:
    def __init__(self, env):
        self.base = env.get("KC_ADMIN_URL", "http://127.0.0.1:8081/auth").rstrip("/")
        parsed = urlsplit(self.base)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or (
                parsed.scheme == "http"
                and parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise BootstrapError("Admin URL must use HTTPS or loopback HTTP")
        self.opener = build_opener(NoRedirect())
        self.token = env.get("KC_ADMIN_TOKEN", "")
        if not self.token:
            form = {
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": required(env, "KC_ADMIN_USERNAME"),
                "password": required(env, "KC_ADMIN_PASSWORD"),
            }
            result = self.request(
                "POST", "/realms/master/protocol/openid-connect/token", form=form
            )
            self.token = result["access_token"]

    def request(self, method, path, body=None, form=None, missing=False):
        headers = {"Accept": "application/json"}
        data = None
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if form is not None:
            data = urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self.opener.open(
                Request(self.base + path, data=data, headers=headers, method=method),
                timeout=30,
            ) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            if missing and exc.code == 404:
                return None
            raise BootstrapError(
                f"Keycloak admin request failed: HTTP {exc.code}"
            ) from None
        except (URLError, TimeoutError, ValueError):
            raise BootstrapError(
                "Keycloak admin request failed (connection or response)"
            ) from None


def configure_registration_policy(api, realm_path):
    components = api.request(
        "GET",
        realm_path + "/components?" + urlencode({"type": REGISTRATION_POLICY_TYPE}),
    )
    policies = [
        component
        for component in components
        if component.get("providerType") == REGISTRATION_POLICY_TYPE
        and component.get("providerId") == "trusted-hosts"
        and component.get("subType") == "anonymous"
    ]
    if len(policies) != 1:
        raise BootstrapError(
            "Expected exactly one anonymous Trusted Hosts registration policy"
        )
    policy = policies[0]
    # Preserve the complete component and every unrelated configuration field.
    config = dict(policy.get("config") or {})
    config.update(
        {
            "host-sending-registration-request-must-match": ["false"],
            "client-uris-must-match": ["true"],
            "trusted-hosts": list(DCR_TRUSTED_HOSTS),
        }
    )
    api.request(
        "PUT",
        realm_path + "/components/" + quote(policy["id"], safe=""),
        dict(policy, config=config),
    )


def bootstrap(api, env, clients):
    realm_path = "/admin/realms/" + REALM
    realm = {
        "realm": REALM,
        "enabled": True,
        "registrationAllowed": False,
        "registrationEmailAsUsername": False,
        "resetPasswordAllowed": False,
        "rememberMe": False,
        "sslRequired": "external",
        "bruteForceProtected": True,
        "permanentLockout": False,
        "failureFactor": 5,
        "waitIncrementSeconds": 60,
        "maxFailureWaitSeconds": 900,
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 1800,
        "ssoSessionMaxLifespan": 43200,
        "revokeRefreshToken": True,
        "refreshTokenMaxReuse": 0,
        "defaultSignatureAlgorithm": "RS256",
        "passwordPolicy": "length(16)",
    }
    if api.request("GET", realm_path, missing=True) is None:
        api.request("POST", "/admin/realms", realm)
    else:
        api.request("PUT", realm_path, realm)
    configure_registration_policy(api, realm_path)
    # DCR clients inherit realm defaults. The roles scope includes Keycloak's
    # audience-resolve mapper, which would add account to the MCP token audience.
    defaults_path = realm_path + "/default-default-client-scopes"
    for scope in api.request("GET", defaults_path):
        if scope["name"] == "roles":
            api.request("DELETE", defaults_path + "/" + quote(scope["id"], safe=""))

    scope_list = api.request("GET", realm_path + "/client-scopes")
    # openid is a protocol scope handled intrinsically by Keycloak, not a
    # client-scope object. Discovery advertises it alongside realm client scopes.
    # Built-in email/profile only map identity claims, never an audience.
    oidc_scope_ids = []
    for name in OIDC_OPTIONAL_SCOPES:
        scope = next((s for s in scope_list if s["name"] == name), None)
        if scope is None:
            raise BootstrapError(f"Keycloak built-in client scope is missing: {name}")
        scope_path = realm_path + "/client-scopes/" + quote(scope["id"], safe="")
        for mapper in api.request("GET", scope_path + "/protocol-mappers/models"):
            if (
                mapper.get("protocolMapper")
                not in {
                    "oidc-usermodel-attribute-mapper",
                    "oidc-usermodel-property-mapper",
                    "oidc-full-name-mapper",
                }
                or mapper.get("config", {}).get("claim.name") == "aud"
            ):
                raise BootstrapError(
                    f"Unexpected mapper in built-in client scope: {name}"
                )
        oidc_scope_ids.append(scope["id"])
    scope_ids = {}
    for name in SCOPES:
        spec = {
            "name": name,
            "protocol": "openid-connect",
            "attributes": {
                "include.in.token.scope": "true",
                "display.on.consent.screen": "true",
                "consent.screen.text": name,
            },
        }
        existing = next((s for s in scope_list if s["name"] == name), None)
        if existing is None:
            api.request("POST", realm_path + "/client-scopes", spec)
            scope_list = api.request("GET", realm_path + "/client-scopes")
            existing = next(s for s in scope_list if s["name"] == name)
        scope_id = existing["id"]
        scope_ids[name] = scope_id
        path = realm_path + "/client-scopes/" + quote(scope_id, safe="")
        api.request("PUT", path, dict(spec, id=scope_id))
        mappers = api.request("GET", path + "/protocol-mappers/models")
        mapper = audience_mapper()
        old = next((m for m in mappers if m["name"] == mapper["name"]), None)
        for other in mappers:
            if other["name"] != mapper["name"]:
                api.request("DELETE", path + "/protocol-mappers/models/" + other["id"])
        if old:
            api.request(
                "PUT",
                path + "/protocol-mappers/models/" + old["id"],
                dict(mapper, id=old["id"]),
            )
        else:
            api.request("POST", path + "/protocol-mappers/models", mapper)
        # DCR clients may inherit these optional scopes. A registration request
        # with an explicit scope list can narrow that set; Claude Web currently
        # requests finance:read only. Never grant finance scopes by default.
        api.request(
            "PUT",
            realm_path + "/default-optional-client-scopes/" + quote(scope_id, safe=""),
        )

    for spec in clients:
        search = realm_path + "/clients?" + urlencode({"clientId": spec["clientId"]})
        found = api.request("GET", search)
        if not found:
            api.request("POST", realm_path + "/clients", spec)
            found = api.request("GET", search)
        client_id = found[0]["id"]
        path = realm_path + "/clients/" + quote(client_id, safe="")
        api.request("PUT", path, dict(spec, id=client_id))
        for mapper in api.request("GET", path + "/protocol-mappers/models"):
            api.request("DELETE", path + "/protocol-mappers/models/" + mapper["id"])
        # Remove broad default/optional scopes (roles, offline_access, etc.).
        # basic is required for the standard sub/session protocol mappers.
        for kind in ("default", "optional"):
            for scope in api.request("GET", path + f"/{kind}-client-scopes"):
                keep = scope["name"] == "basic" if kind == "default" else False
                if not keep:
                    api.request(
                        "DELETE", path + f"/{kind}-client-scopes/" + scope["id"]
                    )
        for scope_id in [*scope_ids.values(), *oidc_scope_ids]:
            api.request("PUT", path + "/optional-client-scopes/" + scope_id)
        basic = next((s for s in scope_list if s["name"] == "basic"), None)
        if basic is None:
            raise BootstrapError("Keycloak 26.7.4 basic client scope is missing")
        api.request("PUT", path + "/default-client-scopes/" + basic["id"])

    username = required(env, "BUDGET_USERNAME")
    search = realm_path + "/users?" + urlencode({"username": username, "exact": "true"})
    users = api.request("GET", search)
    if not users:
        password = required(env, "BUDGET_PASSWORD")
        if len(password) < 16:
            raise BootstrapError(
                "Initial owner password must contain at least 16 characters"
            )
        owner = {
            "username": username,
            "enabled": True,
            "firstName": "Budget",
            "lastName": "Owner",
            "credentials": [{"type": "password", "value": password, "temporary": True}],
        }
        if env.get("BUDGET_EMAIL"):
            owner["email"] = env["BUDGET_EMAIL"]
        api.request("POST", realm_path + "/users", owner)
        users = api.request("GET", search)
    if len(users) != 1 or not users[0].get("enabled"):
        raise BootstrapError("Expected exactly one enabled owner identity")
    return {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "owner_sub": users[0]["id"],
        "client_ids": [c["clientId"] for c in clients],
    }


def main():
    env = os.environ
    try:
        # Validate callback configuration before sending any mutation.
        clients = [
            client_spec(
                "budget-chatgpt",
                callbacks(required(env, "CHATGPT_REDIRECT_URIS")),
                required(env, "CHATGPT_CLIENT_SECRET"),
            ),
            client_spec(
                "budget-claude",
                callbacks(required(env, "CLAUDE_REDIRECT_URIS")),
                required(env, "CLAUDE_CLIENT_SECRET"),
            ),
        ]
        required(env, "BUDGET_USERNAME")
        result = bootstrap(Admin(env), env, clients)
        print(json.dumps(result))
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
