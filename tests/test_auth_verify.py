from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from tendermate_shared.auth import (
    AuthServerUnavailable,
    InvalidServiceToken,
    MissingServiceRole,
    ServiceCaller,
    ServiceTokenVerifier,
    bearer_token,
    jwks_url_for,
)
from tendermate_shared.auth import verify as verify_module

ISSUER = "http://keycloak.test/realms/tendermate"
CALLEE = "tendermate-searching-agent"
SUB = str(uuid.uuid4())

# Stand-in for the realm's signing key; the patched JWKS endpoint publishes its public half.
KID = "test-kid"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS: dict[str, Any] = {
    "keys": [
        {
            **RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key(), as_dict=True),
            "kid": KID,
            "use": "sig",
            "alg": "RS256",
        }
    ]
}


def make_token(
    *, key: Any = PRIVATE_KEY, kid: str = KID, algorithm: str = "RS256", **overrides: Any
) -> str:
    """A Keycloak-shaped service token of tendermate-backend; an override of None drops a claim."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CALLEE,
        "azp": "tendermate-backend",
        "typ": "Bearer",
        "sub": SUB,
        "iat": now,
        "exp": now + 900,
        "preferred_username": "service-account-tendermate-backend",
        "resource_access": {CALLEE: {"roles": ["search"]}},
    } | overrides
    payload = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})


@pytest.fixture(autouse=True)
def _jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    # PyJWKClient.fetch_data is the documented override point: no network in tests.
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: JWKS)


@pytest.fixture
def verifier() -> ServiceTokenVerifier:
    return ServiceTokenVerifier(issuer=ISSUER, client_id=CALLEE)


@pytest.mark.parametrize("aud", [CALLEE, [CALLEE, "tendermate-import-agent"]])
async def test_valid_token_with_role_returns_the_caller(
    verifier: ServiceTokenVerifier, aud: Any
) -> None:
    caller = await verifier.verify(make_token(aud=aud), role="search")

    assert caller == ServiceCaller(client_id="tendermate-backend", subject=SUB)


async def test_issuer_with_trailing_slash_is_accepted() -> None:
    verifier = ServiceTokenVerifier(issuer=f"{ISSUER}/", client_id=CALLEE)

    assert (await verifier.verify(make_token(), role="search")).client_id == "tendermate-backend"


@pytest.mark.parametrize(
    "resource_access",
    [
        None,
        {CALLEE: {"roles": ["other"]}},
        {"tendermate-import-agent": {"roles": ["search"]}},  # the role of another client
        {CALLEE: {"roles": "search"}},
    ],
    ids=["no-resource-access", "other-role", "other-client", "malformed"],
)
async def test_token_without_the_role_is_forbidden(
    verifier: ServiceTokenVerifier, resource_access: Any
) -> None:
    with pytest.raises(MissingServiceRole, match=f"tendermate-backend lacks role {CALLEE}:search"):
        await verifier.verify(make_token(resource_access=resource_access), role="search")


def _expired() -> str:
    now = int(time.time())
    return make_token(iat=now - 1000, exp=now - 100)


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: make_token(aud="tendermate-backend"), id="wrong-aud"),
        pytest.param(lambda: make_token(iss="http://evil.test/realms/tendermate"), id="wrong-iss"),
        pytest.param(_expired, id="expired"),
        pytest.param(lambda: make_token(sub=None), id="missing-sub"),
        pytest.param(lambda: make_token(typ="ID"), id="id-token"),
        pytest.param(lambda: make_token(key=OTHER_KEY), id="foreign-signature"),
        pytest.param(
            lambda: make_token(key="a-shared-hmac-secret-of-32-bytes!!", algorithm="HS256"),
            id="hs256",
        ),
        pytest.param(lambda: make_token(key=None, algorithm="none"), id="alg-none"),
        pytest.param(lambda: make_token(kid="unknown-kid"), id="unknown-kid"),
        pytest.param(lambda: "garbage", id="garbage"),
        pytest.param(lambda: "", id="empty"),
        pytest.param(lambda: None, id="none"),
    ],
)
async def test_invalid_token_is_rejected(verifier: ServiceTokenVerifier, token: Any) -> None:
    with pytest.raises(InvalidServiceToken):
        await verifier.verify(token(), role="search")


async def test_unreachable_jwks_is_auth_server_unavailable(
    verifier: ServiceTokenVerifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(self: jwt.PyJWKClient) -> Any:
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", down)

    with pytest.raises(AuthServerUnavailable):
        await verifier.verify(make_token(), role="search")


async def test_warm_up_failure_is_only_logged(
    verifier: ServiceTokenVerifier,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def down(self: jwt.PyJWKClient) -> Any:
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", down)

    with caplog.at_level(logging.WARNING):
        await verifier.warm_up()

    assert "JWKS not reachable" in caplog.text


async def test_verify_counts_each_result(
    verifier: ServiceTokenVerifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    results: list[str] = []
    monkeypatch.setattr(
        verify_module.metrics,
        "count",
        lambda name, value, attributes=None, **_: results.append(attributes["result"]),
    )

    await verifier.verify(make_token(), role="search")
    with pytest.raises(MissingServiceRole):
        await verifier.verify(make_token(), role="parse")
    with pytest.raises(InvalidServiceToken):
        await verifier.verify("garbage", role="search")

    assert results == ["ok", "forbidden", "invalid"]


async def test_access_token_claims_need_no_role(verifier: ServiceTokenVerifier) -> None:
    # A user's token (the backend): our audience, no client role of ours.
    claims = await verifier.access_token_claims(
        make_token(azp="tendermate-frontend", resource_access=None)
    )

    assert claims["azp"] == "tendermate-frontend"
    assert claims["sub"] == SUB


async def test_access_token_claims_keep_the_pyjwt_cause(verifier: ServiceTokenVerifier) -> None:
    with pytest.raises(InvalidServiceToken) as raised:
        await verifier.access_token_claims(_expired())

    assert isinstance(raised.value.__cause__, jwt.ExpiredSignatureError)


@pytest.mark.parametrize(
    "token",
    [None, "", make_token(typ="ID"), make_token(aud="account")],
    ids=["none", "empty", "id-token", "other-audience"],
)
async def test_access_token_claims_reject_what_verify_rejects(
    verifier: ServiceTokenVerifier, token: str | None
) -> None:
    with pytest.raises(InvalidServiceToken):
        await verifier.access_token_claims(token)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER  abc ", "abc"),
        ("Basic abc", None),
        ("Bearer", None),
        ("Bearer ", None),
        ("", None),
        (None, None),
    ],
)
def test_bearer_token(value: str | None, expected: str | None) -> None:
    assert bearer_token(value) == expected


def test_jwks_url_for_strips_trailing_slash() -> None:
    assert jwks_url_for(f"{ISSUER}/") == f"{ISSUER}/protocol/openid-connect/certs"
