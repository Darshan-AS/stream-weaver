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
STREAM_BADGE_IMPORT_LIMIT: int = 3
STREAM_BADGE_SETTINGS_FEATURE: str = "stream_badge_settings"
STREAM_WEAVER_ORIGIN_CLIENT_ID: str = "stream-weaver-gitops-sync-client"
MAX_BADGE_URL_LENGTH: int = 2048


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

    return Ok(
        {
            "version": version,
            "platforms": platforms,
            "badgePlacement": placement,
            "showFileSizeBadges": show_file_size,
            "showAddonLogo": show_addon_logo,
            "showInPlayer": show_in_player,
            "playerBadgeSource": player_badge_source,
            "imports": deduped_imports,
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


def _extract_imports_from_rules_str(raw_rules: str | None) -> list[JsonDict]:
    if not raw_rules or not raw_rules.strip():
        return []
    try:
        parsed = json.loads(raw_rules)
    except ValueError:
        return []
    if not isinstance(parsed, Mapping):
        return []

    raw_imports = parsed.get("imports")
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
    elif parsed.get("sourceUrl") and isinstance(parsed.get("filters"), Sequence) and parsed["filters"]:
        extracted.append({"sourceUrl": str(parsed["sourceUrl"]).strip(), "isActive": True})

    if extracted:
        active_idx = next((i for i, x in enumerate(extracted) if bool(x["isActive"])), 0)
        for i, x in enumerate(extracted):
            x["isActive"] = i == active_idx
    return extracted[:STREAM_BADGE_IMPORT_LIMIT]


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
        imports = _extract_imports_from_rules_str(_decode_sync_string(badge_feat, "stream_badge_rules"))
        placement_raw = (_decode_sync_string(badge_feat, "stream_badge_placement") or "BOTTOM").upper()
        placement = placement_raw if placement_raw in VALID_BADGE_PLACEMENTS else "BOTTOM"
        show_size = _decode_sync_bool(badge_feat, "show_file_size_badges")
        show_logo = _decode_sync_bool(badge_feat, "show_addon_logo")
        show_player = _decode_sync_bool(badge_feat, "show_badges_in_player")
        player_src = _decode_sync_string(badge_feat, "player_badge_source") or ""
        decoded_by_plat[plat] = {
            "has_settings": bool(badge_feat),
            "imports": imports,
            "badgePlacement": placement,
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
            "showFileSizeBadges": True,
            "showAddonLogo": True,
            "showInPlayer": False,
            "playerBadgeSource": "",
            "imports": [],
        }

    ref = decoded_by_plat[ref_plat]
    tv_state = decoded_by_plat.get("tv", {})
    ref_imports: list[JsonDict] = ref["imports"]
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
        and decoded_by_plat[plat]["badgePlacement"] == ref_placement
        and decoded_by_plat[plat]["showFileSizeBadges"] == ref_show_size
        and decoded_by_plat[plat]["showInPlayer"] == ref_show_player
        and decoded_by_plat[plat]["playerBadgeSource"] == ref_player_src
    ]

    return {
        "version": version,
        "platforms": synced_platforms,
        "badgePlacement": ref_placement,
        "showFileSizeBadges": ref_show_size,
        "showAddonLogo": bool(tv_state.get("showAddonLogo", True)),
        "showInPlayer": ref_show_player,
        "playerBadgeSource": ref_player_src,
        "imports": copy.deepcopy(ref_imports),
    }
