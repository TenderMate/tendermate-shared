"""Service-to-service authentication with Keycloak access tokens (OAuth2 Client Credentials).

Each TenderMate service is a confidential client of the realm. A caller gets its own access token
(:class:`ServiceTokenProvider`) and sends it as ``Authorization: Bearer <token>``: httpx through
:class:`ClientCredentialsAuth`, gRPC as metadata :data:`GRPC_AUTH_METADATA_KEY`. The callee checks
the token locally against the realm JWKS and requires one of its own client roles
(:class:`ServiceTokenVerifier`), e.g. ``tendermate-searching-agent`` : ``search`` for the backend.
"""

from __future__ import annotations

from tendermate_shared.auth.client import (
    ClientCredentialsAuth,
    ServiceTokenProvider,
    token_url_for,
)
from tendermate_shared.auth.errors import (
    AuthServerUnavailable,
    InvalidServiceToken,
    MissingServiceRole,
    ServiceAuthError,
    TokenRequestFailed,
)
from tendermate_shared.auth.verify import (
    ServiceCaller,
    ServiceTokenVerifier,
    bearer_token,
    jwks_url_for,
)

# gRPC metadata keys are lowercase; the value is "Bearer <token>", as in the HTTP header.
GRPC_AUTH_METADATA_KEY = "authorization"

__all__ = [
    "GRPC_AUTH_METADATA_KEY",
    "AuthServerUnavailable",
    "ClientCredentialsAuth",
    "InvalidServiceToken",
    "MissingServiceRole",
    "ServiceAuthError",
    "ServiceCaller",
    "ServiceTokenProvider",
    "ServiceTokenVerifier",
    "TokenRequestFailed",
    "bearer_token",
    "jwks_url_for",
    "token_url_for",
]
