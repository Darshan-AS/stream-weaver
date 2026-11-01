"""
Named client logins (Nuvio / Stremio sessions).

`tenant.json` registers, per client platform, a map of *login name → env key prefix*. A login is
one authenticated identity; several profiles (people) may share it (Nuvio profile #1 and #2 on the
same account) or each have their own. Credentials live in `.env` as `SECRET_<PREFIX>_<FIELD>`.

    nuvio   : SECRET_P_ACCESS_TOKEN  SECRET_P_REFRESH_TOKEN  SECRET_P_EMAIL
    stremio : SECRET_P_AUTH_KEY      SECRET_P_EMAIL

The legacy single-login layout (`SECRET_NUVIO_ACCESS_TOKEN`, …) is simply the login whose prefix
is `NUVIO`, so existing tenants need no value migration. Everything here is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.tenant import TenantManifest
from stream_weaver.core.types import EnvMap

type Platform = str

LOGIN_FIELDS: Mapping[Platform, tuple[tuple[str, str], ...]] = {
    # (AIOManager credential field, env suffix)
    "nuvio": (
        ("accessToken", "ACCESS_TOKEN"),
        ("refreshToken", "REFRESH_TOKEN"),
        ("email", "EMAIL"),
    ),
    "stremio": (("authKey", "AUTH_KEY"), ("email", "EMAIL")),
}
SUPPORTED_PLATFORMS: tuple[Platform, ...] = tuple(LOGIN_FIELDS)


def login_env_key(prefix: str, suffix: str) -> str:
    return f"SECRET_{prefix}_{suffix}"


def login_env_keys(platform: Platform, prefix: str) -> tuple[str, ...]:
    return tuple(login_env_key(prefix, suffix) for _, suffix in LOGIN_FIELDS.get(platform, ()))


@dataclass(frozen=True, slots=True)
class LoginBinding:
    """Resolved credentials for one named login on one client platform."""

    platform: Platform
    name: str
    prefix: str
    credentials: Mapping[str, str]  # AIOManager field name → value ("" when unset)

    @property
    def label(self) -> str:
        return f"{self.platform}/{self.name}"

    def env_key(self, field: str) -> str:
        for f, suffix in LOGIN_FIELDS[self.platform]:
            if f == field:
                return login_env_key(self.prefix, suffix)
        raise KeyError(field)

    @property
    def identity_value(self) -> str:
        """The value that identifies the session (refresh token for Nuvio, authKey for Stremio)."""
        if self.platform == "nuvio":
            return self.credentials.get("refreshToken") or self.credentials.get("accessToken", "")
        return self.credentials.get("authKey", "")

    def configured(self) -> bool:
        if self.platform == "nuvio":
            return bool(self.credentials.get("accessToken"))
        return bool(self.credentials.get("authKey"))

    def placeholders(self) -> dict[str, str]:
        """Scrubbed-document form of these credentials: field → `{{SECRET_…}}`."""
        return {
            f: f"{{{{{login_env_key(self.prefix, suffix)}}}}}"
            for f, suffix in LOGIN_FIELDS[self.platform]
        }


def resolve_login_binding(
    manifest: TenantManifest, env: EnvMap, platform: Platform, name: str
) -> Result[LoginBinding, ConfigValidationError]:
    if platform not in LOGIN_FIELDS:
        return Err(ConfigValidationError(f"unsupported client platform `{platform}`"))
    registry = manifest.logins.get(platform, {})
    prefix = registry.get(name)
    if prefix is None:
        known = ", ".join(sorted(registry)) or "none"
        return Err(
            ConfigValidationError(
                f"tenant `{manifest.name}` has no `{platform}` login named `{name}` (known: {known}); "
                f"run `sw login {platform} --login {name}`"
            )
        )
    creds = {f: env.get(login_env_key(prefix, suffix), "") for f, suffix in LOGIN_FIELDS[platform]}
    return Ok(LoginBinding(platform=platform, name=name, prefix=prefix, credentials=creds))


def login_for_identity(
    manifest: TenantManifest, env: EnvMap, platform: Platform, identity_value: str
) -> LoginBinding | None:
    """Finds the registered login whose identity value (refresh token / authKey) equals `identity_value`."""
    if not identity_value:
        return None
    for name in manifest.logins.get(platform, {}):
        match resolve_login_binding(manifest, env, platform, name):
            case Ok(b) if b.identity_value == identity_value:
                return b
            case _:
                continue
    return None


def refreshed_token_env_keys(env: EnvMap, access_token: str, refresh_token: str) -> tuple[str, str]:
    """
    Decides where rotated Nuvio tokens are persisted. If the current tokens were loaded from
    `SECRET_<P>_ACCESS_TOKEN` / `SECRET_<P>_REFRESH_TOKEN`, the same keys are reused (login-aware);
    otherwise the legacy platform-level keys are used.
    """
    access_key = next(
        (k for k, v in env.items() if v and v == access_token and k.endswith("_ACCESS_TOKEN")), ""
    )
    refresh_key = next(
        (k for k, v in env.items() if v and v == refresh_token and k.endswith("_REFRESH_TOKEN")), ""
    )
    if access_key and not refresh_key:
        refresh_key = access_key.removesuffix("_ACCESS_TOKEN") + "_REFRESH_TOKEN"
    if refresh_key and not access_key:
        access_key = refresh_key.removesuffix("_REFRESH_TOKEN") + "_ACCESS_TOKEN"
    return access_key or "SECRET_NUVIO_ACCESS_TOKEN", refresh_key or "SECRET_NUVIO_REFRESH_TOKEN"
