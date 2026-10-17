"""Immutable domain error types for Stream Weaver GitOps."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StreamWeaverError:
    """Base immutable domain error."""

    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SecretError(StreamWeaverError):
    """Raised when required secrets are missing during config injection."""

    missing_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CryptoError(StreamWeaverError):
    """Raised when encryption or decryption of AIOManager envelopes fails."""


@dataclass(frozen=True, slots=True)
class ConfigValidationError(StreamWeaverError):
    """Raised when a service configuration payload has an invalid structure."""

    service: str = ""


@dataclass(frozen=True, slots=True)
class NetworkError(StreamWeaverError):
    """Raised when an HTTP request fails or returns an unexpected status."""

    status_code: int = 0
    url: str = ""


@dataclass(frozen=True, slots=True)
class StorageError(StreamWeaverError):
    """Raised when a filesystem operation or path boundary check fails."""

    path: str = ""
