"""
Named service instances.

`tenant.json` registers, per service, a map of *instance name → env key prefix*. This module
turns such a registration plus the tenant's `.env` into an immutable `InstanceBinding` that
the service adapters consume — they never read `AIOMETADATA_*` style keys themselves.

Env key scheme (prefix `P`):

    aiostreams  : P_URL  P_UUID  P_PASSWORD  P_ADDON_PASSWORD
    aiometadata : P_URL  P_UUID  P_PASSWORD  P_ADDON_PASSWORD
    aiomanager  : P_URL  P_UUID  P_SYNC_PASSWORD

A non-default instance may omit `P_URL`; it then inherits the URL of the service's `default`
instance (same server, different user). Everything here is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.tenant import TenantManifest
from stream_weaver.core.types import ALL_SERVICES, EnvMap, ServiceId

DEFAULT_INSTANCE = "default"

_PASSWORD_SUFFIX: Mapping[ServiceId, str] = {
    "aiostreams": "PASSWORD",
    "aiometadata": "PASSWORD",
    "aiomanager": "SYNC_PASSWORD",
}
_HAS_ADDON_PASSWORD: frozenset[ServiceId] = frozenset({"aiostreams", "aiometadata"})


@dataclass(frozen=True, slots=True)
class InstanceBinding:
    """Resolved credentials for one named instance of one service."""

    service: ServiceId
    name: str
    prefix: str
    url: str
    uuid: str
    password: str
    addon_password: str = ""
    url_from_prefix: str | None = None
    """Prefix whose `_URL` actually supplied `url` when inherited from the default instance."""

    @property
    def label(self) -> str:
        return f"{self.service}/{self.name}"

    @property
    def url_env_key(self) -> str:
        """The env key documents should reference for this instance's base URL (resolves on every tenant)."""
        return f"{self.url_from_prefix or self.prefix}_URL"

    @property
    def uuid_env_key(self) -> str:
        return f"{self.prefix}_UUID"

    @property
    def password_env_key(self) -> str:
        return f"{self.prefix}_{_PASSWORD_SUFFIX[self.service]}"

    @property
    def addon_password_env_key(self) -> str:
        return f"{self.prefix}_ADDON_PASSWORD"

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")

    def configured_for_pull(self) -> bool:
        return bool(self.url and self.uuid and self.password)

    def configured_for_push(self, *, create: bool = False) -> bool:
        return bool(self.url and self.password and (create or self.uuid))

    def with_uuid(self, uuid: str) -> InstanceBinding:
        return InstanceBinding(
            service=self.service,
            name=self.name,
            prefix=self.prefix,
            url=self.url,
            uuid=uuid,
            password=self.password,
            addon_password=self.addon_password,
            url_from_prefix=self.url_from_prefix,
        )


def instance_env_keys(service: ServiceId, prefix: str) -> tuple[str, ...]:
    """All env keys an instance with `prefix` may use, in canonical `.env` order."""
    keys = [f"{prefix}_URL", f"{prefix}_UUID", f"{prefix}_{_PASSWORD_SUFFIX[service]}"]
    if service in _HAS_ADDON_PASSWORD:
        keys.append(f"{prefix}_ADDON_PASSWORD")
    return tuple(keys)


def instance_prefix(
    manifest: TenantManifest, service: ServiceId, name: str
) -> Result[str, ConfigValidationError]:
    registry = manifest.instances.get(service, {})
    prefix = registry.get(name)
    if prefix is None:
        known = ", ".join(sorted(registry)) or "none"
        return Err(
            ConfigValidationError(
                f"tenant `{manifest.name}` has no `{service}` instance named `{name}` "
                f"(known: {known}); register it in tenant.json or run `sw instance new {service} {name}`",
                service=service,
            )
        )
    return Ok(prefix)


def resolve_instance_binding(
    manifest: TenantManifest,
    env: EnvMap,
    service: ServiceId,
    name: str = DEFAULT_INSTANCE,
) -> Result[InstanceBinding, ConfigValidationError]:
    """Looks up `service/name` in the tenant manifest and binds its credentials from `env`."""
    match instance_prefix(manifest, service, name):
        case Err() as err:
            return err
        case Ok(prefix):
            pass

    url = env.get(f"{prefix}_URL", "")
    url_from_prefix: str | None = None
    if not url and name != DEFAULT_INSTANCE:
        default_prefix = manifest.instances.get(service, {}).get(DEFAULT_INSTANCE)
        if default_prefix:
            url = env.get(f"{default_prefix}_URL", "")
            url_from_prefix = default_prefix if url else None

    return Ok(
        InstanceBinding(
            service=service,
            name=name,
            prefix=prefix,
            url=url,
            uuid=env.get(f"{prefix}_UUID", ""),
            password=env.get(f"{prefix}_{_PASSWORD_SUFFIX[service]}", ""),
            addon_password=env.get(f"{prefix}_ADDON_PASSWORD", "")
            if service in _HAS_ADDON_PASSWORD
            else "",
            url_from_prefix=url_from_prefix,
        )
    )


def resolve_default_bindings(
    manifest: TenantManifest, env: EnvMap
) -> dict[ServiceId, InstanceBinding]:
    """Bindings for every service's `default` instance that is registered (missing ones are skipped)."""
    out: dict[ServiceId, InstanceBinding] = {}
    for svc in ALL_SERVICES:
        match resolve_instance_binding(manifest, env, svc, DEFAULT_INSTANCE):
            case Ok(binding):
                out[svc] = binding
            case Err():
                continue
    return out
