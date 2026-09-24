import copy

import pytest

from deploy import reconcile_claude_dcr_scopes as module


CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def client(client_id, redirects=None, **overrides):
    result = {
        "id": client_id,
        "clientId": client_id,
        "redirectUris": [CALLBACK] if redirects is None else redirects,
        "protocol": "openid-connect",
        "enabled": True,
        "publicClient": False,
        "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled": True,
        "consentRequired": True,
        "fullScopeAllowed": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
    }
    result.update(overrides)
    return result


class FakeAdmin:
    def __init__(self, clients, page_size=100):
        self.clients = {item["id"]: copy.deepcopy(item) for item in clients}
        self.ids = [item["id"] for item in clients]
        self.page_size = page_size
        self.scopes = [
            {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "name": "finance:read", "protocol": "openid-connect"},
            {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "name": "finance:write", "protocol": "openid-connect"},
        ]
        self.optional = {item["id"]: [self.scopes[0], {"id": "offline-id", "name": "offline_access"}] for item in clients}
        self.defaults = {item["id"]: [{"id": "basic-id", "name": "basic"}] for item in clients}
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        base = "/admin/realms/budget/"
        if path == base + "client-scopes":
            return copy.deepcopy(self.scopes)
        if path.startswith(base + "clients?"):
            from urllib.parse import parse_qs, urlsplit

            params = parse_qs(urlsplit(path).query)
            first = int(params["first"][0])
            maximum = int(params["max"][0])
            assert params["briefRepresentation"] == ["false"]
            count = min(maximum, self.page_size)
            return [{"id": value} for value in self.ids[first:first + count]]
        assert path.startswith(base + "clients/")
        segments = path[len(base + "clients/"):].split("/")
        client_id = segments[0]
        if len(segments) == 1:
            return copy.deepcopy(self.clients[client_id])
        if len(segments) == 2:
            return copy.deepcopy(self.optional[client_id] if segments[1] == "optional-client-scopes" else self.defaults[client_id])
        assert method == "PUT" and segments[1:] == ["optional-client-scopes", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
        self.optional[client_id].append(self.scopes[1])
        return None


def test_exact_callback_only_and_preserves_other_clients():
    good = client("11111111-1111-4111-8111-111111111111")
    others = [
        client("22222222-2222-4222-8222-222222222222", ["https://claude.com/other_callback"]),
        client("33333333-3333-4333-8333-333333333333", [CALLBACK, "https://evil.example/cb"]),
        client("44444444-4444-4444-8444-444444444444", publicClient=True),
        client("55555555-5555-4555-8555-555555555555", fullScopeAllowed=True),
        client("66666666-6666-4666-8666-666666666666", clientId="budget-claude"),
    ]
    api = FakeAdmin([good, *others])
    result = module.reconcile(api)
    assert result == {"scanned": 6, "matched": 1, "updated": 1}
    assert {scope["name"] for scope in api.optional[good["id"]]} == {"finance:read", "finance:write", "offline_access"}
    for other in others:
        assert {scope["name"] for scope in api.optional[other["id"]]} == {"finance:read", "offline_access"}
    assert all(api.clients[item["id"]] == item for item in [good, *others])


def test_exact_claude_com_callback_is_eligible():
    target = client("11111111-1111-4111-8111-111111111111", ["https://claude.com/api/mcp/auth_callback"])
    api = FakeAdmin([target])
    assert module.reconcile(api) == {"scanned": 1, "matched": 1, "updated": 1}


def test_idempotence_preserves_existing_scopes_and_consent():
    target = client("11111111-1111-4111-8111-111111111111")
    api = FakeAdmin([target])
    api.optional[target["id"]].append({"id": "profile-id", "name": "profile"})
    assert module.reconcile(api)["updated"] == 1
    before = copy.deepcopy(api.optional)
    assert module.reconcile(api)["updated"] == 0
    assert api.optional == before
    assert api.clients[target["id"]] == target
    assert sum(method == "PUT" for method, _ in api.calls) == 1


def test_paginates_all_clients():
    others = [client(f"00000000-0000-4000-8000-{n:012d}", ["https://other.example/cb"]) for n in range(200)]
    target = client("11111111-1111-4111-8111-111111111111")
    api = FakeAdmin([*others, target])
    assert module.reconcile(api) == {"scanned": 201, "matched": 1, "updated": 1}
    pages = [path for method, path in api.calls if method == "GET" and "/clients?" in path]
    assert len(pages) == 4


def test_paginates_when_server_caps_page_size():
    others = [client(f"00000000-0000-4000-8000-{n:012d}", ["https://other.example/cb"]) for n in range(4)]
    target = client("11111111-1111-4111-8111-111111111111")
    api = FakeAdmin([*others, target], page_size=2)
    assert module.reconcile(api) == {"scanned": 5, "matched": 1, "updated": 1}
    assert sum("/clients?" in path for _, path in api.calls) == 4


def test_late_page_failure_does_not_partially_update():
    target = client("11111111-1111-4111-8111-111111111111")
    other = client("22222222-2222-4222-8222-222222222222", ["https://other.example/cb"])
    api = FakeAdmin([target, other], page_size=1)
    original = api.request

    def broken(method, path, body=None):
        if "/clients?" in path and "first=1" in path:
            raise module.BootstrapError("Keycloak admin request failed: HTTP 500")
        return original(method, path, body)

    api.request = broken
    with pytest.raises(module.BootstrapError):
        module.reconcile(api)
    assert not any(method == "PUT" for method, _ in api.calls)


def test_write_assignment_requires_readback():
    target = client("11111111-1111-4111-8111-111111111111")
    api = FakeAdmin([target])
    original = api.request

    def ignored_put(method, path, body=None):
        if method == "PUT":
            return None
        return original(method, path, body)

    api.request = ignored_put
    with pytest.raises(module.BootstrapError, match="did not persist"):
        module.reconcile(api)


@pytest.mark.parametrize("problem", ["ambiguous_scope", "missing_read", "write_is_default", "api_failure"])
def test_failures_never_mutate(problem):
    target = client("11111111-1111-4111-8111-111111111111")
    api = FakeAdmin([target])
    if problem == "ambiguous_scope":
        api.scopes.append(copy.deepcopy(api.scopes[1]))
    elif problem == "missing_read":
        api.optional[target["id"]] = [{"id": "offline-id", "name": "offline_access"}]
    elif problem == "write_is_default":
        api.defaults[target["id"]].append(api.scopes[1])
    else:
        original = api.request

        def broken(method, path, body=None):
            if "/optional-client-scopes" in path:
                raise module.BootstrapError("Keycloak admin request failed: HTTP 500")
            return original(method, path, body)

        api.request = broken
    with pytest.raises(module.BootstrapError):
        module.reconcile(api)
    assert not any(method == "PUT" for method, _ in api.calls)


def test_aliases_use_existing_private_keycloak_environment():
    env = module.admin_environment({"KEYCLOAK_ADMIN": "admin", "KEYCLOAK_ADMIN_PASSWORD": "private-value"})
    assert env["KC_ADMIN_USERNAME"] == "admin"
    assert env["KC_ADMIN_PASSWORD"] == "private-value"
    assert module.admin_environment({"KEYCLOAK_ADMIN_PASSWORD": "private-value"})["KC_ADMIN_USERNAME"] == "admin"
