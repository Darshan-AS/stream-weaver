"""Structural `typing.Protocol` interfaces for Stream Weaver GitOps providers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from stream_weaver.core.errors import StreamWeaverError
from stream_weaver.core.result import Result
from stream_weaver.core.types import EnvMap, JsonDict, ServiceId, ServicePushOutcome


class ClientSyncProtocol(Protocol):
    """Protocol for direct streaming app synchronization (Nuvio, Stremio, etc.)."""

    @property
    def platform_id(self) -> str: ...

    def sync_account(
        self,
        account: Mapping[str, Any],
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
    ) -> Result[tuple[str, dict[str, str]], StreamWeaverError]:
        """
        Pushes the account's addon list to the client platform.
        Returns `Ok((summary_message, newly_refreshed_secrets))`.
        """
        ...


class ConfigServiceProtocol(Protocol):
    """Protocol for GitOps-managed AIO services (AIOStreams, AIOMetadata, AIOManager)."""

    @property
    def service_id(self) -> ServiceId: ...

    def is_configured_for_pull(self, env: EnvMap) -> bool: ...

    def is_configured_for_push(self, env: EnvMap, *, create: bool = False) -> bool: ...

    def pull_remote(self, env: EnvMap) -> Result[JsonDict, StreamWeaverError]: ...

    def push_remote(
        self,
        config: JsonDict,
        env: EnvMap,
        *,
        create: bool = False,
    ) -> Result[tuple[ServicePushOutcome, JsonDict, dict[str, str]], StreamWeaverError]:
        """
        Pushes `config` to the remote server.
        Returns `Ok((outcome, updated_config_to_persist, newly_discovered_secrets))`.
        """
        ...
