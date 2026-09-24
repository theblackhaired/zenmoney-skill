"""Authenticated Streamable HTTP entrypoint: uvicorn zenmoney_mcp.remote:create_app --factory."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .auth import READ_SCOPE, WRITE_SCOPE, AuthSettings, InvalidToken, TokenVerifier

MAX_BODY_SIZE = 1024 * 1024


class ProtectedMCP:
    def __init__(self, app, settings: AuthSettings, read_tools: frozenset[str], verifier=None):
        self.app = app
        self.settings = settings
        self.read_tools = read_tools
        self.verifier = verifier or TokenVerifier(settings)

    async def _error(self, scope, receive, send, status, error, required=READ_SCOPE):
        headers = {"Cache-Control": "no-store"}
        if status in (401, 403):
            headers["WWW-Authenticate"] = (
                f'Bearer resource_metadata="{self.settings.metadata_url}", '
                f'error="{error}", scope="{required}"'
            )
        await JSONResponse({"error": error}, status_code=status, headers=headers)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        path = scope["path"]
        if path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            if scope["method"] not in ("GET", "HEAD"):
                return await self._error(scope, receive, send, 405, "method_not_allowed")
            return await JSONResponse({
                "resource": self.settings.resource,
                "authorization_servers": [self.settings.issuer],
                "scopes_supported": [READ_SCOPE, WRITE_SCOPE],
                "bearer_methods_supported": ["header"],
            }, headers={"Cache-Control": "no-store"})(scope, receive, send)
        if path != "/mcp":
            return await self._error(scope, receive, send, 404, "not_found")

        auth_headers = Headers(scope=scope).getlist("authorization")
        parts = auth_headers[0].split() if len(auth_headers) == 1 else []
        if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 16384:
            return await self._error(scope, receive, send, 401, "invalid_token")
        try:
            scopes = await self.verifier.verify(parts[1])
        except InvalidToken:
            return await self._error(scope, receive, send, 401, "invalid_token")
        if READ_SCOPE not in scopes:
            return await self._error(scope, receive, send, 403, "insufficient_scope")

        # Inspect the same bounded bytes the MCP server will consume, before any dispatch.
        body = bytearray()
        if scope["method"] == "POST":
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_BODY_SIZE:
                    return await self._error(scope, receive, send, 413, "request_too_large")
                if not message.get("more_body", False):
                    break
            try:
                request = json.loads(body)
                if not isinstance(request, dict):
                    raise TypeError()
                if request.get("method") == "tools/call":
                    params = request.get("params")
                    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                        raise TypeError()
                    if params["name"] not in self.read_tools and WRITE_SCOPE not in scopes:
                        return await self._error(scope, receive, send, 403, "insufficient_scope", f"{READ_SCOPE} {WRITE_SCOPE}")
            except (ValueError, TypeError, UnicodeError):
                return await self._error(scope, receive, send, 400, "invalid_request")

            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, replay, send)
        return await self.app(scope, receive, send)


def create_app():
    # Validation happens before loading the financial adapter. No insecure default mode.
    settings = AuthSettings.from_env()
    from .catalog import READ_TOOLS
    from .server import server

    public = urlsplit(settings.resource)
    app = server.streamable_http_app(
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public.netloc],
            allowed_origins=[f"{public.scheme}://{public.netloc}"],
        ),
    )
    return ProtectedMCP(app, settings, frozenset(READ_TOOLS))
