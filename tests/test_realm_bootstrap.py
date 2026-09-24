import copy
import importlib.util
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

spec = importlib.util.spec_from_file_location(
    "realm_bootstrap", Path(__file__).resolve().parents[1] / "deploy/bootstrap_realm.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
TEST_CLIENT_SECRET = "unit-test-only-client-secret-00001"


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '"https://a.example/cb"',
        '["https://a.example/*"]',
        '["http://a.example/cb"]',
        '["https://u:p@a.example/cb"]',
        '["https://a.example/#x"]',
    ],
)
def test_rejects_unsafe_callbacks(raw):
    with pytest.raises(module.BootstrapError):
        module.callbacks(raw)


def test_exact_callbacks_and_client_restrictions():
    redirects = module.callbacks(
        '["https://chatgpt.com/connector_platform_oauth_redirect"]'
    )
    client = module.client_spec("budget-chatgpt", redirects, TEST_CLIENT_SECRET)
    assert client["redirectUris"] == redirects
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert not client["publicClient"]
    assert client["standardFlowEnabled"]
    assert client["clientAuthenticatorType"] == "client-secret"
    assert client["secret"] == TEST_CLIENT_SECRET
    assert not any(
        client[k]
        for k in (
            "directAccessGrantsEnabled",
            "implicitFlowEnabled",
            "serviceAccountsEnabled",
            "fullScopeAllowed",
        )
    )
    assert (
        module.audience_mapper()["config"]["included.custom.audience"]
        == module.AUDIENCE
    )


def test_api_errors_never_contain_upstream_secrets():
    admin = module.Admin({"KC_ADMIN_TOKEN": "unit-test-token"})

    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError(
                "https://example.org", 401, "secret-in-upstream-message", {}, None
            )

    admin.opener = Opener()
    with pytest.raises(module.BootstrapError, match="HTTP 401") as exc:
        admin.request("GET", "/admin/realms/budget")
    assert "secret" not in str(exc.value)


class FakeAdmin:
    """Stateful REST fake covering create/update and scope assignment semantics."""

    def __init__(self):
        self.realm = None
        self.scopes = [
            {"name": name, "id": name + "-id"} for name in ("basic", "email", "profile")
        ]
        self.clients = []
        self.mappers = {}
        self.assignments = {}
        self.users = []
        self.user_creates = 0
        self.realm_optional_scopes = {"offline-access-id", "address-id"}
        self.realm_default_scopes = [
            {"id": name + "-id", "name": name}
            for name in ("roles", "basic", "profile", "email", "acr")
        ]
        self.components = [
            {
                "id": "trusted-policy",
                "name": "Trusted Hosts",
                "parentId": "budget-id",
                "providerType": module.REGISTRATION_POLICY_TYPE,
                "providerId": "trusted-hosts",
                "subType": "anonymous",
                "config": {"untouched": ["original-value"]},
            }
        ]

    def request(self, method, path, body=None, **kwargs):
        body = copy.deepcopy(body)
        parts = [unquote(part) for part in path.split("?")[0].split("/")[4:]]
        if not parts:
            if method == "GET":
                return self.realm
            self.realm = body
            return None
        if path == "/admin/realms":
            self.realm = body
        elif parts[0] == "default-default-client-scopes":
            if method == "GET":
                return copy.deepcopy(self.realm_default_scopes)
            assert method == "DELETE"
            self.realm_default_scopes = [
                s for s in self.realm_default_scopes if s["id"] != parts[1]
            ]
        elif parts[0] == "default-optional-client-scopes":
            assert method == "PUT"
            self.realm_optional_scopes.add(parts[1])
        elif parts[0] == "components":
            if method == "GET":
                return copy.deepcopy(self.components)
            self.components = [
                body if c["id"] == parts[1] else c for c in self.components
            ]
        elif parts[0] == "client-scopes":
            if len(parts) == 1:
                if method == "GET":
                    return copy.deepcopy(self.scopes)
                self.scopes.append(dict(body, id=body["name"]))
            elif len(parts) > 2:
                self.mapper_request(method, parts[1:], body)
                if method == "GET":
                    return copy.deepcopy(self.mappers.get(parts[1], []))
        elif parts[0] == "clients":
            if len(parts) == 1:
                if method == "GET":
                    name = path.split("clientId=")[1]
                    return [
                        copy.deepcopy(c) for c in self.clients if c["clientId"] == name
                    ]
                self.clients.append(dict(body, id=body["clientId"]))
            elif len(parts) == 2:
                self.clients = [
                    body if c["id"] == parts[1] else c for c in self.clients
                ]
            elif parts[2] == "protocol-mappers":
                if method == "GET":
                    return []
            else:
                key = tuple(parts[1:3])
                assigned = self.assignments.setdefault(key, [])
                if method == "GET":
                    return copy.deepcopy(assigned)
                scope_id = parts[3]
                if method == "DELETE":
                    assigned[:] = [s for s in assigned if s["id"] != scope_id]
                elif not any(s["id"] == scope_id for s in assigned):
                    assigned.append(next(s for s in self.scopes if s["id"] == scope_id))
        elif parts[0] == "users":
            if method == "GET":
                return copy.deepcopy(self.users)
            self.user_creates += 1
            self.users.append(dict(body, id="owner-subject"))

    def mapper_request(self, method, parts, body):
        mappers = self.mappers.setdefault(parts[0], [])
        if method == "POST":
            mappers.append(dict(body, id="mapper-" + parts[0]))
        elif method == "PUT":
            mappers[:] = [body if m["id"] == body["id"] else m for m in mappers]


def test_repeated_bootstrap_preserves_owner_and_optional_scopes():
    api = FakeAdmin()
    env = {"BUDGET_USERNAME": "owner", "BUDGET_PASSWORD": "unit-test-only-password"}
    clients = [
        module.client_spec(
            "budget-chatgpt", ["https://example.org/callback"], TEST_CLIENT_SECRET
        )
    ]
    first = module.bootstrap(api, env, clients)
    del env["BUDGET_PASSWORD"]
    second = module.bootstrap(api, env, clients)
    assert first == second
    assert api.user_creates == 1
    assert len(api.clients) == 1
    assert api.clients[0]["secret"] == TEST_CLIENT_SECRET
    assert TEST_CLIENT_SECRET not in str(second)
    assert len(api.scopes) == 5
    assert [s["name"] for s in api.realm_default_scopes] == [
        "basic",
        "profile",
        "email",
        "acr",
    ]
    assert api.realm_optional_scopes == {
        "offline-access-id",
        "address-id",
        "finance:read",
        "finance:write",
    }
    assert all(len(api.mappers[name]) == 1 for name in module.SCOPES)
    assigned = api.assignments[("budget-chatgpt", "optional-client-scopes")]
    assert {s["name"] for s in assigned} == {
        "finance:read",
        "finance:write",
        "email",
        "profile",
    }
    assert not any(s["name"] == "openid" for s in api.scopes)
    assert (
        api.assignments[("budget-chatgpt", "default-client-scopes")][0]["name"]
        == "basic"
    )
    assert api.users[0]["credentials"][0]["temporary"]


@pytest.mark.parametrize(
    "mapper",
    [
        {"protocolMapper": "oidc-audience-resolve-mapper"},
        {"protocolMapper": "oidc-audience-mapper"},
        {
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {"claim.name": "aud"},
        },
    ],
)
def test_identity_scope_cannot_add_another_audience(mapper):
    api = FakeAdmin()
    api.mappers["email-id"] = [dict(mapper, id="bad-mapper")]
    with pytest.raises(module.BootstrapError, match="Unexpected mapper"):
        module.bootstrap(api, {"BUDGET_USERNAME": "owner"}, [])


def test_builtin_identity_claim_mappers_are_supported():
    api = FakeAdmin()
    api.mappers["email-id"] = [
        {
            "id": "email-mapper",
            "protocolMapper": "oidc-usermodel-property-mapper",
            "config": {"claim.name": "email"},
        }
    ]
    env = {"BUDGET_USERNAME": "owner", "BUDGET_PASSWORD": "unit-test-only-password"}
    module.bootstrap(api, env, [])


@pytest.mark.parametrize("secret", ["", "short", " " * 40, "x" * 32 + "\n"])
def test_invalid_client_secret_fails_without_echo(secret):
    with pytest.raises(module.BootstrapError) as exc:
        module.client_spec("budget-chatgpt", ["https://example.org/callback"], secret)
    if secret:
        assert secret not in str(exc.value)


def test_main_does_not_print_client_secrets(monkeypatch, capsys):
    env = {
        "BUDGET_USERNAME": "owner",
        "BUDGET_PASSWORD": "unit-test-only-password",
        "CHATGPT_REDIRECT_URIS": '["https://example.org/chatgpt"]',
        "CLAUDE_REDIRECT_URIS": '["https://example.org/claude"]',
        "CHATGPT_CLIENT_SECRET": TEST_CLIENT_SECRET,
        "CLAUDE_CLIENT_SECRET": "unit-test-only-claude-secret-00001",
    }
    api = FakeAdmin()
    monkeypatch.setattr(module.os, "environ", env)
    monkeypatch.setattr(module, "Admin", lambda _: api)
    assert module.main() == 0
    output = capsys.readouterr()
    for name in ("BUDGET_PASSWORD", "CHATGPT_CLIENT_SECRET", "CLAUDE_CLIENT_SECRET"):
        assert env[name] not in output.out + output.err
    assert len(api.clients) == 2
    assert {client["secret"] for client in api.clients} == {
        env["CHATGPT_CLIENT_SECRET"],
        env["CLAUDE_CLIENT_SECRET"],
    }


def test_trusted_host_policy_is_idempotent_and_preserves_other_fields():
    api = FakeAdmin()
    authenticated = copy.deepcopy(api.components[0])
    authenticated.update(id="authenticated-policy", subType="authenticated")
    api.components.append(authenticated)
    module.configure_registration_policy(api, "/admin/realms/budget")
    first = copy.deepcopy(api.components)
    module.configure_registration_policy(api, "/admin/realms/budget")
    assert api.components == first
    assert api.components[1] == authenticated
    policy = api.components[0]
    assert policy["parentId"] == "budget-id"
    assert policy["name"] == "Trusted Hosts"
    assert policy["config"] == {
        "untouched": ["original-value"],
        "host-sending-registration-request-must-match": ["false"],
        "client-uris-must-match": ["true"],
        "trusted-hosts": ["chatgpt.com", "claude.ai", "claude.com"],
    }


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_registration_policy_fails_closed(count):
    api = FakeAdmin()
    api.components *= count
    before = copy.deepcopy(api.components)
    with pytest.raises(module.BootstrapError, match="exactly one anonymous"):
        module.configure_registration_policy(api, "/admin/realms/budget")
    assert api.components == before
