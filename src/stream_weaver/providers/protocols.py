"""Structural `typing.Protocol` interfaces for Stream Weaver GitOps providers."""

from collections.abc import Mapping
from typing import Any, Protocol

from stream_weaver.core.instances import InstanceBinding
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
    ) -> Result[tuple[str, dict[str, str]]]:
        """
        Pushes the account's addon list to the client platform.
        Returns `Ok((summary_message, newly_refreshed_secrets))`.
        """
        ...


class ConfigServiceProtocol(Protocol):
    """
    Protocol for GitOps-managed AIO services (AIOStreams, AIOMetadata, AIOManager).

    Adapters are addressed by an `InstanceBinding` (one named instance of the service); they
    never read instance credentials from the environment themselves. `env` is passed only for
    secondary needs such as secret injection into downstream client syncs.
    """

    @property
    def service_id(self) -> ServiceId: ...

    def pull_remote(self, binding: InstanceBinding) -> Result[JsonDict]: ...

    def push_remote(
        self,
        config: JsonDict,
        binding: InstanceBinding,
        env: EnvMap,
        *,
        create: bool = False,
    ) -> Result[tuple[ServicePushOutcome, JsonDict, dict[str, str]]]:
        """
        Pushes `config` to the remote instance.
        Returns `Ok((outcome, updated_config_to_persist, newly_discovered_secrets))`.
        """
        ...
