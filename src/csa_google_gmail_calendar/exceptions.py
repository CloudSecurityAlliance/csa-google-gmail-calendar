"""Exception hierarchy for csa-google-gmail-calendar. Every error this package raises on
purpose is a `CsaGoogleError` subclass, so callers can catch the base class and still see
what kind of failure they got from a specific one. Behaviour (retry hints, HTTP status
mapping, etc.) is added by later tasks as they need it — this is just the shape.
"""
from __future__ import annotations


class CsaGoogleError(Exception):
    """Base class for every error this package raises on purpose."""


class AuthError(CsaGoogleError):
    """Raised when OAuth credentials are missing, expired, or rejected by Google."""


class NotFoundError(CsaGoogleError):
    """Raised when a requested message, thread, draft, event, or calendar does not exist."""


class AccessError(CsaGoogleError):
    """Raised when the authenticated account lacks permission for the requested operation."""


class ApiError(CsaGoogleError):
    """Raised when the Google API returns an error this package cannot otherwise classify."""


class PolicyError(CsaGoogleError):
    """Raised when a request would violate this server's own policy, independent of Google."""


class UnsupportedOperation(CsaGoogleError):
    """Raised when the caller asks for something this package deliberately does not implement."""


class ConflictError(CsaGoogleError):
    """Raised when a write would conflict with concurrent state, e.g. a stale etag or sequence."""
