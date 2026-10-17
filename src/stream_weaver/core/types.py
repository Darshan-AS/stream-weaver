"""Immutable domain data structures for Stream Weaver GitOps."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

type JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
type JsonDict = dict[str, Any]
type EnvMap = Mapping[str, str]
type ServiceId = Literal["aiostreams", "aiometadata", "aiomanager"]

ALL_SERVICES: tuple[ServiceId, ...] = ("aiostreams", "aiometadata", "aiomanager")


@dataclass(frozen=True, slots=True)
class ServicePushOutcome:
    """Result of pushing a configuration to an AIO service."""

    service: ServiceId
    uuid: str
    created: bool
    install_url: str | None = None
    synced_clients: tuple[str, ...] = ()
