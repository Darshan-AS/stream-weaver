"""Pure normalization, dehydration, and rehydration functions for AIO service configs."""

import copy
import difflib
import json
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import (
    ConfigDiffOutcome,
    JsonDict,
    ServiceId,
    is_json_dict,
    is_json_list,
    is_json_mapping,
)

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
    if not is_json_mapping(data):
        return Err(
            ConfigValidationError(
                message="Expected a JSON object for AIOStreams config.",
                service="aiostreams",
            )
        )

    inner_data = data.get("data")
    if is_json_mapping(inner_data) and is_json_mapping(inner_data.get("userData")):
        cfg = dict(inner_data["userData"])
    elif is_json_mapping(data.get("userData")):
        cfg = dict(data["userData"])
    elif is_json_mapping(data.get("config")):
        cfg = dict(data["config"])
    else:
        cfg = dict(data)

    for key in AIOSTREAMS_VOLATILE_KEYS:
        cfg.pop(key, None)
    return Ok(cfg)


def normalize_aiometadata(data: Any) -> Result[JsonDict, ConfigValidationError]:
    """Unwraps API/UI envelopes for AIOMetadata and strips volatile version/timestamp fields."""
    if not is_json_mapping(data):
        return Err(
            ConfigValidationError(
                message="Expected a JSON object for AIOMetadata config.",
                service="aiometadata",
            )
        )

    cfg = dict(data["config"]) if is_json_mapping(data.get("config")) else dict(data)

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
    if is_json_list(catalogs):
        for cat in catalogs:
            if not is_json_dict(cat):
                continue
            extras = cat.get("extra")
            if is_json_list(extras):
                for extra in extras:
                    if (
                        is_json_dict(extra)
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
    if not is_json_mapping(data):
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


def rehydrate_aiomanager(
    data: Mapping[str, Any],
    *,
    remote_telemetry: Mapping[str, Any] | None = None,
) -> JsonDict:
    """
    Purely reconstructs the full AIOManager state object (rehydrating deduplicated manifests,
    the top-level `manifests` lookup map, and preserving remote runtime telemetry when provided
    or falling back to default empty telemetry collections) prior to push.
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
    state.setdefault("notes", [])

    list_telemetry_keys: tuple[str, ...] = (
        "changelog",
        "vaultTombstones",
        "notesTrash",
        "watchEvents",
    )
    dict_telemetry_keys: tuple[str, ...] = (
        "watchSnapshot",
        "watchEventRollups",
        "deletedWatchEvents",
    )

    for l_key in list_telemetry_keys:
        if l_key not in state:
            rem_list = remote_telemetry.get(l_key) if remote_telemetry is not None else None
            state[l_key] = copy.deepcopy(rem_list) if isinstance(rem_list, list) else []

    for d_key in dict_telemetry_keys:
        if d_key not in state:
            rem_dict = remote_telemetry.get(d_key) if remote_telemetry is not None else None
            state[d_key] = copy.deepcopy(dict(rem_dict)) if isinstance(rem_dict, Mapping) else {}

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


def compute_config_diff(
    local_cfg: Mapping[str, Any],
    remote_cfg: Mapping[str, Any],
    *,
    target: str,
    context_lines: int = 3,
) -> ConfigDiffOutcome:
    """
    Pure function: compares local and remote scrubbed JSON objects, returning
    changed top-level keys, added/removed line counts, and a unified diff.
    """
    local_dict = dict(local_cfg)
    remote_dict = dict(remote_cfg)
    if local_dict == remote_dict:
        return ConfigDiffOutcome(target=target, in_sync=True)

    all_keys = list(dict.fromkeys([*local_dict.keys(), *remote_dict.keys()]))
    changed_keys = tuple(k for k in all_keys if local_dict.get(k) != remote_dict.get(k))

    local_lines = (json.dumps(local_dict, indent=2, ensure_ascii=False) + "\n").splitlines(
        keepends=True
    )
    remote_lines = (json.dumps(remote_dict, indent=2, ensure_ascii=False) + "\n").splitlines(
        keepends=True
    )

    diff_lines = list(
        difflib.unified_diff(
            remote_lines,
            local_lines,
            fromfile=f"remote/{target}.json",
            tofile=f"configs/{target}.json",
            n=context_lines,
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    return ConfigDiffOutcome(
        target=target,
        in_sync=False,
        changed_keys=changed_keys,
        added_lines=added,
        removed_lines=removed,
        unified_diff="".join(diff_lines),
    )
