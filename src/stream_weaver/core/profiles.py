"""
Profiles — one person's identity binding inside a tenant (`profiles/<name>/profile.json`).

Schema v1 has two halves in one document, on purpose:

* **identity** — which named instances it uses, which client logins it signs in with (and which
  Nuvio profile), extra addons beyond the instances, appearance;
* **taste** — `collections` (ref-or-inline, see `core.composition`), whole-document refs for
  `badges` / `meta` / `settings`, and `aiometadata.extraCatalogs` for catalogs that should stay
  enabled on the instance although no collection references them (e.g. a Stremio board).

Everything here is pure.
"""

from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.tenant import TenantPaths
from stream_weaver.core.types import JsonDict, ServiceId, is_json_list, is_json_mapping

PROFILE_FILE = "profile.json"
PROFILES_DIR = "profiles"
PROFILE_SCHEMA_VERSION = 1
PROFILE_SCHEMA_URL = "https://stream-weaver.dev/schemas/profile.v1.json"

NUVIO_PLATFORMS: tuple[str, ...] = ("tv", "desktop", "mobile")
PROFILE_INSTANCE_SERVICES: tuple[ServiceId, ...] = ("aiostreams", "aiometadata")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://stream-weaver.dev/ids")  # stable ids


@dataclass(frozen=True, slots=True)
class NuvioClientSpec:
    login: str
    profile_id: str | None = None
    profile_name: str | None = None  # resolved against the login's profile list at push time
    platforms: tuple[str, ...] = NUVIO_PLATFORMS


@dataclass(frozen=True, slots=True)
class StremioClientSpec:
    login: str


@dataclass(frozen=True, slots=True)
class ExtraAddon:
    """An addon installed for this profile beyond its service instances (e.g. BingeCat, Cinemeta)."""

    name: str
    transport_url: str
    custom_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)  # extra AIOManager `metadata` keys
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Appearance:
    emoji: str | None = None
    accent_color: str | None = None


@dataclass(frozen=True, slots=True)
class ExtraCatalogSpec:
    """A catalog kept enabled on the profile's AIOMetadata instance without any collection using it."""

    catalog_id: str
    type: str
    enabled: bool = True
    show_in_home: bool = False


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    display_name: str
    instances: Mapping[ServiceId, str]
    nuvio: NuvioClientSpec | None = None
    stremio: StremioClientSpec | None = None
    extra_addons: tuple[ExtraAddon, ...] = ()
    account_id: str | None = None
    appearance: Appearance = field(default_factory=Appearance)
    # taste
    collections: tuple[Any, ...] = ()  # ref-or-inline items (validated structurally by the compiler)
    badges: Any = None  # {"ref": id} | inline document | None
    meta: Any = None
    settings: Any = None
    extra_catalogs: tuple[ExtraCatalogSpec, ...] = ()
    version: int = PROFILE_SCHEMA_VERSION

    @property
    def has_taste(self) -> bool:
        return bool(self.collections or self.badges or self.meta or self.settings or self.extra_catalogs)

    @property
    def platforms(self) -> tuple[str, ...]:
        return self.nuvio.platforms if self.nuvio else ()


def profile_dir(paths: TenantPaths, name: str) -> Path:
    return paths.root / PROFILES_DIR / name


def profile_file(paths: TenantPaths, name: str) -> Path:
    return profile_dir(paths, name) / PROFILE_FILE


def stable_account_id(tenant_name: str, profile_name: str) -> str:
    """Deterministic AIOManager account id for a profile that has no explicit `accountId`."""
    return str(uuid.uuid5(_UUID_NAMESPACE, f"{tenant_name}/account/{profile_name}"))


def stable_connection_id(account_id: str, platform: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, f"{account_id}/connection/{platform}"))


def _err(msg: str) -> Err[ConfigValidationError]:
    return Err(ConfigValidationError(f"profile.json: {msg}"))


def parse_profile(doc: Mapping[str, Any]) -> Result[Profile, ConfigValidationError]:
    if doc.get("version") != PROFILE_SCHEMA_VERSION:
        return _err(
            f"unsupported version {doc.get('version')!r} (expected {PROFILE_SCHEMA_VERSION})"
        )
    name = doc.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return _err(f"`name` must match {_NAME_RE.pattern}, got {name!r}")
    display_name = doc.get("displayName") or name
    if not isinstance(display_name, str) or not display_name.strip():
        return _err("`displayName` must be a non-empty string")

    raw_instances = doc.get("instances", {})
    if not is_json_mapping(raw_instances):
        return _err("`instances` must be an object")
    instances: dict[ServiceId, str] = {}
    for svc in PROFILE_INSTANCE_SERVICES:
        val = raw_instances.get(svc, "default")
        if not isinstance(val, str) or not _NAME_RE.match(val):
            return _err(f"`instances.{svc}` must be an instance name")
        instances[svc] = val
    unknown_svcs = set(raw_instances) - set(PROFILE_INSTANCE_SERVICES)
    if unknown_svcs:
        return _err(f"`instances` has unsupported services: {sorted(unknown_svcs)}")

    raw_clients = doc.get("clients", {})
    if not is_json_mapping(raw_clients):
        return _err("`clients` must be an object")
    nuvio: NuvioClientSpec | None = None
    stremio: StremioClientSpec | None = None
    for platform, spec in raw_clients.items():
        if not is_json_mapping(spec):
            return _err(f"`clients.{platform}` must be an object")
        login = spec.get("login")
        if not isinstance(login, str) or not _NAME_RE.match(login):
            return _err(f"`clients.{platform}.login` must be a login name")
        if platform == "nuvio":
            prof = spec.get("profile", {})
            if not is_json_mapping(prof):
                return _err("`clients.nuvio.profile` must be an object")
            pid = prof.get("id")
            match_obj = prof.get("match", {})
            pname = match_obj.get("name") if is_json_mapping(match_obj) else None
            if pid is not None and not isinstance(pid, str):
                return _err("`clients.nuvio.profile.id` must be a string")
            if pname is not None and not isinstance(pname, str):
                return _err("`clients.nuvio.profile.match.name` must be a string")
            platforms = spec.get("platforms", list(NUVIO_PLATFORMS))
            if (
                not is_json_list(platforms)
                or not platforms
                or any(p not in NUVIO_PLATFORMS for p in platforms)
            ):
                return _err(
                    f"`clients.nuvio.platforms` must be a non-empty subset of {list(NUVIO_PLATFORMS)}"
                )
            nuvio = NuvioClientSpec(
                login=login, profile_id=pid, profile_name=pname, platforms=tuple(platforms)
            )
        elif platform == "stremio":
            stremio = StremioClientSpec(login=login)
        else:
            return _err(f"unsupported client platform `{platform}`")

    raw_addons = doc.get("addons", {})
    if not is_json_mapping(raw_addons):
        return _err("`addons` must be an object")
    extras: list[ExtraAddon] = []
    for item in raw_addons.get("extra", []) or []:
        if not is_json_mapping(item):
            return _err("`addons.extra[]` entries must be objects")
        a_name = item.get("name")
        t_url = item.get("transportUrl")
        if not isinstance(a_name, str) or not _NAME_RE.match(a_name):
            return _err(f"`addons.extra[].name` must match {_NAME_RE.pattern}, got {a_name!r}")
        if not isinstance(t_url, str) or not t_url.startswith(("http://", "https://", "{{")):
            return _err(f"`addons.extra[{a_name}].transportUrl` must be a URL")
        meta = item.get("metadata", {})
        if not is_json_mapping(meta):
            return _err(f"`addons.extra[{a_name}].metadata` must be an object")
        extras.append(
            ExtraAddon(
                name=a_name,
                transport_url=t_url,
                custom_name=item.get("customName")
                if isinstance(item.get("customName"), str)
                else None,
                metadata=dict(meta),
                enabled=bool(item.get("enabled", True)),
            )
        )
    if len({a.name for a in extras}) != len(extras):
        return _err("`addons.extra[].name` values must be unique")

    account_id = doc.get("accountId")
    if account_id is not None and (not isinstance(account_id, str) or not account_id.strip()):
        return _err("`accountId` must be a non-empty string when present")

    raw_cols = doc.get("collections", [])
    if not is_json_list(raw_cols):
        return _err("`collections` must be a list of `{ref}` items or inline collections")
    for i, item in enumerate(raw_cols):
        if not is_json_mapping(item):
            return _err(f"`collections[{i}]` must be an object")
        if "ref" in item and not isinstance(item["ref"], str):
            return _err(f"`collections[{i}].ref` must be a string")

    def _doc_ref(key: str) -> Result[Any, ConfigValidationError]:
        val = doc.get(key)
        if val is None:
            return Ok(None)
        if not is_json_mapping(val):
            return _err(f"`{key}` must be a `{{ref}}` or an inline document")
        if "ref" in val and (not isinstance(val["ref"], str) or set(val) - {"ref", "with"}):
            return _err(f"`{key}` ref must be `{{\"ref\": id}}` (optionally with `with`)")
        return Ok(dict(val))

    taste_docs: dict[str, Any] = {}
    for key in ("badges", "meta", "settings"):
        match _doc_ref(key):
            case Err() as err:
                return err
            case Ok(val):
                taste_docs[key] = val

    raw_meta_cfg = doc.get("aiometadata", {})
    if not is_json_mapping(raw_meta_cfg):
        return _err("`aiometadata` must be an object")
    extra_catalogs: list[ExtraCatalogSpec] = []
    for i, item in enumerate(raw_meta_cfg.get("extraCatalogs", []) or []):
        if not is_json_mapping(item) or not isinstance(item.get("id"), str) or not isinstance(item.get("type"), str):
            return _err(f"`aiometadata.extraCatalogs[{i}]` needs string `id` and `type`")
        extra_catalogs.append(
            ExtraCatalogSpec(
                catalog_id=item["id"],
                type=item["type"],
                enabled=bool(item.get("enabled", True)),
                show_in_home=bool(item.get("showInHome", False)),
            )
        )

    raw_app = doc.get("appearance", {})
    if not is_json_mapping(raw_app):
        return _err("`appearance` must be an object")
    appearance = Appearance(
        emoji=raw_app.get("emoji") if isinstance(raw_app.get("emoji"), str) else None,
        accent_color=raw_app.get("accentColor")
        if isinstance(raw_app.get("accentColor"), str)
        else None,
    )

    return Ok(
        Profile(
            name=name,
            display_name=display_name.strip(),
            instances=instances,
            nuvio=nuvio,
            stremio=stremio,
            extra_addons=tuple(extras),
            account_id=account_id,
            appearance=appearance,
            collections=tuple(copy.deepcopy(list(raw_cols))),
            badges=taste_docs["badges"],
            meta=taste_docs["meta"],
            settings=taste_docs["settings"],
            extra_catalogs=tuple(extra_catalogs),
        )
    )


def profile_to_doc(profile: Profile) -> JsonDict:
    """Canonical JSON form of a `Profile` (inverse of `parse_profile`)."""
    clients: JsonDict = {}
    if profile.nuvio is not None:
        prof: JsonDict = {}
        if profile.nuvio.profile_id:
            prof["id"] = profile.nuvio.profile_id
        if profile.nuvio.profile_name:
            prof["match"] = {"name": profile.nuvio.profile_name}
        clients["nuvio"] = {
            "login": profile.nuvio.login,
            "profile": prof,
            "platforms": list(profile.nuvio.platforms),
        }
    if profile.stremio is not None:
        clients["stremio"] = {"login": profile.stremio.login}
    extras: list[JsonDict] = []
    for a in profile.extra_addons:
        item: JsonDict = {"name": a.name, "transportUrl": a.transport_url}
        if a.custom_name:
            item["customName"] = a.custom_name
        if a.metadata:
            item["metadata"] = dict(a.metadata)
        if not a.enabled:
            item["enabled"] = False
        extras.append(item)
    doc: JsonDict = {
        "$schema": PROFILE_SCHEMA_URL,
        "version": PROFILE_SCHEMA_VERSION,
        "name": profile.name,
        "displayName": profile.display_name,
        "instances": dict(profile.instances),
        "clients": clients,
        "addons": {"extra": extras},
    }
    if profile.account_id:
        doc["accountId"] = profile.account_id
    if profile.collections:
        doc["collections"] = copy.deepcopy(list(profile.collections))
    for key, val in (("badges", profile.badges), ("meta", profile.meta), ("settings", profile.settings)):
        if val is not None:
            doc[key] = copy.deepcopy(val)
    if profile.extra_catalogs:
        doc["aiometadata"] = {
            "extraCatalogs": [
                {"id": e.catalog_id, "type": e.type, "enabled": e.enabled, "showInHome": e.show_in_home}
                for e in profile.extra_catalogs
            ]
        }
    appearance: JsonDict = {}
    if profile.appearance.emoji:
        appearance["emoji"] = profile.appearance.emoji
    if profile.appearance.accent_color:
        appearance["accentColor"] = profile.appearance.accent_color
    if appearance:
        doc["appearance"] = appearance
    return doc
