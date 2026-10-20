"""Immutable domain data structures and PEP 742 TypeIs narrowing guards for Stream Weaver GitOps."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeIs

type JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
type JsonDict = dict[str, Any]
type EnvMap = Mapping[str, str]
type ServiceId = Literal["aiostreams", "aiometadata", "aiomanager"]

ALL_SERVICES: tuple[ServiceId, ...] = ("aiostreams", "aiometadata", "aiomanager")


def is_json_mapping(val: Any) -> TypeIs[Mapping[str, Any]]:
    """PEP 742 bidirectional type guard narrowing arbitrary values to `Mapping[str, Any]`."""
    return isinstance(val, Mapping)


def is_json_dict(val: Any) -> TypeIs[JsonDict]:
    """PEP 742 bidirectional type guard narrowing arbitrary values to mutable `JsonDict`."""
    return isinstance(val, dict)


def is_json_list(val: Any) -> TypeIs[list[Any]]:
    """PEP 742 bidirectional type guard narrowing arbitrary values to `list[Any]`."""
    return isinstance(val, list)


@dataclass(frozen=True, slots=True)
class ServicePushOutcome:
    """Result of pushing a configuration to an AIO service."""

    service: ServiceId
    uuid: str
    created: bool
    install_url: str | None = None
    synced_clients: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfigDiffOutcome:
    """Immutable structural and unified diff summary between local and remote configs."""

    target: str
    in_sync: bool
    changed_keys: tuple[str, ...] = ()
    added_lines: int = 0
    removed_lines: int = 0
    unified_diff: str = ""
