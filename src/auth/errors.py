"""Errors of service-to-service authentication (:mod:`tendermate_shared.auth`)."""

from __future__ import annotations


class ServiceAuthError(Exception):
    """Base of every service-to-service auth failure."""


class TokenRequestFailed(ServiceAuthError):
    """Keycloak refused this service's credentials (4xx: wrong secret, disabled client)."""


class AuthServerUnavailable(ServiceAuthError):
    """Keycloak (token endpoint or JWKS) is unreachable, timed out or answered 5xx."""


class InvalidServiceToken(ServiceAuthError):
    """Missing, malformed, expired or foreign token: the callee answers 401 / UNAUTHENTICATED."""


class MissingServiceRole(ServiceAuthError):
    """Valid token without the required client role: the callee answers 403 / PERMISSION_DENIED."""
