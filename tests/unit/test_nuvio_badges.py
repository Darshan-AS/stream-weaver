"""Unit tests for Nuvio Stream Badge compilation, safe profile blob merging, and round-trip sync."""

import base64
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.coercions import (
    compute_config_diff,
    dehydrate_aiomanager,
    rehydrate_aiomanager,
)
from stream_weaver.core.crypto import encrypt_field
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.nuvio_badges import (
    build_platform_stream_badge_settings,
    compile_nuvio_badge_import,
    compile_nuvio_badge_rules_json,
    decompile_nuvio_badges,
    merge_profile_settings_blob_badges,
    normalize_nuvio_badges_doc,
    validate_badge_source_url,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.effects.http import HttpResponse
from stream_weaver.providers.services.clients import NuvioClientSync

ELITE_SAMPLE_URL = "https://raw.githubusercontent.com/leonevz/Elite-Badges/main/badges.json"
ELITE_SAMPLE_PACK: dict[str, Any] = {
    "filters": [
        {
            "id": "flt-4k",
            "groupId": "grp-res",
            "name": "4K UHD",
            "pattern": "(?i)\\b(2160p|4k|uhd)\\b",
            "imageURL": "https://raw.githubusercontent.com/leonevz/Elite-Badges/main/badges/resolution/4k.png",
            "isEnabled": True,
        },
        {
            "id": "flt-atmos",
            "groupId": "grp-audio",
            "name": "Dolby Atmos",
            "pattern": "(?i)\\batmos\\b",
            "imageURL": "https://raw.githubusercontent.com/leonevz/Elite-Badges/main/badges/audio/atmos.png",
            "isEnabled": True,
        },
    ],
    "groups": [
        {"id": "grp-res", "name": "Resolution", "color": "#FFFFFF", "isExpanded": True},
        {"id": "grp-audio", "name": "Audio", "color": "#FFFFFF", "isExpanded": True},
    ],
}


def test_validate_badge_source_url_enforces_https_and_rejects_unsafe_urls() -> None:
    assert isinstance(validate_badge_source_url(ELITE_SAMPLE_URL), Ok)
    assert isinstance(validate_badge_source_url("http://example.com/badges.json"), Err)
    assert isinstance(validate_badge_source_url("javascript:alert(1)"), Err)
    assert isinstance(validate_badge_source_url("https://localhost/badges.json"), Err)
    assert isinstance(validate_badge_source_url("https://user:pass@example.com/badges.json"), Err)


def test_normalize_nuvio_badges_doc_enforces_invariants() -> None:
    raw = {
        "platforms": ["mobile", "tv"],
        "badgePlacement": "bottom",
        "showFileSizeBadges": False,
        "showAddonLogo": True,
        "showInPlayer": True,
        "imports": [
            "https://example.com/pack1.json",
            {"sourceUrl": "https://example.com/pack2.json", "isActive": True},
            {"sourceUrl": "https://example.com/pack3.json", "isActive": False},
            {"sourceUrl": "https://example.com/pack4.json", "isActive": False},
        ],
    }
    res = normalize_nuvio_badges_doc(raw)
    assert isinstance(res, Ok)
    doc = res.value
    # Canonical platform order (`tv`, `desktop`, `mobile`) and 3-import limit
    assert doc["platforms"] == ["tv", "mobile"]
    assert doc["badgePlacement"] == "BOTTOM"
    assert len(doc["imports"]) == 3
    # Exactly one active import (first active wins)
    assert [imp["isActive"] for imp in doc["imports"]] == [True, False, False]

    # Reject unknown platform or placement
    assert isinstance(normalize_nuvio_badges_doc({"platforms": ["xbox"]}), Err)
    assert isinstance(normalize_nuvio_badges_doc({"badgePlacement": "SIDE"}), Err)


def test_compile_merge_and_decompile_roundtrip_preserves_existing_features() -> None:
    local_res = normalize_nuvio_badges_doc(
        {
            "version": "1.0.0",
            "platforms": ["tv", "desktop", "mobile"],
            "badgePlacement": "BOTTOM",
            "showFileSizeBadges": False,
            "showAddonLogo": True,
            "showInPlayer": True,
            "playerBadgeSource": "",
            "imports": [{"sourceUrl": ELITE_SAMPLE_URL, "isActive": True}],
        }
    )
    assert isinstance(local_res, Ok)
    local_doc = local_res.value

    imp_res = compile_nuvio_badge_import(ELITE_SAMPLE_URL, ELITE_SAMPLE_PACK, is_active=True)
    assert isinstance(imp_res, Ok)
    rules_json = compile_nuvio_badge_rules_json([imp_res.value])

    existing_desktop_blob: dict[str, Any] = {
        "version": 4,
        "features": {
            "theme_settings": {"selected_theme": {"type": "string", "value": "OCEAN"}},
            "player_settings": {"hw_decode": {"type": "boolean", "value": True}},
            "stream_badge_settings": {
                "stream_background_mode": {"type": "string", "value": "cinematic"},
            },
        },
    }
    existing_tv_blob: dict[str, Any] = {
        "version": 1,
        "features": {
            "tmdb_settings": {"lang": {"type": "string", "value": "en"}},
            "stream_badge_settings": {},
        },
    }

    tv_merged = merge_profile_settings_blob_badges(
        existing_tv_blob,
        build_platform_stream_badge_settings(local_doc, rules_json, platform="tv"),
        platform="tv",
    )
    desktop_merged = merge_profile_settings_blob_badges(
        existing_desktop_blob,
        build_platform_stream_badge_settings(local_doc, rules_json, platform="desktop"),
        platform="desktop",
    )
    mobile_merged = merge_profile_settings_blob_badges(
        existing_desktop_blob,
        build_platform_stream_badge_settings(local_doc, rules_json, platform="mobile"),
        platform="mobile",
    )

    assert tv_merged is not None
    assert desktop_merged is not None
    assert mobile_merged is not None
    # Unrelated features, stream_background_mode & blob versions remain completely intact
    assert desktop_merged["version"] == 4
    assert desktop_merged["features"]["theme_settings"] == {
        "selected_theme": {"type": "string", "value": "OCEAN"}
    }
    assert desktop_merged["features"]["stream_badge_settings"]["stream_background_mode"] == {
        "type": "string",
        "value": "cinematic",
    }
    assert tv_merged["features"]["tmdb_settings"] == {"lang": {"type": "string", "value": "en"}}
    # Player badge sync keys are emitted across all 3 platforms; show_addon_logo is TV-only
    assert desktop_merged["features"]["stream_badge_settings"]["show_badges_in_player"] == {
        "type": "boolean",
        "value": True,
    }
    assert mobile_merged["features"]["stream_badge_settings"]["show_badges_in_player"] == {
        "type": "boolean",
        "value": True,
    }
    assert "show_addon_logo" in tv_merged["features"]["stream_badge_settings"]
    assert "show_addon_logo" not in desktop_merged["features"]["stream_badge_settings"]

    # Uninitialized desktop blob returns None to protect first-run local settings
    assert merge_profile_settings_blob_badges(None, {}, platform="desktop") is None

    # Decompiling across all 3 platforms matches local_doc with zero diff
    decompiled = decompile_nuvio_badges(
        {"tv": tv_merged, "desktop": desktop_merged, "mobile": mobile_merged}
    )
    diff = compute_config_diff(local_doc, decompiled, target="nuvio_badges")
    assert diff.in_sync is True

    # Simulate missing show_badges_in_player on desktop (exact regression scenario)
    desktop_missing_player = {
        "version": 4,
        "features": {
            "stream_badge_settings": {
                k: v
                for k, v in desktop_merged["features"]["stream_badge_settings"].items()
                if k != "show_badges_in_player"
            }
        },
    }
    decompiled_player_drift = decompile_nuvio_badges(
        {"tv": tv_merged, "desktop": desktop_missing_player, "mobile": mobile_merged}
    )
    assert decompiled_player_drift["platforms"] == ["tv", "mobile"]
    player_drift_diff = compute_config_diff(local_doc, decompiled_player_drift, target="nuvio_badges")
    assert player_drift_diff.in_sync is False

    # Simulate drift on mobile (e.g., badges cleared on mobile)
    drifted_mobile: dict[str, Any] = {"version": 4, "features": {"stream_badge_settings": {}}}
    decompiled_drift = decompile_nuvio_badges(
        {"tv": tv_merged, "desktop": desktop_merged, "mobile": drifted_mobile}
    )
    assert decompiled_drift["platforms"] == ["tv", "desktop"]
    drift_diff = compute_config_diff(local_doc, decompiled_drift, target="nuvio_badges")
    assert drift_diff.in_sync is False
    assert "platforms" in drift_diff.changed_keys


def test_aiomanager_coercions_propagate_and_strip_nuvio_badges() -> None:
    raw_mgr: dict[str, Any] = {
        "accounts": [{"id": "acc-1", "addons": []}],
        "nuvioBadges": {"badgePlacement": "BOTTOM", "imports": []},
    }
    rehydrated = rehydrate_aiomanager(raw_mgr)
    assert "nuvioBadges" not in rehydrated
    assert rehydrated["accounts"][0]["nuvioBadges"] == {"badgePlacement": "BOTTOM", "imports": []}

    dehydrated_res = dehydrate_aiomanager(rehydrated)
    assert isinstance(dehydrated_res, Ok)
    assert "nuvioBadges" not in dehydrated_res.value
    assert "nuvioBadges" not in dehydrated_res.value["accounts"][0]


def test_nuvio_client_sync_push_and_pull_badges_e2e() -> None:
    password = "master-sync-pass"
    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    enc_access = encrypt_field("nuvio-jwt-tok", password, salt_b64)
    conn = {
        "platform": "nuvio",
        "enabled": True,
        "credentials": {"accessToken": enc_access, "profileId": "p-uuid-1"},
    }
    env = {"NUVIO_SUPABASE_ANON_KEY": "anon-key-123"}

    remote_blobs: dict[str, dict[str, Any]] = {
        "tv": {"version": 1, "features": {"theme_settings": {"dark": {"type": "boolean", "value": True}}}},
        "desktop": {"version": 4, "features": {"theme_settings": {"amoled": {"type": "boolean", "value": False}}}},
        "mobile": {"version": 4, "features": {"theme_settings": {"amoled": {"type": "boolean", "value": True}}}},
    }

    class FakeNuvioHttp:
        def request_json(
            self,
            method: str,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
            json_body: Any | None = None,
            raw_body: bytes | None = None,
            timeout: float = 30.0,
        ) -> Result[HttpResponse, NetworkError]:
            _ = headers, raw_body, timeout
            if method == "GET" and url == ELITE_SAMPLE_URL:
                return Ok(HttpResponse(status_code=200, data=ELITE_SAMPLE_PACK, text="{}"))
            if url.endswith("/sync_pull_profiles"):
                return Ok(
                    HttpResponse(
                        status_code=200,
                        data=[{"id": "p-uuid-1", "profile_index": 1}],
                        text="[]",
                    )
                )
            if url.endswith("/sync_pull_profile_settings_blob"):
                assert isinstance(json_body, Mapping)
                plat = str(json_body["p_platform"])
                return Ok(
                    HttpResponse(
                        status_code=200,
                        data=[{"profile_id": 1, "settings_json": remote_blobs[plat]}],
                        text="[]",
                    )
                )
            if url.endswith("/sync_push_profile_settings_blob"):
                assert isinstance(json_body, Mapping)
                plat = str(json_body["p_platform"])
                remote_blobs[plat] = dict(json_body["p_settings_json"])
                return Ok(HttpResponse(status_code=204, data={}, text=""))
            return Err(NetworkError(message=f"Unexpected URL {url}", status_code=404, url=url))

    syncer = NuvioClientSync(FakeNuvioHttp())
    badge_doc = {
        "version": "1.0.0",
        "platforms": ["tv", "desktop", "mobile"],
        "badgePlacement": "BOTTOM",
        "showFileSizeBadges": False,
        "showAddonLogo": True,
        "showInPlayer": True,
        "playerBadgeSource": "",
        "imports": [{"sourceUrl": ELITE_SAMPLE_URL, "isActive": True}],
    }

    push_res = syncer.push_badges(badge_doc, conn, password, salt_b64, env)
    assert isinstance(push_res, Ok)
    msg, _ = push_res.value
    assert "2 filters across tv, desktop, mobile" in msg
    # Existing theme_settings on all 3 platforms remain untouched
    assert remote_blobs["desktop"]["features"]["theme_settings"] == {
        "amoled": {"type": "boolean", "value": False}
    }

    pull_res = syncer.pull_badges(conn, password, salt_b64, env)
    assert isinstance(pull_res, Ok)
    pulled_doc, _ = pull_res.value
    assert pulled_doc == badge_doc
