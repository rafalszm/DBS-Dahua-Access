"""Exceptions raised by the Dahua access adapter."""

from __future__ import annotations


class DahuaError(Exception):
    """Base Dahua access error."""


class DahuaSdkUnavailable(DahuaError):
    """Dahua NetSDK Python bindings or native libraries are unavailable."""


class DahuaConnectionError(DahuaError):
    """The controller cannot be reached or returned an SDK error."""


class DahuaAuthError(DahuaConnectionError):
    """The controller rejected the supplied credentials."""
