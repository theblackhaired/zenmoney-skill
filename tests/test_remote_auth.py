"""Resource-server boundary tests use synthetic identity and fresh generated keys."""
import json
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from zenmoney_mcp.auth import AuthSettings, TokenVerifier
from zenmoney_mcp.remote import ProtectedMCP, create_app


class RemoteAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="test-key", use="sig", alg="RS256")

    def setUp(self):
        self.settings = AuthSettings("https://issuer.example", "https://issuer.example/jwks",
                                     "https://finance.example/mcp", "test-owner")
        self.calls = []

        async def inner(scope, receive, send):
            self.calls.append(scope["method"])
            body = (await receive()).get("body", b"") if scope["method"] == "POST" else b""
            await JSONResponse({"private": "synthetic-financial-result", "body": body.decode()})(scope, receive, send)

        verifier = TokenVerifier(self.settings)
        self.fetch = patch.object(verifier.jwks, "fetch_data", return_value={"keys": [self.jwk]})
        self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.client = TestClient(ProtectedMCP(inner, self.settings, frozenset({"get_accounts"}), verifier))
        self.addCleanup(self.client.close)

    def token(self, **updates):
        claims = {"iss": self.settings.issuer, "aud": self.settings.resource,
                  "sub": "test-owner", "exp": int(time.time()) + 60, "scope": "finance:read"}
        claims.update(updates)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})

    def request(self, token=None, payload=None):
        headers = {"Authorization": "Bearer " + token} if token is not None else {}
        return self.client.post("/mcp", headers=headers, json=payload or {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    def assert_denied(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertEqual(self.calls, [])
        self.assertNotIn("synthetic-financial-result", response.text)
        self.assertNotIn("get_accounts", response.text)

    def test_discovery_public_and_exact_resource(self):
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["resource"], self.settings.resource)
            self.assertEqual(response.json()["authorization_servers"], [self.settings.issuer])
        self.assertEqual(self.calls, [])

    def test_missing_invalid_and_expired_fail_before_dispatch(self):
        for token in (None, "broken", self.token(exp=int(time.time()) - 1)):
            with self.subTest(token_kind="missing" if token is None else "supplied"):
                response = self.request(token)
                self.assert_denied(response, 401)
                self.assertIn(self.settings.metadata_url, response.headers["WWW-Authenticate"])
                self.assertIn('scope="finance:read finance:write"',
                              response.headers["WWW-Authenticate"])

    def test_claim_restrictions(self):
        for updates in ({"aud": "https://other.example/mcp"}, {"aud": [self.settings.resource]},
                        {"iss": "https://other.example"}, {"sub": "another-user"},
                        {"exp": None}, {"scope": ["finance:read"]}, {"nbf": int(time.time()) + 600}):
            with self.subTest(updates=updates):
                self.assert_denied(self.request(self.token(**updates)), 401)

    def test_wrong_signature(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        bad = jwt.encode({"iss": self.settings.issuer, "aud": self.settings.resource,
                          "sub": "test-owner", "exp": int(time.time()) + 60},
                         key, algorithm="RS256", headers={"kid": "test-key"})
        self.assert_denied(self.request(bad), 401)

    def test_missing_required_claims(self):
        for missing in ("iss", "aud", "exp", "sub"):
            claims = jwt.decode(self.token(), options={"verify_signature": False})
            claims.pop(missing)
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})
            self.assert_denied(self.request(token), 401)

    def test_scope_required_for_catalogue(self):
        for scope in ("", "finance:write", "unrelated"):
            self.assert_denied(self.request(self.token(scope=scope)), 403)

    def test_read_token_gets_catalogue_and_read_call(self):
        response = self.request(self.token())
        self.assertEqual(response.status_code, 200)
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_accounts"}}
        response = self.request(self.token(), request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.json()["body"]), request)
        self.assertEqual(len(self.calls), 2)

    def test_write_and_unknown_tool_require_write_scope(self):
        for name in ("create_transaction", "future_tool"):
            payload = {"method": "tools/call", "params": {"name": name, "arguments": {"confirm_write": True}}}
            response = self.request(self.token(), payload)
            self.assert_denied(response, 403)
            self.assertIn("finance:write", response.headers["WWW-Authenticate"])
        response = self.request(self.token(scope="finance:read finance:write"), payload)
        self.assertEqual(response.status_code, 200)

    def test_every_http_method_requires_token(self):
        for method in ("GET", "DELETE", "OPTIONS"):
            self.assert_denied(self.client.request(method, "/mcp"), 401)

    def test_query_token_does_not_authenticate(self):
        self.assert_denied(self.client.get("/mcp", params={"access_token": self.token()}), 401)

    def test_duplicate_authorization_headers_rejected(self):
        response = self.client.post("/mcp", headers=[("Authorization", "Bearer " + self.token())] * 2)
        self.assert_denied(response, 401)

    def test_batch_and_malformed_tool_rejected(self):
        for payload in ([{"method": "tools/call", "params": {"name": "create_transaction"}}],
                        {"method": "tools/call", "params": []}):
            self.assert_denied(self.request(self.token(), payload), 400)

    def test_oversized_body_rejected(self):
        response = self.client.post("/mcp", content=b" " * (1024 * 1024 + 1),
                                    headers={"Authorization": "Bearer " + self.token()})
        self.assert_denied(response, 413)

    def test_no_configuration_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            create_app()

    def test_real_sdk_handshake_and_catalogue(self):
        env = {
            "ZENMONEY_OAUTH_ISSUER": self.settings.issuer,
            "ZENMONEY_OAUTH_JWKS_URL": self.settings.jwks_url,
            "ZENMONEY_MCP_RESOURCE": self.settings.resource,
            "ZENMONEY_OAUTH_SUBJECT": self.settings.allowed_subject,
        }
        with patch.dict("os.environ", env):
            app = create_app()
        with (
            patch.object(app.verifier.jwks, "fetch_data", return_value={"keys": [self.jwk]}),
            TestClient(app, base_url="https://finance.example") as client,
        ):
            listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            denied = client.post("/mcp", json=listing)
            self.assertEqual(denied.status_code, 401)
            self.assertNotIn("get_accounts", denied.text)
            headers = {
                "Authorization": "Bearer " + self.token(),
                "Accept": "application/json, text/event-stream",
            }
            response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "synthetic-test", "version": "1"}},
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("tools", response.json()["result"]["capabilities"])
            headers["MCP-Protocol-Version"] = response.json()["result"]["protocolVersion"]
            response = client.post("/mcp", headers=headers, json=listing)
            self.assertEqual(response.status_code, 200, response.text)
            names = {tool["name"] for tool in response.json()["result"]["tools"]}
            self.assertEqual(len(names), 28)
            self.assertIn("get_accounts", names)
            self.assertIn("create_transaction", names)
            for tool in response.json()["result"]["tools"]:
                scheme = [{"type": "oauth2", "scopes": ["finance:read", "finance:write"]}]
                self.assertEqual(tool["securitySchemes"], scheme, tool["name"])
                self.assertEqual(tool["_meta"]["securitySchemes"], scheme, tool["name"])
            # A successful request does not create an authorization bypass session.
            self.assertEqual(client.post("/mcp", json=listing).status_code, 401)

    def test_unsafe_configuration_rejected(self):
        for resource in ("http://finance.example/mcp", "https://finance.example/", "https://finance.example/mcp?x=1"):
            with self.assertRaises(ValueError):
                AuthSettings(self.settings.issuer, self.settings.jwks_url, resource, "test-owner")


if __name__ == "__main__":
    unittest.main()
