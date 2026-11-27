"""The receiving half: verify a service token locally against the realm JWKS and a client role."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient
from tendermate_shared.auth.errors import (
    AuthServerUnavailable,
    InvalidServiceToken,
    MissingServiceRole,
    ServiceAuthError,
)
from tendermate_shared.observability import metrics

logger = logging.getLogger(__name__)


def jwks_url_for(issuer: str) -> str:
    """The realm's JWKS endpoint for an issuer URL (``…/realms/<realm>``)."""
    return f"{issuer.rstrip('/')}/protocol/openid-connect/certs"


@dataclass(frozen=True)
class ServiceCaller:
    """The Keycloak client behind a verified service token."""

    client_id: str  # token "azp": realm-imported clients have no "client_id" claim
    subject: str  # token "sub": the client's service-account user id


def bearer_token(value: str | None) -> str | None:
    """``"Bearer <token>"`` (scheme case-insensitive) → ``"<token>"``; anything else → None."""
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class ServiceTokenVerifier:
    """Verifies a service token for THIS service (``client_id``, the token audience).

    Checks: RS256 signature by the realm JWKS, ``iss``, ``aud`` contains ``client_id``,
    ``exp``/``iat``/``sub`` present (30 s leeway), ``typ`` = ``Bearer``, and the required role in
    ``resource_access[client_id].roles``. A user's token for the same audience lacks the role.

    :meth:`access_token_claims` is the same check without the role, for a service that also takes
    users' tokens (the backend): one JWKS cache and one warm-up for both kinds.
    """

    def __init__(self, *, issuer: str, client_id: str, jwks_url: str | None = None) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        # Caches the JWK set for an hour; an unknown kid (key rotation) forces a refetch.
        self._jwks = PyJWKClient(
            jwks_url or jwks_url_for(issuer), cache_keys=True, lifespan=3600, timeout=5
        )

    async def warm_up(self) -> None:
        """Prefetch the JWK set at startup; a failure is only logged (Keycloak may start later)."""
        try:
            await asyncio.to_thread(self._jwks.get_jwk_set)
        except jwt.PyJWKClientError as exc:
            logger.warning("Keycloak JWKS not reachable at startup: %s", exc)

    async def verify(self, token: str | None, *, role: str) -> ServiceCaller:
        """The caller of a valid token with ``role``; raises a :class:`ServiceAuthError` otherwise.

        InvalidServiceToken → 401, MissingServiceRole → 403, AuthServerUnavailable → JWKS down.
        """
        try:
            caller = await self._verify(token, role)
        except ServiceAuthError as exc:
            metrics.count("service_auth.verify", 1, attributes={"result": _result_of(exc)})
            raise
        metrics.count("service_auth.verify", 1, attributes={"result": "ok"})
        return caller

    async def access_token_claims(self, token: str | None) -> dict[str, Any]:
        """Claims of a valid access token for this audience, any caller and roles.

        InvalidServiceToken (cause: the PyJWT error, e.g. ExpiredSignatureError) → 401,
        AuthServerUnavailable → JWKS down. Not counted in ``service_auth.verify``.
        """
        if not token:
            raise InvalidServiceToken("No bearer token")
        try:
            # PyJWKClient fetches with blocking urllib on a cache miss.
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWKClientConnectionError as exc:  # a PyJWTError too: must come first
            raise AuthServerUnavailable("Keycloak JWKS unreachable") from exc
        except jwt.PyJWTError as exc:
            # Names the failed check ("Audience doesn't match"), never the token itself.
            raise InvalidServiceToken(str(exc)) from exc
        # ID tokens are signed with the same key; only access tokens may call a service.
        if claims.get("typ") != "Bearer":
            raise InvalidServiceToken("Not an access token")
        return claims

    async def _verify(self, token: str | None, role: str) -> ServiceCaller:
        claims = await self.access_token_claims(token)
        caller = ServiceCaller(client_id=str(claims.get("azp") or ""), subject=str(claims["sub"]))
        if role not in _client_roles(claims, self._client_id):
            raise MissingServiceRole(
                f"{caller.client_id or caller.subject} lacks role {self._client_id}:{role}"
            )
        return caller


def _client_roles(claims: dict[str, Any], client_id: str) -> list[Any]:
    access = claims.get("resource_access")
    own = access.get(client_id) if isinstance(access, dict) else None
    roles = own.get("roles") if isinstance(own, dict) else None
    return roles if isinstance(roles, list) else []


def _result_of(exc: ServiceAuthError) -> str:
    if isinstance(exc, MissingServiceRole):
        return "forbidden"
    if isinstance(exc, AuthServerUnavailable):
        return "unavailable"
    return "invalid"
