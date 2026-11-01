"""
Managed `accounts[]` for AIOManager — generated from profiles, merged over existing state.

`aiomanager.json` is a raw document that round-trips with the server, except for its `accounts`
list: each profile of the tenant owns exactly one account there. On every push the account is
regenerated from the profile (addons = its instances + extras, connections = its logins) and
*merged* over the existing account so server-assigned fields (ids, auth keys, manifests,
telemetry, `createdAt`, …) survive. Accounts that no profile claims are left untouched and
reported as unmanaged.

All functions operate on the **scrubbed** document (placeholders, not secrets) and are pure.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from stream_weaver.core.instances import InstanceBinding
from stream_weaver.core.logins import LoginBinding
from stream_weaver.core.profiles import (
    Appearance,
    ExtraAddon,
    NuvioClientSpec,
    Profile,
    StremioClientSpec,
    stable_account_id,
    stable_connection_id,
)
from stream_weaver.core.types import JsonDict, ServiceId

_DEFAULT_CAPABILITIES: Mapping[str, tuple[str, ...]] = {
    "nuvio": ("addons", "plugins", "profiles"),
    "stremio": ("addons",),
}
_INSTANCE_ADDON_ORDER: tuple[ServiceId, ...] = ("aiometadata", "aiostreams")
_INSTANCE_CUSTOM_NAMES: Mapping[ServiceId, str] = {
    "aiometadata": "AIOMetadata",
    "aiostreams": "AIOStreams",
}


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    """A profile with its instance and login bindings looked up for the current tenant."""

    profile: Profile
    instances: Mapping[ServiceId, InstanceBinding]
    logins: Mapping[str, LoginBinding] = field(default_factory=dict)  # platform → binding

    @property
    def account_id(self) -> str:
        return self.profile.account_id or ""


def instance_transport_url(binding: InstanceBinding) -> str:
    """Scrubbed install URL of an instance, expressed purely through identity placeholders."""
    base = f"{{{{{binding.url_env_key}}}}}/stremio/{{{{{binding.uuid_env_key}}}}}"
    if binding.service == "aiostreams":
        return f"{base}/{{{{SECRET_{binding.prefix}_ENCRYPTED_PASSWORD}}}}"
    return base


def instance_url_identities(binding: InstanceBinding) -> frozenset[str]:
    """
    Every normalised form under which this instance's install URL may appear in a document:
    fully placeholdered, with a literal host, and/or with a literal UUID (pre-sanitize documents).
    """
    canonical = instance_transport_url(binding)
    forms = {canonical}
    if binding.url:
        forms.add(canonical.replace(f"{{{{{binding.url_env_key}}}}}", binding.base_url))
    if binding.uuid:
        forms |= {f.replace(f"{{{{{binding.uuid_env_key}}}}}", binding.uuid) for f in list(forms)}
    return frozenset(_url_identity(f) for f in forms)


def _url_identity(url: str) -> str:
    """Normalises a transport URL for matching: strips trailing `/manifest.json` and slashes."""
    return url.strip().removesuffix("/manifest.json").rstrip("/")


def _find_addon(
    addons: Sequence[Mapping[str, Any]], transport_url: str
) -> Mapping[str, Any] | None:
    wanted = _url_identity(transport_url)
    for a in addons:
        if isinstance(a, Mapping) and _url_identity(str(a.get("transportUrl") or "")) == wanted:
            return a
    return None


def _build_addon(
    transport_url: str,
    *,
    custom_name: str | None,
    metadata_extra: Mapping[str, Any],
    enabled: bool,
    existing: Mapping[str, Any] | None,
) -> JsonDict:
    addon: JsonDict = (
        copy.deepcopy(dict(existing))
        if existing
        else {"transportUrl": transport_url, "transportName": ""}
    )
    addon["transportUrl"] = transport_url
    flags = dict(addon.get("flags") or {})
    flags["enabled"] = enabled
    addon["flags"] = flags
    metadata = dict(addon.get("metadata") or {})
    if custom_name:
        metadata["customName"] = custom_name
    for k, v in metadata_extra.items():
        metadata[k] = copy.deepcopy(v)
    if metadata:
        addon["metadata"] = metadata
    return addon


def _build_connection(
    platform: str,
    *,
    account_id: str,
    login: LoginBinding | None,
    spec: NuvioClientSpec | StremioClientSpec,
    existing: Mapping[str, Any] | None,
) -> JsonDict:
    conn: JsonDict = (
        copy.deepcopy(dict(existing))
        if existing
        else {
            "id": stable_connection_id(account_id, platform),
            "platform": platform,
            "connectionType": "native",
            "enabled": True,
            "credentials": {},
            "capabilities": list(_DEFAULT_CAPABILITIES.get(platform, ("addons",))),
        }
    )
    creds = dict(conn.get("credentials") or {})
    if login is not None:
        for fld, placeholder in login.placeholders().items():
            # Only overlay fields the login actually has a value for; keep whatever the account had otherwise
            # (e.g. a Stremio connection that reuses the Nuvio email placeholder).
            if login.credentials.get(fld):
                creds[fld] = placeholder
    if isinstance(spec, NuvioClientSpec):
        if spec.profile_id:
            creds["profileId"] = spec.profile_id
        creds.setdefault("profileIndex", "")
    conn["credentials"] = creds
    conn["enabled"] = True
    return conn


def build_managed_account(
    tenant_name: str,
    resolved: ResolvedProfile,
    existing: Mapping[str, Any] | None,
) -> JsonDict:
    """Generates the account for `resolved` and merges it over `existing` (if any)."""
    profile = resolved.profile
    account_id = profile.account_id or (
        str(existing.get("id")) if existing and existing.get("id") else ""
    )
    account_id = account_id or stable_account_id(tenant_name, profile.name)

    account: JsonDict = (
        copy.deepcopy(dict(existing)) if existing else {"profiles": [], "deletedAddons": {}}
    )
    account["id"] = account_id
    account["name"] = profile.display_name
    if profile.appearance.emoji:
        account["emoji"] = profile.appearance.emoji
    if profile.appearance.accent_color:
        account["accentColor"] = profile.appearance.accent_color

    existing_addons: list[Mapping[str, Any]] = [
        a for a in (account.get("addons") or []) if isinstance(a, Mapping)
    ]
    addons: list[JsonDict] = []
    for svc in _INSTANCE_ADDON_ORDER:
        binding = resolved.instances.get(svc)
        if binding is None:
            continue
        url = instance_transport_url(binding)
        addons.append(
            _build_addon(
                url,
                custom_name=_INSTANCE_CUSTOM_NAMES[svc],
                metadata_extra={},
                enabled=True,
                existing=_find_addon(existing_addons, url),
            )
        )
    for extra in profile.extra_addons:
        addons.append(
            _build_addon(
                extra.transport_url,
                custom_name=extra.custom_name,
                metadata_extra=extra.metadata,
                enabled=extra.enabled,
                existing=_find_addon(existing_addons, extra.transport_url),
            )
        )
    account["addons"] = addons

    existing_conns: list[Mapping[str, Any]] = [
        c for c in (account.get("connections") or []) if isinstance(c, Mapping)
    ]

    def _existing_conn(platform: str) -> Mapping[str, Any] | None:
        return next(
            (c for c in existing_conns if str(c.get("platform") or "").lower() == platform), None
        )

    connections: list[JsonDict] = []
    if profile.nuvio is not None:
        connections.append(
            _build_connection(
                "nuvio",
                account_id=account_id,
                login=resolved.logins.get("nuvio"),
                spec=profile.nuvio,
                existing=_existing_conn("nuvio"),
            )
        )
    if profile.stremio is not None:
        connections.append(
            _build_connection(
                "stremio",
                account_id=account_id,
                login=resolved.logins.get("stremio"),
                spec=profile.stremio,
                existing=_existing_conn("stremio"),
            )
        )
    account["connections"] = connections
    conn_ids = [str(c["id"]) for c in connections]
    if conn_ids:
        primary = str(account.get("primaryConnectionId") or "")
        account["primaryConnectionId"] = primary if primary in conn_ids else conn_ids[0]
    else:
        account.pop("primaryConnectionId", None)
    return account


@dataclass(frozen=True, slots=True)
class ManagedAccountsOutcome:
    state: JsonDict
    managed: tuple[str, ...]  # "<profile> → <account id>"
    unmanaged: tuple[str, ...]  # account names present in state but owned by no profile
    changed: bool


def apply_managed_accounts(
    state: Mapping[str, Any],
    tenant_name: str,
    resolved_profiles: Sequence[ResolvedProfile],
) -> ManagedAccountsOutcome:
    """Regenerates every profile-owned account inside `state.accounts`, preserving unmanaged ones."""
    out: JsonDict = copy.deepcopy(dict(state))
    raw_accounts = out.get("accounts")
    existing: list[JsonDict] = (
        [a for a in raw_accounts if isinstance(a, dict)] if isinstance(raw_accounts, list) else []
    )
    by_id = {str(a.get("id")): a for a in existing if a.get("id")}
    by_name = {str(a.get("name") or "").lower(): a for a in existing}

    new_accounts: list[JsonDict] = []
    claimed: set[int] = set()
    managed: list[str] = []
    for rp in resolved_profiles:
        prof = rp.profile
        match_acc: JsonDict | None = None
        if prof.account_id and prof.account_id in by_id:
            match_acc = by_id[prof.account_id]
        elif not prof.account_id:
            stable = stable_account_id(tenant_name, prof.name)
            match_acc = by_id.get(stable) or by_name.get(prof.display_name.lower())
        built = build_managed_account(tenant_name, rp, match_acc)
        if match_acc is not None:
            claimed.add(id(match_acc))
        new_accounts.append(built)
        managed.append(f"{prof.name} → {built['id']}")

    unmanaged: list[str] = []
    for acc in existing:
        if id(acc) not in claimed:
            new_accounts.append(acc)
            unmanaged.append(str(acc.get("name") or acc.get("id") or "?"))

    out["accounts"] = new_accounts
    return ManagedAccountsOutcome(
        state=out,
        managed=tuple(managed),
        unmanaged=tuple(unmanaged),
        changed=new_accounts != existing,
    )


# ---------------------------------------------------------------------------
# Inverse: derive a profile from an existing account (migration / `sw profile import`)
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:32] or "addon"
    return slug if slug[0].isalpha() else f"a-{slug}"[:32]


def derive_profile_from_account(
    account: Mapping[str, Any],
    *,
    profile_name: str,
    instance_bindings: Mapping[ServiceId, Sequence[InstanceBinding]],
    login_bindings: Mapping[str, Sequence[LoginBinding]],
) -> Profile:
    """
    Builds a `Profile` describing an existing scrubbed AIOManager account: addons whose URL matches a
    registered instance become `instances.*`, connections whose credential placeholders match a
    registered login become `clients.*`, everything else becomes `addons.extra[]`.
    """
    instances: dict[ServiceId, str] = {}
    extras: list[ExtraAddon] = []
    for a in account.get("addons") or []:
        if not isinstance(a, Mapping):
            continue
        t_url = str(a.get("transportUrl") or "")
        matched = False
        for svc, bindings in instance_bindings.items():
            for ib in bindings:
                if _url_identity(t_url) in instance_url_identities(ib):
                    instances[svc] = ib.name
                    matched = True
                    break
            if matched:
                break
        if matched:
            continue
        meta = dict(a.get("metadata") or {})
        custom = meta.pop("customName", None)
        meta.pop("lastUpdated", None)
        flags = a.get("flags") or {}
        extras.append(
            ExtraAddon(
                name=_slug(str(custom or (a.get("manifest") or {}).get("name") or t_url)),
                transport_url=t_url,
                custom_name=str(custom) if custom else None,
                metadata=meta,
                enabled=bool(flags.get("enabled", True)) if isinstance(flags, Mapping) else True,
            )
        )

    nuvio: NuvioClientSpec | None = None
    stremio: StremioClientSpec | None = None
    for c in account.get("connections") or []:
        if not isinstance(c, Mapping):
            continue
        platform = str(c.get("platform") or "").lower()
        raw_creds = c.get("credentials")
        creds: Mapping[str, Any] = raw_creds if isinstance(raw_creds, Mapping) else {}
        login_name = ""
        for lb in login_bindings.get(platform, ()):
            placeholders = lb.placeholders()
            key = "accessToken" if platform == "nuvio" else "authKey"
            if creds.get(key) == placeholders.get(key):
                login_name = lb.name
                break
        if not login_name:
            continue
        if platform == "nuvio":
            pid = str(creds.get("profileId") or "") or None
            nuvio = NuvioClientSpec(login=login_name, profile_id=pid)
        elif platform == "stremio":
            stremio = StremioClientSpec(login=login_name)

    return Profile(
        name=profile_name,
        display_name=str(account.get("name") or profile_name),
        instances={svc: instances.get(svc, "default") for svc in ("aiostreams", "aiometadata")},
        nuvio=nuvio,
        stremio=stremio,
        extra_addons=tuple(extras),
        account_id=str(account.get("id")) if account.get("id") else None,
        appearance=Appearance(
            emoji=str(account["emoji"]) if account.get("emoji") else None,
            accent_color=str(account["accentColor"]) if account.get("accentColor") else None,
        ),
    )
