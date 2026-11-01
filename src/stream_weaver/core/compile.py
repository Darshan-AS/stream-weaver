"""
Profile compilation — ties a resolved profile, the recipe library and the addon index together.

`compile_profile` produces every raw document a profile contributes:
Nuvio collections / badges / meta / settings for its Nuvio profile, and the AIOMetadata catalog
*uses* that `derive_instance_catalogs` later folds into the instance document shared by all
profiles on that instance. Pure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from stream_weaver.core.addon_index import build_addon_resolver
from stream_weaver.core.aiomanager_accounts import ResolvedProfile
from stream_weaver.core.composition import (
    AddonResolver,
    CatalogUse,
    CompiledCollections,
    ExtraCatalog,
    compile_collections,
    compile_doc,
    derive_catalogs,
)
from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.library import Library
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict


@dataclass(frozen=True, slots=True)
class CompiledProfile:
    resolved: ResolvedProfile
    resolver: AddonResolver
    collections: CompiledCollections | None  # None when the profile declares no `collections`
    badges: JsonDict | None
    meta: JsonDict | None
    settings: JsonDict | None

    @property
    def name(self) -> str:
        return self.resolved.profile.name

    @property
    def collections_doc(self) -> list[JsonDict]:
        return list(self.collections.collections) if self.collections else []

    @property
    def catalog_uses(self) -> tuple[CatalogUse, ...]:
        return self.collections.catalog_uses if self.collections else ()

    def account_payload(self) -> JsonDict:
        """Transient per-account keys the AIOManager push fans out to Nuvio (`nuvio*`)."""
        payload: JsonDict = {}
        if self.collections is not None:
            payload["nuvioCollections"] = self.collections_doc
        if self.badges is not None:
            payload["nuvioBadges"] = self.badges
        if self.meta is not None:
            payload["nuvioMeta"] = self.meta
        if self.settings is not None:
            payload["nuvioSettings"] = self.settings
        return payload


def compile_profile(
    resolved: ResolvedProfile,
    library: Library,
    aiomanager_doc: Mapping[str, Any],
) -> Result[CompiledProfile, ConfigValidationError]:
    resolver = build_addon_resolver(resolved, aiomanager_doc)
    profile = resolved.profile
    ctx = f"profile `{profile.name}`"

    compiled_cols: CompiledCollections | None = None
    if profile.collections:
        match compile_collections(profile.collections, library, resolver):
            case Err(err):
                return Err(ConfigValidationError(f"{ctx}: {err.message}"))
            case Ok(cc):
                compiled_cols = cc

    docs: dict[str, JsonDict | None] = {}
    for kind, item in (("badges", profile.badges), ("meta", profile.meta), ("settings", profile.settings)):
        match compile_doc(item, kind, library):
            case Err(err):
                return Err(ConfigValidationError(f"{ctx}: {err.message}"))
            case Ok(doc):
                docs[kind] = doc

    return Ok(
        CompiledProfile(
            resolved=resolved,
            resolver=resolver,
            collections=compiled_cols,
            badges=docs["badges"],
            meta=docs["meta"],
            settings=docs["settings"],
        )
    )


@dataclass(frozen=True, slots=True)
class InstanceCatalogPlan:
    instance_name: str
    profiles: tuple[str, ...]
    catalogs: list[JsonDict] = field(default_factory=list)
    changed: bool = False


def derive_instance_catalogs(
    instance_name: str,
    instance_doc: Mapping[str, Any],
    compiled_profiles: Sequence[CompiledProfile],
) -> Result[InstanceCatalogPlan, ConfigValidationError]:
    """
    Derived `catalogs` for one AIOMetadata instance from every compiled profile bound to it.
    Profiles without any `collections` contribute nothing (the instance keeps its raw list), so an
    un-migrated profile never silently disables catalogs.
    """
    on_instance = [cp for cp in compiled_profiles if cp.resolved.profile.instances.get("aiometadata") == instance_name]
    names = tuple(cp.name for cp in on_instance)
    raw_catalogs = instance_doc.get("catalogs")
    existing: list[Mapping[str, Any]] = [c for c in raw_catalogs if isinstance(c, Mapping)] if isinstance(raw_catalogs, list) else []
    contributing = [cp for cp in on_instance if cp.collections is not None or cp.resolved.profile.extra_catalogs]
    if not contributing:
        return Ok(InstanceCatalogPlan(instance_name=instance_name, profiles=names, catalogs=[dict(c) for c in existing]))

    uses: list[CatalogUse] = []
    extras: list[ExtraCatalog] = []
    for cp in contributing:
        uses.extend(cp.catalog_uses)
        extras.extend(
            ExtraCatalog(catalog_id=e.catalog_id, type=e.type, enabled=e.enabled, show_in_home=e.show_in_home)
            for e in cp.resolved.profile.extra_catalogs
        )
    match derive_catalogs(existing, uses, extras):
        case Err(err):
            return Err(ConfigValidationError(f"aiometadata/{instance_name}: {err.message}"))
        case Ok(derived):
            pass
    return Ok(
        InstanceCatalogPlan(
            instance_name=instance_name,
            profiles=names,
            catalogs=derived,
            changed=derived != [dict(c) for c in existing],
        )
    )
