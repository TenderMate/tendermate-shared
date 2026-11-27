from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from tendermate_shared.auth import (
    AuthServerUnavailable,
    ClientCredentialsAuth,
    ServiceTokenProvider,
    TokenRequestFailed,
    token_url_for,
)

TOKEN_URL = "http://keycloak.test/realms/tendermate/protocol/openid-connect/token"
CALLEE_URL = "http://callee.test/api/v1/parse"
SECRET = "s3cret-client-secret"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Keycloak:
    """Token endpoint stand-in: issues t1, t2, … and records each form it got."""

    def __init__(self, *, expires_in: int = 300, delay: float = 0.0) -> None:
        self.forms: list[dict[str, str]] = []
        self.expires_in = expires_in
        self.delay = delay
        self.fail: Callable[[], httpx.Response] | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            return self.fail()
        body = {
            "access_token": f"t{len(self.forms)}",
            "expires_in": self.expires_in,
            "token_type": "Bearer",
        }
        return httpx.Response(200, json=body)


def _provider(
    handler: Callable[[httpx.Request], Any], clock: _Clock | None = None
) -> ServiceTokenProvider:
    return ServiceTokenProvider(
        token_url=TOKEN_URL,
        client_id="tendermate-backend",
        client_secret=SECRET,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=clock or _Clock(),
    )


async def test_token_is_requested_once_and_cached() -> None:
    keycloak = _Keycloak()
    provider = _provider(keycloak.handle)

    assert await provider.token() == "t1"
    assert await provider.token() == "t1"

    assert keycloak.forms == [
        {
            "grant_type": "client_credentials",
            "client_id": "tendermate-backend",
            "client_secret": SECRET,
        }
    ]


async def test_scope_is_requested_when_given() -> None:
    # One provider per callee: the optional client scope that adds only that callee's role.
    keycloak = _Keycloak()
    provider = ServiceTokenProvider(
        token_url=TOKEN_URL,
        client_id="tendermate-backend",
        client_secret=SECRET,
        scope="tm-search",
        http=httpx.AsyncClient(transport=httpx.MockTransport(keycloak.handle)),
    )

    await provider.token()

    assert keycloak.forms[0]["scope"] == "tm-search"


async def test_token_is_renewed_a_minute_before_expiry() -> None:
    keycloak, clock = _Keycloak(expires_in=300), _Clock()
    provider = _provider(keycloak.handle, clock)
    await provider.token()

    clock.now += 239
    assert await provider.token() == "t1"
    clock.now += 1
    assert await provider.token() == "t2"


async def test_short_lived_token_is_renewed_at_half_its_lifetime() -> None:
    keycloak, clock = _Keycloak(expires_in=60), _Clock()
    provider = _provider(keycloak.handle, clock)
    await provider.token()

    clock.now += 29
    assert await provider.token() == "t1"
    clock.now += 1
    assert await provider.token() == "t2"


async def test_concurrent_callers_share_one_request() -> None:
    keycloak = _Keycloak(delay=0.01)
    provider = _provider(keycloak.handle)

    tokens = await asyncio.gather(*(provider.token() for _ in range(10)))

    assert tokens == ["t1"] * 10
    assert len(keycloak.forms) == 1


async def test_invalidate_forces_a_new_request() -> None:
    keycloak = _Keycloak()
    provider = _provider(keycloak.handle)
    await provider.token()

    provider.invalidate()

    assert await provider.token() == "t2"


async def test_rejected_credentials_raise_with_keycloak_error_but_not_the_secret() -> None:
    keycloak = _Keycloak()
    keycloak.fail = lambda: httpx.Response(
        401,
        json={
            "error": "unauthorized_client",
            "error_description": "Invalid client or Invalid client credentials",
        },
    )
    provider = _provider(keycloak.handle)

    with pytest.raises(TokenRequestFailed) as exc_info:
        await provider.token()

    message = str(exc_info.value)
    assert "unauthorized_client" in message
    assert "tendermate-backend" in message
    assert SECRET not in message


def _raises(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503, text="Service Unavailable"),
        lambda request: httpx.Response(200, text="<html>proxy page</html>"),
        _raises(httpx.ConnectError("connection refused")),
        _raises(httpx.ReadTimeout("timed out")),
    ],
    ids=["503", "not-json", "connect-error", "read-timeout"],
)
async def test_unreachable_keycloak_raises_auth_server_unavailable(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    provider = _provider(handler)

    with pytest.raises(AuthServerUnavailable):
        await provider.token()


async def test_concurrent_callers_share_a_failure_and_the_next_call_retries() -> None:
    keycloak = _Keycloak(delay=0.01)
    keycloak.fail = lambda: httpx.Response(503)
    provider = _provider(keycloak.handle)

    results = await asyncio.gather(*(provider.token() for _ in range(5)), return_exceptions=True)

    assert all(isinstance(result, AuthServerUnavailable) for result in results)
    assert len(keycloak.forms) == 1

    keycloak.fail = None
    assert await provider.token() == "t2"


async def test_aclose_closes_only_its_own_client() -> None:
    injected = httpx.AsyncClient(transport=httpx.MockTransport(_Keycloak().handle))
    provider = ServiceTokenProvider(
        token_url=TOKEN_URL, client_id="c", client_secret="s", http=injected
    )
    await provider.aclose()
    assert not injected.is_closed

    own = ServiceTokenProvider(token_url=TOKEN_URL, client_id="c", client_secret="s")
    await own.aclose()
    assert own._http.is_closed


def test_token_url_for_strips_trailing_slash() -> None:
    assert token_url_for("http://kc/realms/tendermate/") == (
        "http://kc/realms/tendermate/protocol/openid-connect/token"
    )


# --- ClientCredentialsAuth -------------------------------------------------------------------


class _Callee:
    """Service stand-in: answers with the given statuses in order, records what it received."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        # (Authorization, body) as sent: httpx resends the same, mutated Request object.
        self.received: list[tuple[str, bytes]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.received.append((request.headers["Authorization"], request.content))
        return httpx.Response(self.statuses.pop(0))


def _callee_client(callee: _Callee) -> tuple[httpx.AsyncClient, _Keycloak]:
    keycloak = _Keycloak()
    provider = _provider(keycloak.handle)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(callee.handle), auth=ClientCredentialsAuth(provider)
    )
    return client, keycloak


async def test_auth_sends_the_bearer_token() -> None:
    callee = _Callee(200)
    client, keycloak = _callee_client(callee)

    response = await client.get(CALLEE_URL)

    assert response.status_code == 200
    assert [auth for auth, _ in callee.received] == ["Bearer t1"]
    assert len(keycloak.forms) == 1


async def test_auth_renews_the_token_and_resends_once_after_401() -> None:
    callee = _Callee(401, 200)
    client, keycloak = _callee_client(callee)

    response = await client.get(CALLEE_URL)

    assert response.status_code == 200
    assert [auth for auth, _ in callee.received] == ["Bearer t1", "Bearer t2"]
    assert len(keycloak.forms) == 2


async def test_auth_returns_the_second_401_without_looping() -> None:
    callee = _Callee(401, 401)
    client, _ = _callee_client(callee)

    response = await client.get(CALLEE_URL)

    assert response.status_code == 401
    assert len(callee.received) == 2


async def test_auth_resends_a_multipart_body_intact() -> None:
    callee = _Callee(401, 200)
    client, _ = _callee_client(callee)
    data = b"PK\x03\x04 spreadsheet bytes"

    response = await client.post(CALLEE_URL, files={"file": ("tender.xlsx", data)})

    assert response.status_code == 200
    first, second = (body for _, body in callee.received)
    assert first == second
    assert data in second


def test_auth_refuses_a_sync_client() -> None:
    provider = _provider(_Keycloak().handle)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        auth=ClientCredentialsAuth(provider),
    )

    with pytest.raises(RuntimeError, match="async-only"):
        client.get(CALLEE_URL)
