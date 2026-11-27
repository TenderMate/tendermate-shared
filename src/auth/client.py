"""The calling half: this service's Keycloak access token (OAuth2 Client Credentials).

:class:`ServiceTokenProvider` fetches and caches the token; :class:`ClientCredentialsAuth` puts it
on every request of an ``httpx.AsyncClient``. gRPC callers send it themselves as metadata
``{GRPC_AUTH_METADATA_KEY: f"Bearer {await provider.token()}"}``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable, Generator

import httpx
from tendermate_shared.auth.errors import (
    AuthServerUnavailable,
    ServiceAuthError,
    TokenRequestFailed,
)
from tendermate_shared.observability import metrics


def token_url_for(issuer: str) -> str:
    """The realm's token endpoint for an issuer URL (``…/realms/<realm>``)."""
    return f"{issuer.rstrip('/')}/protocol/openid-connect/token"


class ServiceTokenProvider:
    """This service's access token, cached until shortly before it expires.

    Keycloak returns no refresh token for Client Credentials, so a new token is requested when the
    cached one is within ``refresh_margin`` seconds of expiry (or half its lifetime, whichever is
    smaller). Concurrent callers share one request, its failure included.

    ``scope``: the client scope(s) to request (space-separated). A client that calls several
    services takes one provider per callee, each with the optional client scope that adds only
    that callee's role (and so its audience): a callee cannot replay the token on another.
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        timeout: float = 10.0,
        refresh_margin: float = 60.0,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._refresh_margin = refresh_margin
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._renew_at = 0.0
        # Requests finished so far: a caller that waited on the lock while a request failed gets
        # that failure instead of sending another request to a Keycloak that is down.
        self._completed = 0
        self._failure: ServiceAuthError | None = None

    async def token(self) -> str:
        """A valid access token; requests a new one when the cached one is (nearly) expired."""
        seen = self._completed
        async with self._lock:
            if self._token is not None and self._clock() < self._renew_at:
                return self._token
            if self._failure is not None and self._completed != seen:
                raise type(self._failure)(str(self._failure))
            try:
                token, lifetime = await self._request()
            except ServiceAuthError as exc:
                self._completed += 1
                self._failure = exc
                raise
            self._completed += 1
            self._failure = None
            self._token = token
            self._renew_at = self._clock() + lifetime - min(self._refresh_margin, lifetime / 2)
            return token

    def invalidate(self) -> None:
        """Forget the cached token (a callee rejected it); the next token() requests a new one."""
        self._token = None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _request(self) -> tuple[str, float]:
        form = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scope:
            form["scope"] = self._scope
        try:
            response = await self._http.post(self._token_url, data=form)
        except httpx.HTTPError as exc:
            _count("unavailable")
            raise AuthServerUnavailable(
                f"Keycloak token endpoint unreachable: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 500:
            _count("unavailable")
            raise AuthServerUnavailable(
                f"Keycloak token endpoint answered HTTP {response.status_code}"
            )
        if response.status_code != 200:
            _count("rejected")
            # Keycloak's OAuth error body; the secret itself is never part of the message.
            error, description = _oauth_error(response)
            raise TokenRequestFailed(
                f"Keycloak rejected client {self._client_id}: HTTP {response.status_code} "
                f"{error}: {description}"
            )
        try:
            body = response.json()
            token, lifetime = str(body["access_token"]), float(body["expires_in"])
        except (ValueError, KeyError, TypeError) as exc:
            _count("unavailable")
            raise AuthServerUnavailable(
                "Keycloak token endpoint answered an unexpected body"
            ) from exc
        _count("ok")
        return token, lifetime


class ClientCredentialsAuth(httpx.Auth):
    """httpx auth: the provider's Bearer token; a 401 renews the token and resends once."""

    # The resent request must carry its body again (the import upload is multipart).
    requires_request_body = True

    def __init__(self, provider: ServiceTokenProvider) -> None:
        self._provider = provider

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        raise RuntimeError("ClientCredentialsAuth is async-only (httpx.AsyncClient)")

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {await self._provider.token()}"
        response = yield request
        if response.status_code == 401:
            self._provider.invalidate()
            request.headers["Authorization"] = f"Bearer {await self._provider.token()}"
            yield request


def _oauth_error(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "unknown_error", "no OAuth error body"
    if not isinstance(body, dict):
        return "unknown_error", "no OAuth error body"
    return str(body.get("error", "unknown_error")), str(body.get("error_description", ""))


def _count(result: str) -> None:
    metrics.count("service_auth.token", 1, attributes={"result": result})
