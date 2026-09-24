"""OAuth resource-server validation; ZenMoney credentials are never client tokens."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from starlette.concurrency import run_in_threadpool

READ_SCOPE = "finance:read"
WRITE_SCOPE = "finance:write"


@dataclass(frozen=True)
class AuthSettings:
    issuer: str
    jwks_url: str
    resource: str
    allowed_subject: str

    def __post_init__(self):
        for name in ("issuer", "jwks_url", "resource"):
            value = getattr(self, name)
            parts = urlsplit(value)
            if (parts.scheme != "https" or not parts.hostname or parts.username
                    or parts.password or parts.fragment or parts.query
                    or any(ord(char) < 33 or char in '\\"' for char in value)):
                raise ValueError(f"{name} must be an absolute HTTPS URL without credentials, query or fragment")
        if urlsplit(self.resource).path != "/mcp":
            raise ValueError("resource must be the public HTTPS /mcp endpoint")
        if not self.allowed_subject or not self.allowed_subject.strip():
            raise ValueError("allowed_subject must be explicitly configured")

    @classmethod
    def from_env(cls):
        names = ("ZENMONEY_OAUTH_ISSUER", "ZENMONEY_OAUTH_JWKS_URL",
                 "ZENMONEY_MCP_RESOURCE", "ZENMONEY_OAUTH_SUBJECT")
        values = [os.environ.get(name, "") for name in names]
        if not all(values):
            raise ValueError("Remote OAuth requires " + ", ".join(names))
        return cls(*values)

    @property
    def metadata_url(self):
        parts = urlsplit(self.resource)
        return f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource/mcp"


class InvalidToken(Exception):
    """Deliberately carries no token or provider exception details."""


class TokenVerifier:
    def __init__(self, settings: AuthSettings):
        self.settings = settings
        # Only public signing keys are cached. Financial data and access tokens are not.
        self.jwks = jwt.PyJWKClient(settings.jwks_url, lifespan=300, timeout=5)

    def _verify(self, token: str) -> frozenset[str]:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ("RS256", "ES256") or not header.get("kid"):
                raise InvalidToken()
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256", "ES256"],
                audience=self.settings.resource, issuer=self.settings.issuer,
                options={"require": ["iss", "aud", "exp", "sub"], "strict_aud": True},
            )
            if claims["sub"] != self.settings.allowed_subject:
                raise InvalidToken()
            scope = claims.get("scope", "")
            if not isinstance(scope, str):
                raise InvalidToken()
            return frozenset(scope.split())
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OSError):
            raise InvalidToken() from None

    async def verify(self, token: str) -> frozenset[str]:
        return await run_in_threadpool(self._verify, token)
