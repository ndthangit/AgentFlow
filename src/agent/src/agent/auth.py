"""OIDC access-token verification for the HTTP boundary."""

import os
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from agent.contracts import AgentRunError


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    audience: str
    jwks_url: str

    @classmethod
    def from_env(cls) -> "OidcSettings":
        issuer = os.getenv("OIDC_ISSUER", "").strip().rstrip("/")
        audience = os.getenv("OIDC_AUDIENCE", "").strip()
        jwks_url = os.getenv("OIDC_JWKS_URL", "").strip()
        if not issuer or not audience:
            raise AgentRunError(
                "CONFIGURATION_ERROR",
                "Set OIDC_ISSUER and OIDC_AUDIENCE when AUTH_ENABLED=true.",
            )
        return cls(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url or f"{issuer}/protocol/openid-connect/certs",
        )


class KeycloakTokenVerifier:
    """Verify signed JWTs against Keycloak's rotating JWKS keys."""

    def __init__(self, settings: OidcSettings):
        self.settings = settings
        self.jwks = PyJWKClient(settings.jwks_url, cache_jwk_set=True, lifespan=300)

    def verify(self, token: str) -> dict[str, Any]:
        signing_key = self.jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=self.settings.issuer,
            audience=self.settings.audience,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
