"""Pure normalization, dehydration, and rehydration functions for AIO service configs."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict, ServiceId

AIOSTREAMS_VOLATILE_KEYS: tuple[str, ...] = ("uuid", "encryptedPassword", "accessKey", "ip")
AIOMETADATA_VOLATILE_KEYS: tuple[str, ...] = ("lastModified", "configVersion")
AIOMANAGER_VOLATILE_KEYS: tuple[str, ...] = (
    "syncedAt",
    "contentHint",
    "force",
    "watchEvents",
    "watchEventRollups",
    "watchSnapshot",
    "deletedWatchEvents",
    "changelog",
    "vaultTombstones",
    "notesTrash",
    "nuvioCollections",
)


def normalize_aiostreams(data: Any) -> Result[JsonDict, ConfigValidationError]:
    """Unwraps API/UI envelopes for AIOStreams and strips volatile server fields."""
    if not isinstance(data, Mapping):
        return Err(
            ConfigValidationError(
                message="Expected a JSON object for AIOStreams config.",
                service="aiostreams",
            )
        )

    inner_data = data.get("data")
    if isinstance(inner_data, Mapping) and isinstance(inner_data.get("userData"), Mapping):
        cfg = dict(inner_data["userData"])
    elif isinstance(data.get("userData"), Mapping):
        cfg = dict(data["userData"])
    elif isinstance(data.get("config"), Mapping):
        cfg = dict(data["config"])
    else:
        cfg = dict(data)

    for key in AIOSTREAMS_VOLATILE_KEYS:
        cfg.pop(key, None)
    return Ok(cfg)


def normalize_aiometadata(data: Any) -> Result[JsonDict, ConfigValidationError]:
    """Unwraps API/UI envelopes for AIOMetadata and strips volatile version/timestamp fields."""
    if not isinstance(data, Mapping):
        return Err(
            ConfigValidationError(
                message="Expected a JSON object for AIOMetadata config.",
                service="aiometadata",
            )
        )

    cfg = dict(data["config"]) if isinstance(data.get("config"), Mapping) else dict(data)

    for key in AIOMETADATA_VOLATILE_KEYS:
        cfg.pop(key, None)
    return Ok(cfg)


def sanitize_manifest_home_catalogs(manifest: JsonDict) -> JsonDict:
    """
    Strips forced `"default"` values from optional (`isRequired: false`) `"genre"` extras
    in addon catalog definitions so Nuvio/Stremio request the fast unfiltered root Home
    catalog instead of an accidental default genre filter (e.g. `"Action"`).
    """
    catalogs = manifest.get("catalogs")
    if isinstance(catalogs, list):
        for cat in catalogs:
            if not isinstance(cat, dict):
                continue
            extras = cat.get("extra")
            if isinstance(extras, list):
                for extra in extras:
                    if (
                        isinstance(extra, dict)
                        and extra.get("name") == "genre"
                        and not extra.get("isRequired", False)
                    ):
                        extra.pop("default", None)
    return manifest


def dehydrate_aiomanager(data: Any) -> Result[JsonDict, ConfigValidationError]:
    """
    Purely strips volatile runtime telemetry (`watchEvents`, `changelog`, etc.) and
    deduplicates cached addon manifests so `configs/aiomanager.json` stays compact and diff-friendly.
    """
    if not isinstance(data, Mapping):
        return Err(
            ConfigValidationError(
                message="Expected a JSON object for AIOManager config.",
                service="aiomanager",
            )
        )

    cfg: JsonDict = copy.deepcopy(dict(data))
    for key in AIOMANAGER_VOLATILE_KEYS:
        cfg.pop(key, None)

    # Build lookup of canonical manifests from `addons.savedAddons` (or existing `manifests`)
    existing_manifests = cfg.pop("manifests", None)
    manifest_by_id: dict[str, JsonDict] = {}
    if isinstance(existing_manifests, Mapping):
        for k, v in existing_manifests.items():
            if isinstance(v, Mapping):
                manifest_by_id[str(k)] = sanitize_manifest_home_catalogs(dict(v))

    addons_section = cfg.get("addons")
    saved_by_id: dict[str, JsonDict] = {}
    if isinstance(addons_section, dict):
        saved_addons = addons_section.get("savedAddons")
        if isinstance(saved_addons, list):
            for sa in saved_addons:
                if not isinstance(sa, dict):
                    continue
                mf = sa.get("manifest")
                if isinstance(mf, dict) and mf.get("id"):
                    sanitize_manifest_home_catalogs(mf)
                    mid = f"{mf['id']}:{mf.get('version', '1.0.0')}"
                    saved_by_id[mid] = mf
                elif isinstance(mf, dict):
                    # Backfill from top-level manifests if needed
                    pass

    # In `accounts[].addons`, if `manifest` matches `savedAddons`, strip duplicate `catalogs` list
    accounts = cfg.get("accounts")
    if isinstance(accounts, list):
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            acc.pop("nuvioCollections", None)
            acc_addons = acc.get("addons")
            if not isinstance(acc_addons, list):
                continue
            for addon_entry in acc_addons:
                if not isinstance(addon_entry, dict):
                    continue
                mid = str(addon_entry.get("manifestId") or "")
                mf = addon_entry.get("manifest")
                if isinstance(mf, dict):
                    sanitize_manifest_home_catalogs(mf)
                canonical_mf = saved_by_id.get(mid) or manifest_by_id.get(mid)
                if isinstance(mf, dict) and isinstance(canonical_mf, dict) and mf == canonical_mf:
                    addon_entry["manifest"] = {
                        "id": mf.get("id"),
                        "version": mf.get("version"),
                        "name": mf.get("name"),
                        "_deduped": True,
                    }

    return Ok(cfg)


def rehydrate_aiomanager(data: Mapping[str, Any]) -> JsonDict:
    """
    Purely reconstructs the full AIOManager state object (rehydrating deduplicated manifests,
    the top-level `manifests` lookup map, and default empty telemetry collections) prior to push.
    """
    state: JsonDict = copy.deepcopy(dict(data))

    manifests_map: dict[str, JsonDict] = {}
    existing_manifests = state.get("manifests")
    if isinstance(existing_manifests, dict):
        for k, v in existing_manifests.items():
            if isinstance(v, dict):
                manifests_map[str(k)] = sanitize_manifest_home_catalogs(v)

    addons_section = state.get("addons")
    if isinstance(addons_section, dict):
        saved_addons = addons_section.get("savedAddons")
        if isinstance(saved_addons, list):
            for sa in saved_addons:
                if isinstance(sa, dict):
                    mf = sa.get("manifest")
                    if isinstance(mf, dict) and mf.get("id") and not mf.get("_deduped"):
                        sanitize_manifest_home_catalogs(mf)
                        mid = f"{mf['id']}:{mf.get('version', '1.0.0')}"
                        manifests_map[mid] = copy.deepcopy(mf)

    top_collections = state.pop("nuvioCollections", None)
    accounts = state.get("accounts")
    if isinstance(accounts, list):
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            if isinstance(top_collections, list) and top_collections:
                acc.setdefault("nuvioCollections", copy.deepcopy(top_collections))
            acc_addons = acc.get("addons")
            if not isinstance(acc_addons, list):
                continue
            for addon_entry in acc_addons:
                if not isinstance(addon_entry, dict):
                    continue
                mid = str(addon_entry.get("manifestId") or "")
                mf = addon_entry.get("manifest")
                if isinstance(mf, dict) and mf.get("_deduped") and mid in manifests_map:
                    addon_entry["manifest"] = copy.deepcopy(manifests_map[mid])
                elif isinstance(mf, dict) and mf.get("id") and mid not in manifests_map:
                    sanitize_manifest_home_catalogs(mf)
                    manifests_map[mid] = copy.deepcopy(mf)

    state["manifests"] = manifests_map
    state.setdefault("changelog", [])
    state.setdefault("vaultTombstones", [])
    state.setdefault("notes", [])
    state.setdefault("notesTrash", [])
    state.setdefault("watchEvents", [])
    state.setdefault("watchSnapshot", {})
    state.setdefault("watchEventRollups", {})
    state.setdefault("deletedWatchEvents", {})
    return state


def normalize_service_config(
    service: ServiceId,
    data: Any,
) -> Result[JsonDict, ConfigValidationError]:
    """Dispatches normalization/dehydration for any supported service."""
    match service:
        case "aiostreams":
            return normalize_aiostreams(data)
        case "aiometadata":
            return normalize_aiometadata(data)
        case "aiomanager":
            return dehydrate_aiomanager(data)
