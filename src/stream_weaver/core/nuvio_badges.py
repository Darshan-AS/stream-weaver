"""Pure functional compiler, normalizer, and decompiler for Nuvio Stream Badge settings."""

import copy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict, is_json_mapping

VALID_NUVIO_BADGE_PLATFORMS: tuple[str, ...] = ("tv", "desktop", "mobile")
VALID_BADGE_PLACEMENTS: tuple[str, ...] = ("BOTTOM", "TOP")
VALID_STREAM_BACKGROUND_MODES: tuple[str, ...] = ("normal", "cinematic")
STREAM_BADGE_IMPORT_LIMIT: int = 3
STREAM_BADGE_SETTINGS_FEATURE: str = "stream_badge_settings"
STREAM_WEAVER_ORIGIN_CLIENT_ID: str = "stream-weaver-gitops-sync-client"
MAX_BADGE_URL_LENGTH: int = 2048
MAX_BADGE_PATTERN_LENGTH: int = 2048
_SW_EXTRA_PREFIX: str = "sw:"
_SW_GROUP_START_MARK: str = "^"
_SW_EXTRA_FILTER_ID_RE = re.compile(r"^sw:(?:(\d+):)?(.+)$")
VALID_EXTRA_FILTER_PLACEMENTS: tuple[str, ...] = ("group-end", "group-start")


def validate_badge_source_url(raw_url: str) -> Result[str, ConfigValidationError]:
    """
    Validates a badge pack or badge image HTTPS URL against strict scheme, length,
    and host checks (failing closed on non-HTTPS or credential-bearing URLs).
    """
    cleaned = raw_url.strip()
    if not cleaned:
        return Err(
            ConfigValidationError(
                message="Badge sourceUrl must not be empty.",
                service="nuvio_badges",
            )
        )
    if len(cleaned) > MAX_BADGE_URL_LENGTH:
        return Err(
            ConfigValidationError(
                message="Badge sourceUrl exceeds maximum allowed length.",
                service="nuvio_badges",
            )
        )
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return Err(
            ConfigValidationError(
                message=f"Invalid badge URL format: {cleaned}",
                service="nuvio_badges",
            )
        )
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return Err(
            ConfigValidationError(
                message=f"Badge URL must use HTTPS with a valid hostname: {cleaned}",
                service="nuvio_badges",
            )
        )
    if parsed.username or parsed.password:
        return Err(
            ConfigValidationError(
                message="Badge URL must not contain embedded credentials.",
                service="nuvio_badges",
            )
        )
    host_lower = parsed.hostname.lower()
    if host_lower in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return Err(
            ConfigValidationError(
                message=f"Badge URL must not target a loopback host: {cleaned}",
                service="nuvio_badges",
            )
        )
    return Ok(cleaned)


def normalize_nuvio_badges_doc(data: Any) -> Result[JsonDict, ConfigValidationError]:
    """
    Validates and normalizes a declarative `configs/nuvio_badges.json` document into
    canonical key order and Nuvio's 3-import / single-active-import invariants.
    """
    if not is_json_mapping(data):
        return Err(
            ConfigValidationError(
                message="Expected a JSON object for Nuvio badges config.",
                service="nuvio_badges",
            )
        )

    version = str(data.get("version") or "1.0.0").strip() or "1.0.0"

    raw_platforms = data.get("platforms")
    if raw_platforms is None:
        platforms = list(VALID_NUVIO_BADGE_PLATFORMS)
    elif isinstance(raw_platforms, Sequence) and not isinstance(raw_platforms, (str, bytes)):
        requested: set[str] = set()
        for item in raw_platforms:
            plat = str(item).strip().lower()
            if plat not in VALID_NUVIO_BADGE_PLATFORMS:
                return Err(
                    ConfigValidationError(
                        message=(
                            f"Unsupported Nuvio platform '{item}'. "
                            f"Expected one of: {', '.join(VALID_NUVIO_BADGE_PLATFORMS)}."
                        ),
                        service="nuvio_badges",
                    )
                )
            requested.add(plat)
        platforms = [p for p in VALID_NUVIO_BADGE_PLATFORMS if p in requested]
    else:
        return Err(
            ConfigValidationError(
                message="'platforms' in nuvio_badges.json must be a list of strings.",
                service="nuvio_badges",
            )
        )

    placement = str(data.get("badgePlacement") or "BOTTOM").strip().upper()
    if placement not in VALID_BADGE_PLACEMENTS:
        return Err(
            ConfigValidationError(
                message=f"Invalid badgePlacement '{placement}'. Expected BOTTOM or TOP.",
                service="nuvio_badges",
            )
        )

    bg_mode = str(data.get("streamBackgroundMode") or "normal").strip().lower()
    if bg_mode not in VALID_STREAM_BACKGROUND_MODES:
        return Err(
            ConfigValidationError(
                message=(
                    f"Invalid streamBackgroundMode '{bg_mode}'. "
                    f"Expected one of: {', '.join(VALID_STREAM_BACKGROUND_MODES)}."
                ),
                service="nuvio_badges",
            )
        )

    show_file_size = bool(data.get("showFileSizeBadges", True))
    show_addon_logo = bool(data.get("showAddonLogo", True))
    show_in_player = bool(data.get("showInPlayer", False))
    player_badge_source = str(data.get("playerBadgeSource") or "").strip()
    if player_badge_source:
        match validate_badge_source_url(player_badge_source):
            case Err() as err:
                return err
            case Ok(valid_src):
                player_badge_source = valid_src

    raw_imports = data.get("imports", [])
    if not isinstance(raw_imports, Sequence) or isinstance(raw_imports, (str, bytes)):
        return Err(
            ConfigValidationError(
                message="'imports' in nuvio_badges.json must be a list.",
                service="nuvio_badges",
            )
        )

    deduped_imports: list[JsonDict] = []
    for raw_imp in raw_imports:
        if isinstance(raw_imp, str):
            raw_url = raw_imp
            is_active = True
        elif isinstance(raw_imp, Mapping):
            raw_url = str(raw_imp.get("sourceUrl") or "")
            is_active = bool(raw_imp.get("isActive", raw_imp.get("active", True)))
        else:
            return Err(
                ConfigValidationError(
                    message="Each entry in 'imports' must be an object with 'sourceUrl' or a URL string.",
                    service="nuvio_badges",
                )
            )

        match validate_badge_source_url(raw_url):
            case Err() as err:
                return err
            case Ok(clean_url):
                pass

        existing_idx = next(
            (
                idx
                for idx, existing in enumerate(deduped_imports)
                if str(existing["sourceUrl"]).lower() == clean_url.lower()
            ),
            -1,
        )
        entry: JsonDict = {"sourceUrl": clean_url, "isActive": is_active}
        if existing_idx >= 0:
            deduped_imports[existing_idx] = entry
        elif len(deduped_imports) < STREAM_BADGE_IMPORT_LIMIT:
            deduped_imports.append(entry)

    if deduped_imports:
        active_idx = next(
            (idx for idx, imp in enumerate(deduped_imports) if bool(imp.get("isActive"))),
            0,
        )
        for idx, imp in enumerate(deduped_imports):
            imp["isActive"] = idx == active_idx

    raw_extra_groups = data.get("extraGroups", [])
    if not isinstance(raw_extra_groups, Sequence) or isinstance(raw_extra_groups, (str, bytes)):
        return Err(
            ConfigValidationError(
                message="'extraGroups' in nuvio_badges.json must be a list.",
                service="nuvio_badges",
            )
        )
    extra_groups: list[JsonDict] = []
    seen_group_ids: set[str] = set()
    for raw_grp in raw_extra_groups:
        if not isinstance(raw_grp, Mapping):
            return Err(
                ConfigValidationError(
                    message="Each entry in 'extraGroups' must be an object.",
                    service="nuvio_badges",
                )
            )
        grp_id = str(raw_grp.get("id") or "").strip().removeprefix(_SW_EXTRA_PREFIX)
        grp_name = str(raw_grp.get("name") or "").strip()
        if not grp_id or not grp_name:
            return Err(
                ConfigValidationError(
                    message="Each entry in 'extraGroups' must have non-empty 'id' and 'name'.",
                    service="nuvio_badges",
                )
            )
        if grp_id in seen_group_ids:
            continue
        seen_group_ids.add(grp_id)
        raw_exp = raw_grp.get("isExpanded")
        extra_groups.append(
            {
                "id": grp_id,
                "name": grp_name,
                "color": str(raw_grp.get("color") or "#00000000"),
                "isExpanded": bool(raw_exp) if isinstance(raw_exp, bool) else True,
            }
        )

    raw_extra_filters = data.get("extraFilters", [])
    if not isinstance(raw_extra_filters, Sequence) or isinstance(raw_extra_filters, (str, bytes)):
        return Err(
            ConfigValidationError(
                message="'extraFilters' in nuvio_badges.json must be a list.",
                service="nuvio_badges",
            )
        )
    extra_filters: list[JsonDict] = []
    seen_filter_ids: set[str] = set()
    for raw_flt in raw_extra_filters:
        if not isinstance(raw_flt, Mapping):
            return Err(
                ConfigValidationError(
                    message="Each entry in 'extraFilters' must be an object.",
                    service="nuvio_badges",
                )
            )
        raw_fid = str(raw_flt.get("id") or "").strip()
        id_match = _SW_EXTRA_FILTER_ID_RE.match(raw_fid)
        flt_id = id_match.group(2) if id_match else raw_fid
        flt_placement = str(raw_flt.get("placement") or "group-end").strip().lower()
        if flt_id.startswith(_SW_GROUP_START_MARK):
            flt_id = flt_id.removeprefix(_SW_GROUP_START_MARK)
            flt_placement = "group-start"
        if flt_placement not in VALID_EXTRA_FILTER_PLACEMENTS:
            return Err(
                ConfigValidationError(
                    message=(
                        f"Invalid placement '{flt_placement}' on extraFilter '{flt_id}'. "
                        f"Expected one of: {', '.join(VALID_EXTRA_FILTER_PLACEMENTS)}."
                    ),
                    service="nuvio_badges",
                )
            )
        flt_name = str(raw_flt.get("name") or "").strip()
        flt_pattern = str(raw_flt.get("pattern") or "").strip()
        if not flt_id or not flt_name or not flt_pattern:
            return Err(
                ConfigValidationError(
                    message="Each entry in 'extraFilters' must have non-empty 'id', 'name', and 'pattern'.",
                    service="nuvio_badges",
                )
            )
        if len(flt_pattern) > MAX_BADGE_PATTERN_LENGTH:
            return Err(
                ConfigValidationError(
                    message=f"Extra filter '{flt_id}' pattern exceeds maximum allowed length.",
                    service="nuvio_badges",
                )
            )
        try:
            re.compile(flt_pattern)
        except re.error as exc:
            return Err(
                ConfigValidationError(
                    message=f"Invalid regex pattern in extraFilter '{flt_id}': {exc}",
                    service="nuvio_badges",
                )
            )
        flt_image_url = str(raw_flt.get("imageURL") or "").strip()
        if flt_image_url:
            match validate_badge_source_url(flt_image_url):
                case Err() as err:
                    return err
                case Ok(valid_img_url):
                    flt_image_url = valid_img_url
        if flt_id in seen_filter_ids:
            continue
        seen_filter_ids.add(flt_id)
        raw_en = raw_flt.get("isEnabled")
        norm_flt: JsonDict = {
            "id": flt_id,
            "groupId": str(raw_flt.get("groupId") or "").strip().removeprefix(_SW_EXTRA_PREFIX),
            "name": flt_name,
            "pattern": flt_pattern,
            "imageURL": flt_image_url,
            "isEnabled": bool(raw_en) if isinstance(raw_en, bool) else True,
            "tagColor": str(raw_flt.get("tagColor") or ""),
            "tagStyle": str(raw_flt.get("tagStyle") or ""),
            "textColor": str(raw_flt.get("textColor") or ""),
            "borderColor": str(raw_flt.get("borderColor") or "#00000000"),
        }
        if flt_placement == "group-start":
            norm_flt["placement"] = "group-start"
        extra_filters.append(norm_flt)

    return Ok(
        {
            "version": version,
            "platforms": platforms,
            "badgePlacement": placement,
            "streamBackgroundMode": bg_mode,
            "showFileSizeBadges": show_file_size,
            "showAddonLogo": show_addon_logo,
            "showInPlayer": show_in_player,
            "playerBadgeSource": player_badge_source,
            "imports": deduped_imports,
            "extraGroups": extra_groups,
            "extraFilters": extra_filters,
        }
    )


def compile_nuvio_badge_import(
    source_url: str,
    raw_payload: Any,
    *,
    is_active: bool = True,
) -> Result[JsonDict, ConfigValidationError]:
    """
    Pure function: compiles a remote Fusion/Nuvio badge JSON payload (`filters` + `groups`)
    into Nuvio's `StreamBadgeImport` structure, matching `StreamBadgeRulesParser.parse`.
    """
    match validate_badge_source_url(source_url):
        case Err() as err:
            return err
        case Ok(clean_url):
            pass

    if not is_json_mapping(raw_payload):
        return Err(
            ConfigValidationError(
                message=f"Badge pack at {clean_url} did not return a JSON object.",
                service="nuvio_badges",
            )
        )

    raw_filters = raw_payload.get("filters", [])
    if not isinstance(raw_filters, Sequence) or isinstance(raw_filters, (str, bytes)):
        return Err(
            ConfigValidationError(
                message=f"Badge pack at {clean_url} has an invalid 'filters' field.",
                service="nuvio_badges",
            )
        )

    filters_out: list[JsonDict] = []
    usable_compiled_count = 0
    for flt in raw_filters:
        if not isinstance(flt, Mapping):
            continue
        name = str(flt.get("name") or "").strip()
        pattern = str(flt.get("pattern") or "").strip()
        if not name or not pattern:
            continue

        image_url = str(flt.get("imageURL") or "").strip()
        if image_url:
            match validate_badge_source_url(image_url):
                case Err() as err:
                    return err
                case Ok(valid_img_url):
                    image_url = valid_img_url

        raw_enabled = flt.get("isEnabled")
        is_enabled = bool(raw_enabled) if isinstance(raw_enabled, bool) else True

        if is_enabled:
            try:
                re.compile(pattern)
                usable_compiled_count += 1
            except re.error:
                pass

        filters_out.append(
            {
                "id": str(flt.get("id") or ""),
                "groupId": str(flt.get("groupId") or ""),
                "name": name,
                "pattern": pattern,
                "imageURL": image_url,
                "isEnabled": is_enabled,
                "tagColor": str(flt.get("tagColor") or ""),
                "tagStyle": str(flt.get("tagStyle") or ""),
                "textColor": str(flt.get("textColor") or ""),
                "borderColor": str(flt.get("borderColor") or ""),
            }
        )

    if not filters_out or usable_compiled_count == 0:
        return Err(
            ConfigValidationError(
                message=f"Badge pack at {clean_url} did not contain any usable regex filters.",
                service="nuvio_badges",
            )
        )

    raw_groups = raw_payload.get("groups", [])
    groups_out: list[JsonDict] = []
    if isinstance(raw_groups, Sequence) and not isinstance(raw_groups, (str, bytes)):
        for grp in raw_groups:
            if not isinstance(grp, Mapping):
                continue
            raw_exp = grp.get("isExpanded")
            is_expanded = bool(raw_exp) if isinstance(raw_exp, bool) else True
            groups_out.append(
                {
                    "id": str(grp.get("id") or ""),
                    "name": str(grp.get("name") or ""),
                    "color": str(grp.get("color") or ""),
                    "isExpanded": is_expanded,
                }
            )

    return Ok(
        {
            "sourceUrl": clean_url,
            "filters": filters_out,
            "groups": groups_out,
            "isActive": is_active,
        }
    )


def compile_nuvio_badge_rules_json(compiled_imports: Sequence[Mapping[str, Any]]) -> str:
    """Serializes compiled `StreamBadgeImport` dicts into Nuvio's compact `StreamBadgeRules` JSON string."""
    if not compiled_imports:
        return ""
    active_idx = next(
        (idx for idx, imp in enumerate(compiled_imports) if bool(imp.get("isActive", True))),
        0,
    )
    normalized_imports: list[JsonDict] = []
    for idx, imp in enumerate(compiled_imports[:STREAM_BADGE_IMPORT_LIMIT]):
        imp_copy = dict(imp)
        imp_copy["isActive"] = idx == active_idx
        normalized_imports.append(imp_copy)
    return json.dumps(
        {"imports": normalized_imports},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _encode_sync_string(value: str) -> JsonDict:
    return {"type": "string", "value": value}


def _encode_sync_bool(value: bool) -> JsonDict:
    return {"type": "boolean", "value": value}


def _decode_sync_string(feature_json: Mapping[str, Any], key: str) -> str | None:
    entry = feature_json.get(key)
    if not isinstance(entry, Mapping) or entry.get("type") != "string":
        return None
    val = entry.get("value")
    return val if isinstance(val, str) else None


def _decode_sync_bool(feature_json: Mapping[str, Any], key: str) -> bool | None:
    entry = feature_json.get(key)
    if not isinstance(entry, Mapping) or entry.get("type") != "boolean":
        return None
    val = entry.get("value")
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        lower = val.strip().lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
    return None


def merge_extra_into_badge_import(
    compiled_import: Mapping[str, Any],
    extra_groups: Sequence[Mapping[str, Any]],
    extra_filters: Sequence[Mapping[str, Any]],
) -> JsonDict:
    """
    Pure function: splices declarative `extraGroups` and `extraFilters` into the active
    `StreamBadgeImport` (since Nuvio's `StreamBadgeRules.normalized()` allows only one active
    import at a time). Extra filters are placed right after their matching `groupId` so badges
    render left-to-right in semantic category order, while `sw:<idx>:<id>` preserves exact
    round-trip order on `decompile_nuvio_badges`.
    """
    imp_copy: JsonDict = copy.deepcopy(dict(compiled_import))
    if not extra_groups and not extra_filters:
        return imp_copy

    base_groups = [
        copy.deepcopy(dict(grp))
        for grp in imp_copy.get("groups", [])
        if isinstance(grp, Mapping) and not str(grp.get("id") or "").startswith(_SW_EXTRA_PREFIX)
    ]
    extra_group_ids: set[str] = set()
    merged_groups: list[JsonDict] = list(base_groups)
    for grp in extra_groups:
        clean_gid = str(grp.get("id") or "").strip().removeprefix(_SW_EXTRA_PREFIX)
        if not clean_gid:
            continue
        extra_group_ids.add(clean_gid)
        merged_groups.append(
            {
                "id": f"{_SW_EXTRA_PREFIX}{clean_gid}",
                "name": str(grp.get("name") or ""),
                "color": str(grp.get("color") or "#00000000"),
                "isExpanded": bool(grp.get("isExpanded", True)),
            }
        )

    base_filters = [
        copy.deepcopy(dict(flt))
        for flt in imp_copy.get("filters", [])
        if isinstance(flt, Mapping) and not str(flt.get("id") or "").startswith(_SW_EXTRA_PREFIX)
    ]
    base_ids: set[str] = {
        str(flt.get("id") or "").strip()
        for flt in base_filters
        if str(flt.get("id") or "").strip()
    }
    override_by_base_id: dict[str, JsonDict] = {}
    wire_extra_by_gid: dict[str, list[JsonDict]] = {}
    wire_start_by_gid: dict[str, list[JsonDict]] = {}
    for idx, flt in enumerate(extra_filters):
        raw_fid = str(flt.get("id") or "").strip()
        id_match = _SW_EXTRA_FILTER_ID_RE.match(raw_fid)
        clean_fid = id_match.group(2) if id_match else raw_fid
        at_start = clean_fid.startswith(_SW_GROUP_START_MARK) or (
            str(flt.get("placement") or "").strip().lower() == "group-start"
        )
        clean_fid = clean_fid.removeprefix(_SW_GROUP_START_MARK)
        clean_gid = str(flt.get("groupId") or "").strip().removeprefix(_SW_EXTRA_PREFIX)
        wire_gid = f"{_SW_EXTRA_PREFIX}{clean_gid}" if clean_gid in extra_group_ids else clean_gid
        wire_fid = f"{_SW_EXTRA_PREFIX}{idx:03d}:{_SW_GROUP_START_MARK if at_start else ''}{clean_fid}"
        wire_flt: JsonDict = {
            "id": wire_fid,
            "groupId": wire_gid,
            "name": str(flt.get("name") or ""),
            "pattern": str(flt.get("pattern") or ""),
            "imageURL": str(flt.get("imageURL") or ""),
            "isEnabled": bool(flt.get("isEnabled", True)),
            "tagColor": str(flt.get("tagColor") or ""),
            "tagStyle": str(flt.get("tagStyle") or ""),
            "textColor": str(flt.get("textColor") or ""),
            "borderColor": str(flt.get("borderColor") or "#00000000"),
        }
        if clean_fid in base_ids and not at_start:
            override_by_base_id[clean_fid] = wire_flt
        elif at_start:
            wire_start_by_gid.setdefault(wire_gid, []).append(wire_flt)
        else:
            wire_extra_by_gid.setdefault(wire_gid, []).append(wire_flt)

    effective_base_filters: list[JsonDict] = []
    for base_flt in base_filters:
        bid = str(base_flt.get("id") or "").strip()
        if bid in override_by_base_id:
            effective_base_filters.append(override_by_base_id[bid])
        else:
            effective_base_filters.append(base_flt)

    first_idx_for_gid: dict[str, int] = {}
    last_idx_for_gid: dict[str, int] = {}
    for i, base_flt in enumerate(effective_base_filters):
        bgid = str(base_flt.get("groupId") or "")
        first_idx_for_gid.setdefault(bgid, i)
        last_idx_for_gid[bgid] = i

    merged_filters: list[JsonDict] = []
    for i, base_flt in enumerate(effective_base_filters):
        bgid = str(base_flt.get("groupId") or "")
        if first_idx_for_gid.get(bgid) == i and bgid in wire_start_by_gid:
            merged_filters.extend(wire_start_by_gid.pop(bgid))
        merged_filters.append(base_flt)
        if last_idx_for_gid.get(bgid) == i and bgid in wire_extra_by_gid:
            merged_filters.extend(wire_extra_by_gid.pop(bgid))

    for grp in merged_groups:
        gid = str(grp.get("id") or "")
        if gid in wire_start_by_gid:
            merged_filters.extend(wire_start_by_gid.pop(gid))
        if gid in wire_extra_by_gid:
            merged_filters.extend(wire_extra_by_gid.pop(gid))

    for remaining in wire_start_by_gid.values():
        merged_filters.extend(remaining)
    for remaining in wire_extra_by_gid.values():
        merged_filters.extend(remaining)

    imp_copy["groups"] = merged_groups
    imp_copy["filters"] = merged_filters
    return imp_copy


def build_platform_stream_badge_settings(
    badge_doc: Mapping[str, Any],
    compiled_rules_json: str,
    *,
    platform: str,
) -> JsonDict:
    """
    Builds the platform-specific `features.stream_badge_settings` object matching
    `NuvioTV` (`StreamBadgeSettingsDataStore`) and `NuvioDesktop`/`NuvioMobile` (`StreamBadgeSettingsStorage`).
    """
    placement = str(badge_doc.get("badgePlacement") or "BOTTOM").strip().upper()
    bg_mode_raw = str(badge_doc.get("streamBackgroundMode") or "normal").strip().lower()
    bg_mode = bg_mode_raw if bg_mode_raw in VALID_STREAM_BACKGROUND_MODES else "normal"
    show_file_size = bool(badge_doc.get("showFileSizeBadges", True))
    show_in_player = bool(badge_doc.get("showInPlayer", False))
    player_badge_source = str(badge_doc.get("playerBadgeSource") or "").strip()

    out: JsonDict = {}
    if compiled_rules_json:
        out["stream_badge_rules"] = _encode_sync_string(compiled_rules_json)
    out["show_file_size_badges"] = _encode_sync_bool(show_file_size)
    out["show_badges_in_player"] = _encode_sync_bool(show_in_player)
    out["player_badge_source"] = _encode_sync_string(player_badge_source)
    out["stream_badge_placement"] = _encode_sync_string(placement)

    if platform == "tv":
        show_addon_logo = bool(badge_doc.get("showAddonLogo", True))
        out["show_addon_logo"] = _encode_sync_bool(show_addon_logo)
    elif platform in ("desktop", "mobile"):
        out["stream_background_mode"] = _encode_sync_string(bg_mode)

    return out


def merge_profile_settings_blob_badges(
    remote_settings_json: Mapping[str, Any] | None,
    platform_badge_settings: JsonDict,
    *,
    platform: str,
) -> JsonDict | None:
    """
    Pure read-modify-write merge of `features.stream_badge_settings` into a Nuvio
    profile settings blob, preserving `version` and all unrelated feature settings.
    Returns `None` for uninitialized `desktop`/`mobile` blobs to avoid overwriting
    local first-run theme/player settings with empty defaults.
    """
    if remote_settings_json is None:
        if platform == "tv":
            return {
                "version": 1,
                "features": {
                    STREAM_BADGE_SETTINGS_FEATURE: copy.deepcopy(platform_badge_settings),
                },
            }
        return None

    merged: JsonDict = copy.deepcopy(dict(remote_settings_json))
    raw_features = merged.get("features")
    features: JsonDict = copy.deepcopy(dict(raw_features)) if isinstance(raw_features, Mapping) else {}
    existing_badge_feat = features.get(STREAM_BADGE_SETTINGS_FEATURE)
    new_badge_feat: JsonDict = copy.deepcopy(platform_badge_settings)
    if (
        platform in ("desktop", "mobile")
        and isinstance(existing_badge_feat, Mapping)
        and "stream_background_mode" in existing_badge_feat
        and "stream_background_mode" not in new_badge_feat
    ):
        new_badge_feat["stream_background_mode"] = copy.deepcopy(
            existing_badge_feat["stream_background_mode"]
        )
    features[STREAM_BADGE_SETTINGS_FEATURE] = new_badge_feat
    merged["features"] = features
    merged.setdefault("version", 1 if platform == "tv" else 4)
    return merged


def _extract_rules_components(
    raw_rules: str | None,
) -> tuple[list[JsonDict], list[JsonDict], list[JsonDict]]:
    if not raw_rules or not raw_rules.strip():
        return [], [], []
    try:
        parsed = json.loads(raw_rules)
    except ValueError:
        return [], [], []
    if not isinstance(parsed, Mapping):
        return [], [], []

    raw_imports = parsed.get("imports")
    import_mappings: list[Mapping[str, Any]] = []
    extracted: list[JsonDict] = []
    if isinstance(raw_imports, Sequence) and not isinstance(raw_imports, (str, bytes)):
        for imp in raw_imports:
            if not isinstance(imp, Mapping):
                continue
            url = str(imp.get("sourceUrl") or "").strip()
            filters = imp.get("filters")
            if not url or not isinstance(filters, Sequence) or not filters:
                continue
            is_active = bool(imp.get("isActive", imp.get("active", True)))
            extracted.append({"sourceUrl": url, "isActive": is_active})
            import_mappings.append(imp)
    elif parsed.get("sourceUrl") and isinstance(parsed.get("filters"), Sequence) and parsed["filters"]:
        extracted.append({"sourceUrl": str(parsed["sourceUrl"]).strip(), "isActive": True})
        import_mappings.append(parsed)

    if not extracted:
        return [], [], []

    active_idx = next((i for i, x in enumerate(extracted) if bool(x["isActive"])), 0)
    for i, x in enumerate(extracted):
        x["isActive"] = i == active_idx

    active_imp = import_mappings[active_idx] if active_idx < len(import_mappings) else {}
    extra_groups: list[JsonDict] = []
    raw_groups = active_imp.get("groups", [])
    if isinstance(raw_groups, Sequence) and not isinstance(raw_groups, (str, bytes)):
        for grp in raw_groups:
            if not isinstance(grp, Mapping):
                continue
            gid = str(grp.get("id") or "").strip()
            if not gid.startswith(_SW_EXTRA_PREFIX):
                continue
            raw_exp = grp.get("isExpanded")
            extra_groups.append(
                {
                    "id": gid.removeprefix(_SW_EXTRA_PREFIX),
                    "name": str(grp.get("name") or ""),
                    "color": str(grp.get("color") or "#00000000"),
                    "isExpanded": bool(raw_exp) if isinstance(raw_exp, bool) else True,
                }
            )

    indexed_extra_filters: list[tuple[int, int, JsonDict]] = []
    raw_filters = active_imp.get("filters", [])
    if isinstance(raw_filters, Sequence) and not isinstance(raw_filters, (str, bytes)):
        for encounter_idx, flt in enumerate(raw_filters):
            if not isinstance(flt, Mapping):
                continue
            fid = str(flt.get("id") or "").strip()
            m = _SW_EXTRA_FILTER_ID_RE.match(fid)
            if not m:
                continue
            sort_idx = int(m.group(1)) if m.group(1) is not None else 100_000 + encounter_idx
            clean_fid = m.group(2)
            at_start = clean_fid.startswith(_SW_GROUP_START_MARK)
            clean_fid = clean_fid.removeprefix(_SW_GROUP_START_MARK)
            raw_en = flt.get("isEnabled")
            decoded_flt: JsonDict = {
                "id": clean_fid,
                "groupId": str(flt.get("groupId") or "").strip().removeprefix(_SW_EXTRA_PREFIX),
                "name": str(flt.get("name") or ""),
                "pattern": str(flt.get("pattern") or ""),
                "imageURL": str(flt.get("imageURL") or ""),
                "isEnabled": bool(raw_en) if isinstance(raw_en, bool) else True,
                "tagColor": str(flt.get("tagColor") or ""),
                "tagStyle": str(flt.get("tagStyle") or ""),
                "textColor": str(flt.get("textColor") or ""),
                "borderColor": str(flt.get("borderColor") or "#00000000"),
            }
            if at_start:
                decoded_flt["placement"] = "group-start"
            indexed_extra_filters.append((sort_idx, encounter_idx, decoded_flt))
    indexed_extra_filters.sort(key=lambda item: (item[0], item[1]))
    extra_filters = [item[2] for item in indexed_extra_filters]

    return extracted[:STREAM_BADGE_IMPORT_LIMIT], extra_groups, extra_filters


def decompile_nuvio_badges(
    platform_blobs: Mapping[str, Mapping[str, Any] | None],
    *,
    version: str = "1.0.0",
) -> JsonDict:
    """
    Pure function: inspects `sync_pull_profile_settings_blob` payloads across
    `("tv", "desktop", "mobile")` and reconstructs the declarative `configs/nuvio_badges.json`
    representation, listing only platforms whose active badge settings are in sync.
    """
    decoded_by_plat: dict[str, dict[str, Any]] = {}
    for plat in VALID_NUVIO_BADGE_PLATFORMS:
        blob = platform_blobs.get(plat)
        if not isinstance(blob, Mapping):
            continue
        feats = blob.get("features")
        if not isinstance(feats, Mapping):
            continue
        badge_feat = feats.get(STREAM_BADGE_SETTINGS_FEATURE)
        if not isinstance(badge_feat, Mapping):
            continue
        imports, extra_groups, extra_filters = _extract_rules_components(
            _decode_sync_string(badge_feat, "stream_badge_rules")
        )
        placement_raw = (_decode_sync_string(badge_feat, "stream_badge_placement") or "BOTTOM").upper()
        placement = placement_raw if placement_raw in VALID_BADGE_PLACEMENTS else "BOTTOM"
        bg_raw = (_decode_sync_string(badge_feat, "stream_background_mode") or "normal").strip().lower()
        bg_mode = bg_raw if bg_raw in VALID_STREAM_BACKGROUND_MODES else "normal"
        show_size = _decode_sync_bool(badge_feat, "show_file_size_badges")
        show_logo = _decode_sync_bool(badge_feat, "show_addon_logo")
        show_player = _decode_sync_bool(badge_feat, "show_badges_in_player")
        player_src = _decode_sync_string(badge_feat, "player_badge_source") or ""
        decoded_by_plat[plat] = {
            "has_settings": bool(badge_feat),
            "imports": imports,
            "extraGroups": extra_groups,
            "extraFilters": extra_filters,
            "badgePlacement": placement,
            "streamBackgroundMode": bg_mode,
            "showFileSizeBadges": True if show_size is None else show_size,
            "showAddonLogo": True if show_logo is None else show_logo,
            "showInPlayer": False if show_player is None else show_player,
            "playerBadgeSource": player_src,
        }

    ref_plat = next(
        (p for p in VALID_NUVIO_BADGE_PLATFORMS if decoded_by_plat.get(p, {}).get("imports")),
        next(
            (p for p in VALID_NUVIO_BADGE_PLATFORMS if decoded_by_plat.get(p, {}).get("has_settings")),
            None,
        ),
    )

    if ref_plat is None:
        return {
            "version": version,
            "platforms": [],
            "badgePlacement": "BOTTOM",
            "streamBackgroundMode": "normal",
            "showFileSizeBadges": True,
            "showAddonLogo": True,
            "showInPlayer": False,
            "playerBadgeSource": "",
            "imports": [],
            "extraGroups": [],
            "extraFilters": [],
        }

    ref = decoded_by_plat[ref_plat]
    tv_state = decoded_by_plat.get("tv", {})
    ref_bg_mode = next(
        (
            str(decoded_by_plat[p]["streamBackgroundMode"])
            for p in ("mobile", "desktop")
            if p in decoded_by_plat and decoded_by_plat[p]["has_settings"]
        ),
        "normal",
    )
    ref_imports: list[JsonDict] = ref["imports"]
    ref_extra_groups: list[JsonDict] = ref["extraGroups"]
    ref_extra_filters: list[JsonDict] = ref["extraFilters"]
    ref_placement: str = ref["badgePlacement"]
    ref_show_size: bool = ref["showFileSizeBadges"]
    ref_show_player: bool = ref["showInPlayer"]
    ref_player_src: str = ref["playerBadgeSource"]

    synced_platforms = [
        plat
        for plat in VALID_NUVIO_BADGE_PLATFORMS
        if plat in decoded_by_plat
        and decoded_by_plat[plat]["has_settings"]
        and decoded_by_plat[plat]["imports"] == ref_imports
        and decoded_by_plat[plat]["extraGroups"] == ref_extra_groups
        and decoded_by_plat[plat]["extraFilters"] == ref_extra_filters
        and decoded_by_plat[plat]["badgePlacement"] == ref_placement
        and decoded_by_plat[plat]["showFileSizeBadges"] == ref_show_size
        and decoded_by_plat[plat]["showInPlayer"] == ref_show_player
        and decoded_by_plat[plat]["playerBadgeSource"] == ref_player_src
        and (
            plat == "tv"
            or decoded_by_plat[plat]["streamBackgroundMode"] == ref_bg_mode
        )
    ]

    return {
        "version": version,
        "platforms": synced_platforms,
        "badgePlacement": ref_placement,
        "streamBackgroundMode": ref_bg_mode,
        "showFileSizeBadges": ref_show_size,
        "showAddonLogo": bool(tv_state.get("showAddonLogo", True)),
        "showInPlayer": ref_show_player,
        "playerBadgeSource": ref_player_src,
        "imports": copy.deepcopy(ref_imports),
        "extraGroups": copy.deepcopy(ref_extra_groups),
        "extraFilters": copy.deepcopy(ref_extra_filters),
    }
