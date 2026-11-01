"""
Addon index — builds the `AddonResolver` for a profile from the tenant's scrubbed `aiomanager.json`.

Symbolic addon → manifest id + cached catalog list:

* `aiometadata` → constant manifest id `aio-metadata`; catalogs from the cached manifest if present.
* `aiostreams`  → `<host>.<uuid[:12]>` computed from the instance binding (works before the first
  sync); catalogs + preset ids from the cached manifest once the instance has synced.
* extras        → cached manifest matched by transport URL; without a cache the extra cannot be
  used by sources that need `match` (the compiler reports this precisely).

Pure: the caller passes the already-loaded document.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.aiomanager_accounts import ResolvedProfile, instance_url_identities
from stream_weaver.core.composition import (
    AIOMETADATA_MANIFEST_ID,
    AddonInfo,
    AddonResolver,
    aiostreams_manifest_id,
)

_PRESET_PREFIX_RE = re.compile(r"^([a-z0-9]{3})[0-9a-f]{4}\.")


def _url_identity(url: str) -> str:
    return url.strip().removesuffix("/manifest.json").rstrip("/")


def _manifests_by_url(aiomanager_doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """transport-url identity → fullest cached manifest (savedAddons first, then account addons)."""
    out: dict[str, Mapping[str, Any]] = {}
    addons_section = aiomanager_doc.get("addons")
    saved = addons_section.get("savedAddons") if isinstance(addons_section, Mapping) else None
    for sa in saved or []:
        if isinstance(sa, Mapping) and isinstance(sa.get("manifest"), Mapping) and sa.get("installUrl"):
            out.setdefault(_url_identity(str(sa["installUrl"])), sa["manifest"])
    for acc in aiomanager_doc.get("accounts") or []:
        if not isinstance(acc, Mapping):
            continue
        for a in acc.get("addons") or []:
            if not isinstance(a, Mapping) or not a.get("transportUrl"):
                continue
            mf = a.get("manifest")
            if not isinstance(mf, Mapping):
                continue
            key = _url_identity(str(a["transportUrl"]))
            existing = out.get(key)
            # prefer a manifest that actually lists catalogs over a deduped stub
            if existing is None or (not existing.get("catalogs") and mf.get("catalogs")):
                out[key] = mf
    return out


def _lookup(manifests: Mapping[str, Mapping[str, Any]], identities: frozenset[str] | set[str]) -> Mapping[str, Any] | None:
    for ident in identities:
        if ident in manifests:
            return manifests[ident]
    return None


def build_addon_resolver(resolved: ResolvedProfile, aiomanager_doc: Mapping[str, Any]) -> AddonResolver:
    manifests = _manifests_by_url(aiomanager_doc)
    addons: dict[str, AddonInfo] = {}

    meta_binding = resolved.instances.get("aiometadata")
    meta_mf = _lookup(manifests, instance_url_identities(meta_binding)) if meta_binding else None
    addons["aiometadata"] = AddonInfo(
        name="aiometadata",
        addon_id=str(meta_mf.get("id")) if meta_mf and meta_mf.get("id") else AIOMETADATA_MANIFEST_ID,
        catalogs=tuple(c for c in (meta_mf or {}).get("catalogs", []) if isinstance(c, Mapping)),
    )

    streams_binding = resolved.instances.get("aiostreams")
    if streams_binding is not None:
        streams_mf = _lookup(manifests, instance_url_identities(streams_binding))
        catalogs = tuple(c for c in (streams_mf or {}).get("catalogs", []) if isinstance(c, Mapping))
        presets = frozenset(
            m.group(1) for c in catalogs if (m := _PRESET_PREFIX_RE.match(str(c.get("id") or "")))
        )
        addons["aiostreams"] = AddonInfo(
            name="aiostreams",
            addon_id=str(streams_mf.get("id"))
            if streams_mf and streams_mf.get("id")
            else aiostreams_manifest_id(streams_binding.base_url, streams_binding.uuid),
            catalogs=catalogs,
            presets=presets,
        )

    for extra in resolved.profile.extra_addons:
        mf = manifests.get(_url_identity(extra.transport_url))
        addons[extra.name] = AddonInfo(
            name=extra.name,
            addon_id=str(mf.get("id")) if mf and mf.get("id") else "",
            catalogs=tuple(c for c in (mf or {}).get("catalogs", []) if isinstance(c, Mapping)),
        )
    return AddonResolver(addons=addons)
