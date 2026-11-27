"""Unit tests for AIOStreams, AIOMetadata, and AIOManager power configuration modules."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from stream_weaver.core.aiomanager_power import (
    apply_aiomanager_power,
    inspect_aiomanager_power,
    inspect_extra_addons,
    normalize_extra_addons,
)
from stream_weaver.core.aiometadata_power import (
    apply_aiometadata_power,
    inspect_aiometadata_power,
    required_aiometadata_power_secrets,
)
from stream_weaver.core.aiostreams_power import (
    MANAGED_CUSTOM_PRESET_IDS,
    STREAM_FORMATTER_PRESETS,
    apply_advanced_stream_filters,
    apply_custom_stream_addons,
    apply_debrid_services,
    apply_stream_formatter,
    inspect_advanced_stream_filters,
    inspect_custom_stream_addons,
    inspect_debrid_services,
    inspect_stream_formatter,
    required_multi_debrid_secrets,
)
from stream_weaver.core.profiles import parse_profile, profile_to_doc
from stream_weaver.core.result import Err, Ok
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"


def _load_example(filename: str) -> dict[str, Any]:
    return json.loads((EXAMPLE / filename).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# 1. AIOStreams Power — Multi-Debrid & Usenet Stacking
# ---------------------------------------------------------------------------


def test_multi_debrid_stacking_priority_and_secret_requirements() -> None:
    raw = _load_example("aiostreams.json")
    inspected = inspect_debrid_services(raw, {"SECRET_TORBOX_API_KEY": "tb-key"})
    assert [s["id"] for s in inspected["active"]] == ["torbox"]
    assert inspected["active"][0]["configured"] is True
    assert inspected["active"][0]["priority"] == 1
    assert len(inspected["available"]) == 5
    assert required_multi_debrid_secrets(raw) == ("SECRET_TORBOX_API_KEY",)

    # Round-trip identity on starter config
    rt = apply_debrid_services(raw, inspected["active"])
    assert isinstance(rt, Ok)
    assert rt.value == raw

    # Stack Real-Debrid (priority 1), TorBox (priority 2), and Easynews Usenet (priority 3)
    stacked_res = apply_debrid_services(
        raw,
        [
            {"id": "realdebrid", "enabled": True},
            {"id": "torbox", "enabled": True},
            {"id": "easynews", "enabled": True},
            {"id": "alldebrid", "enabled": False},
        ],
    )
    assert isinstance(stacked_res, Ok)
    stacked = stacked_res.value
    assert [s["id"] for s in stacked["services"]] == ["realdebrid", "torbox", "easynews"]
    assert stacked["services"][2]["credentials"] == {
        "username": "{{SECRET_EASYNEWS_USERNAME}}",
        "password": "{{SECRET_EASYNEWS_PASSWORD}}",
    }
    # Cloud library catalog modification rewired to highest-priority debrid (Real-Debrid)
    lib_mod = stacked["catalogModifications"][0]
    assert "aiostreams::library.realdebrid" in lib_mod["id"]
    assert lib_mod["name"] == "Real-Debrid Cloud Library"

    assert required_multi_debrid_secrets(stacked) == (
        "SECRET_EASYNEWS_PASSWORD",
        "SECRET_EASYNEWS_USERNAME",
        "SECRET_RD_API_KEY",
        "SECRET_TORBOX_API_KEY",
    )

    partial_env = {
        "SECRET_RD_API_KEY": "rd-tok-123456",
        "SECRET_TORBOX_API_KEY": "tb-tok-123456",
        "SECRET_EASYNEWS_USERNAME": "en-user",
    }
    st2 = inspect_debrid_services(stacked, partial_env)
    by_id = {x["id"]: x for x in st2["active"]}
    assert by_id["realdebrid"]["configured"] is True
    assert by_id["realdebrid"]["priority"] == 1
    assert by_id["easynews"]["configured"] is False  # password still unset
    assert by_id["easynews"]["priority"] == 3

    # Scrub & inject round-trip with Easynews + Multi-Debrid
    full_env = {
        **partial_env,
        "SECRET_EASYNEWS_PASSWORD": "en-pass-secret-9999",
        "SECRET_TMDB_API_KEY": "tmdb-key-1234",
        "SECRET_TMDB_ACCESS_TOKEN": "tmdb-jwt-1234",
        "SECRET_TVDB_API_KEY": "tvdb-key-1234",
        "SECRET_RPDB_API_KEY": "t0-free-rpdb",
        "SECRET_ITSOUT_TOKEN": "itsout-token-1234",
        "SECRET_SUBSOURCE_API_KEY": "subsource-secret-9999",
    }
    inj = inject_secrets(stacked, full_env)
    assert isinstance(inj, Ok)
    scrubbed, _ = harvest_and_scrub("aiostreams", inj.value, dict(full_env))
    assert scrubbed == stacked

    assert isinstance(apply_debrid_services(raw, []), Err)
    assert isinstance(apply_debrid_services(raw, [{"id": "torbox", "enabled": False}]), Err)
    assert isinstance(apply_debrid_services(raw, ["unknown_debrid"]), Err)


# ---------------------------------------------------------------------------
# 2. AIOStreams Power — Custom Addon URL Installer
# ---------------------------------------------------------------------------


def test_custom_stream_addons_preserves_managed_presets_and_wires_groups() -> None:
    raw = _load_example("aiostreams.json")
    # Inject a `swc` contraband preset to verify `swc` is also protected
    with_swc = copy.deepcopy(raw)
    presets_list = list(with_swc["presets"])
    presets_list.append(
        {
            "type": "custom",
            "instanceId": "swc",
            "enabled": True,
            "options": {
                "name": "Contraband",
                "manifestUrl": "https://raw.githubusercontent.com/org/repo/main/manifest.json",
                "timeout": 5000,
                "resources": ["stream", "catalog"],
            },
        }
    )
    with_swc["presets"] = presets_list

    assert inspect_custom_stream_addons(with_swc) == []
    rt = apply_custom_stream_addons(with_swc, [])
    assert isinstance(rt, Ok)
    assert rt.value == with_swc

    installed_res = apply_custom_stream_addons(
        with_swc,
        [
            {
                "name": "StremThru Store",
                "manifestUrl": "stremio://stremthru.example.com/manifest.json",
                "enabled": True,
                "group": "primary",
                "resources": ["stream"],
                "timeout": 8000,
            },
            {
                "instanceId": "httpfb1",
                "name": "WebStreamr",
                "manifestUrl": "https://webstreamr.example.com",
                "enabled": False,
                "group": "fallback",
                "resources": ["stream"],
                "timeout": 15000,
            },
            {
                "instanceId": "kitsu_sub",
                "name": "Anime Subtitles",
                "manifestUrl": "https://animesubs.example.com/manifest.json",
                "enabled": True,
                "group": "subtitles",
                "resources": ["subtitles"],
                "timeout": 9000,
            },
        ],
    )
    assert isinstance(installed_res, Ok), installed_res
    doc2 = installed_res.value

    # All managed custom presets (`ssp`, `ito`, `bso`, `plv`, `etv`, `eth`, `swc`, `sl1..sl3`) survive intact
    present_ids = {p["instanceId"] for p in doc2["presets"]}
    assert MANAGED_CUSTOM_PRESET_IDS.issubset(present_ids)
    assert {"sl1", "sl2", "sl3"}.issubset(present_ids)

    inspected = inspect_custom_stream_addons(doc2)
    assert len(inspected) == 3
    primary_id = inspected[0]["instanceId"]
    assert inspected[0]["manifestUrl"] == "https://stremthru.example.com/manifest.json"
    assert inspected[0]["group"] == "primary"
    assert inspected[1]["instanceId"] == "httpfb1"
    assert inspected[1]["manifestUrl"] == "https://webstreamr.example.com/manifest.json"
    assert inspected[1]["enabled"] is False
    assert inspected[1]["group"] == "fallback"
    assert inspected[2]["instanceId"] == "kitsu_sub"
    assert inspected[2]["group"] == "subtitles"

    g1 = doc2["groups"]["groupings"][0]["addons"]
    g2 = doc2["groups"]["groupings"][1]["addons"]
    assert primary_id in g1
    assert primary_id not in g2
    assert "httpfb1" in g2
    assert "httpfb1" not in g1
    assert "kitsu_sub" not in g1
    assert "kitsu_sub" not in g2

    # Idempotent round-trip with custom addons (including disabled fallback addon)
    rt2 = apply_custom_stream_addons(doc2, inspected)
    assert isinstance(rt2, Ok)
    assert rt2.value == doc2

    # Move `httpfb1` to primary and remove the other two -> original groups restored for removed items
    moved_res = apply_custom_stream_addons(
        doc2,
        [
            {
                **inspected[1],
                "enabled": True,
                "group": "primary",
            }
        ],
    )
    assert isinstance(moved_res, Ok)
    doc3 = moved_res.value
    assert [c["instanceId"] for c in inspect_custom_stream_addons(doc3)] == ["httpfb1"]
    assert "httpfb1" in doc3["groups"]["groupings"][0]["addons"]
    assert "httpfb1" not in doc3["groups"]["groupings"][1]["addons"]
    assert primary_id not in doc3["groups"]["groupings"][0]["addons"]

    # Clearing all user custom addons restores `with_swc` byte-for-byte
    cleared = apply_custom_stream_addons(doc3, [])
    assert isinstance(cleared, Ok)
    assert cleared.value == with_swc

    # Rejects collisions with managed/built-in preset IDs and invalid inputs
    for reserved_id in ("ssp", "sl1", "swc", "tio"):
        assert isinstance(
            apply_custom_stream_addons(
                raw,
                [{"instanceId": reserved_id, "name": "Bad", "manifestUrl": "https://ok.example/manifest.json"}],
            ),
            Err,
        )
    assert isinstance(
        apply_custom_stream_addons(raw, [{"name": "Bad", "manifestUrl": "ftp://bad.example"}]),
        Err,
    )
    assert isinstance(
        apply_custom_stream_addons(
            raw, [{"name": "Bad", "manifestUrl": "https://ok.example/manifest.json", "group": "tier9"}]
        ),
        Err,
    )


# ---------------------------------------------------------------------------
# 3. AIOStreams Power — Formatter Presets & Sample Simulator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset_id", ["prism", "compact_tv", "minimal"])
def test_stream_formatter_presets_and_custom_detection(preset_id: str) -> None:
    raw = _load_example("aiostreams.json")
    initial = inspect_stream_formatter(raw)
    assert initial["active"] == "custom"
    assert initial["sampleName"]
    assert initial["sampleDescription"]
    assert len(initial["presets"]) == 3

    # `custom` preserves user's existing custom formatter byte-for-byte
    kept = apply_stream_formatter(raw, "custom")
    assert isinstance(kept, Ok)
    assert kept.value == raw

    preset_spec = STREAM_FORMATTER_PRESETS[preset_id]
    assert preset_spec["sampleName"]
    assert preset_spec["sampleDescription"]

    applied = apply_stream_formatter(raw, preset_id)
    assert isinstance(applied, Ok)
    after = inspect_stream_formatter(applied.value)
    assert after["active"] == preset_id
    assert after["sampleName"] == preset_spec["sampleName"]
    assert after["sampleDescription"] == preset_spec["sampleDescription"]

    assert isinstance(apply_stream_formatter(raw, "nonexistent_formatter"), Err)


# ---------------------------------------------------------------------------
# 4. AIOStreams Power — Advanced Stream Filter Rules
# ---------------------------------------------------------------------------


def test_advanced_stream_filters_round_trip_and_mutations() -> None:
    raw = _load_example("aiostreams.json")
    inspected = inspect_advanced_stream_filters(raw)
    assert inspected["maxSizeGb"] is None
    assert inspected["excludeQualities"] == ["3D"]
    assert inspected["excludeKeywords"] == ["r00", "iso"]
    assert inspected["preferredCodecs"] == ["AV1", "HEVC", "AVC"]
    assert inspected["hdrPolicy"] == "prefer_dv_hdr"
    assert inspected["minSeeders"] == 0
    assert inspected["rpdbPostersEnabled"] is True

    # Zero-diff round-trip on starter config
    rt = apply_advanced_stream_filters(raw, inspected)
    assert isinstance(rt, Ok)
    assert rt.value == raw

    mut_res = apply_advanced_stream_filters(
        raw,
        {
            "maxSizeGb": 35,
            "excludeQualities": ["CAM", "TS", "SCR", "3D"],
            "excludeKeywords": ["r00", "iso", " telesync ", "sample"],
            "preferredCodecs": ["hevc", "av1"],
            "hdrPolicy": "sdr_only",
            "minSeeders": 12,
            "rpdbPostersEnabled": False,
        },
    )
    assert isinstance(mut_res, Ok), mut_res
    doc2 = mut_res.value
    assert doc2["size"]["global"]["movies"] == [0, 35 * (1024**3)]
    assert doc2["excludedQualities"] == ["CAM", "TS", "SCR"]
    assert "3D" in doc2["excludedVisualTags"]
    assert "DV" in doc2["excludedVisualTags"]
    assert doc2["preferredEncodes"] == ["HEVC", "AV1", "Unknown"]
    assert doc2["requiredSeederRange"] == [12, 100000]
    assert doc2["seederRangeTypes"] == ["p2p", "uncached"]
    assert doc2["rpdbApiKey"] == ""
    assert doc2["posterService"] == "none"

    insp2 = inspect_advanced_stream_filters(doc2)
    assert insp2["maxSizeGb"] == 35
    assert insp2["excludeQualities"] == ["CAM", "TS", "SCR", "3D"]
    assert insp2["excludeKeywords"] == ["r00", "iso", "telesync", "sample"]
    assert insp2["preferredCodecs"] == ["HEVC", "AV1"]
    assert insp2["hdrPolicy"] == "sdr_only"
    assert insp2["minSeeders"] == 12
    assert insp2["rpdbPostersEnabled"] is False

    # Reset cap/seeders + test `hdrPolicy == "any"` + drop `"3D"`
    any_hdr_res = apply_advanced_stream_filters(
        doc2,
        {
            "maxSizeGb": 0,
            "excludeQualities": ["CAM"],
            "hdrPolicy": "any",
            "minSeeders": 0,
            "rpdbPostersEnabled": True,
        },
    )
    assert isinstance(any_hdr_res, Ok)
    doc3 = any_hdr_res.value
    assert "size" not in doc3
    assert "requiredSeederRange" not in doc3
    assert doc3["excludedVisualTags"] == []
    assert inspect_advanced_stream_filters(doc3)["hdrPolicy"] == "any"
    assert inspect_advanced_stream_filters(doc3)["rpdbPostersEnabled"] is True
    assert doc3["rpdbApiKey"] == "{{SECRET_RPDB_API_KEY}}"

    assert isinstance(apply_advanced_stream_filters(raw, {"maxSizeGb": -5}), Err)
    assert isinstance(apply_advanced_stream_filters(raw, {"excludeQualities": ["VHS-RIP-99"]}), Err)
    assert isinstance(apply_advanced_stream_filters(raw, {"preferredCodecs": ["VP99"]}), Err)
    assert isinstance(apply_advanced_stream_filters(raw, {"hdrPolicy": "dolby_magic"}), Err)
    assert isinstance(apply_advanced_stream_filters(raw, {"minSeeders": -1}), Err)


# ---------------------------------------------------------------------------
# 5. AIOMetadata Power — Providers, AI Search, Posters & Catalog Overrides
# ---------------------------------------------------------------------------


def test_aiometadata_power_providers_ai_search_posters_and_catalogs() -> None:
    raw = _load_example("aiometadata.json")
    insp = inspect_aiometadata_power(raw)
    assert insp["movieProvider"] == "tmdb"
    assert insp["seriesProvider"] == "tmdb"
    assert insp["animeProvider"] == "tvdb"
    assert insp["aiSearchEnabled"] is False
    assert insp["aiSearchProvider"] == "gemini"
    assert insp["posterProvider"] == "postersplus"
    assert insp["customPosterUrlPattern"].startswith("https://postersplus.slokker.cc/poster")
    pc = insp["posterCustomizations"]
    assert pc["statusMode"] == "notch"
    assert pc["tvStatusOverlay"] is True
    assert pc["showWatchingStatus"] is True
    assert pc["awardsMask"] == 7
    assert pc["maxBadges"] == 3
    assert "imdb" in pc["ratings"]
    assert insp["posterOptions"]["enableRatingPostersForLibrary"] is True
    assert insp["posterOptions"]["englishArtOnly"] is True

    # Updating Posters+ customizations preserves template placeholders & other query params
    pc_res = apply_aiometadata_power(
        raw,
        {
            "posterCustomizations": {
                "ratings": ["imdb", "tomatoes:mt"],
                "maxBadges": 2,
                "statusMode": "pill",
                "tvStatusOverlay": False,
                "awardsMask": 3,
                "badgeStyle": "solid",
            },
            "posterOptions": {"blurThumbs": True, "englishArtOnly": False},
        },
    )
    assert isinstance(pc_res, Ok), pc_res
    pc_insp = inspect_aiometadata_power(pc_res.value)
    assert "{tmdb_id?}" in pc_insp["customPosterUrlPattern"]
    assert "{imdb_id?}" in pc_insp["customPosterUrlPattern"]
    assert pc_insp["posterCustomizations"]["ratings"] == ["imdb", "tomatoes:mt"]
    assert pc_insp["posterCustomizations"]["maxBadges"] == 2
    assert pc_insp["posterCustomizations"]["statusMode"] == "pill"
    assert pc_insp["posterCustomizations"]["tvStatusOverlay"] is False
    assert pc_insp["posterCustomizations"]["awardsMask"] == 3
    assert pc_insp["posterCustomizations"]["badgeStyle"] == "solid"
    assert pc_insp["posterOptions"]["blurThumbs"] is True
    assert pc_insp["posterOptions"]["englishArtOnly"] is False
    assert len(insp["catalogs"]) > 40
    assert set(insp["catalogs"][0].keys()) == {"id", "name", "type", "source", "enabled", "showInHome"}
    assert required_aiometadata_power_secrets(raw) == (
        "SECRET_TMDB_API_KEY",
        "SECRET_TRAKT_TOKEN_ID",
        "SECRET_TVDB_API_KEY",
    )

    # Zero-diff round-trip on starter config
    rt = apply_aiometadata_power(
        raw,
        {
            "movieProvider": insp["movieProvider"],
            "seriesProvider": insp["seriesProvider"],
            "animeProvider": insp["animeProvider"],
            "searchEngines": insp["searchEngines"],
            "aiSearchEnabled": insp["aiSearchEnabled"],
            "aiSearchProvider": insp["aiSearchProvider"],
            "posterProvider": insp["posterProvider"],
            "catalogOverrides": insp["catalogs"],
        },
    )
    assert isinstance(rt, Ok), rt
    assert rt.value == raw

    # Switch providers + enable OpenRouter AI Search + TopPosters rating engine + catalog overrides
    power_res = apply_aiometadata_power(
        raw,
        {
            "movieProvider": "tvdb",
            "seriesProvider": "tvdb",
            "animeProvider": "mal",
            "searchEngines": {"mal.search.series": True, "tvmaze.search": False},
            "aiSearchEnabled": True,
            "aiSearchProvider": "openrouter",
            "posterProvider": "topposters",
            "catalogOverrides": [
                # Reorder `tmdb.top_rated` (movie) ahead of `tmdb.top` (movie) AND rename it
                {"id": "tmdb.top_rated", "type": "movie", "name": "Critics Choice Films"},
                {"id": "tmdb.top", "type": "movie", "showInHome": False},
                # Disable Trakt Home catalogs so Trakt token becomes optional
                {"id": "trakt.trending.movies", "type": "movie", "enabled": False, "showInHome": False},
                {"id": "trakt.trending.shows", "type": "series", "enabled": False, "showInHome": False},
            ],
        },
    )
    assert isinstance(power_res, Ok), power_res
    doc2 = power_res.value

    insp2 = inspect_aiometadata_power(doc2)
    assert insp2["movieProvider"] == "tvdb"
    assert insp2["seriesProvider"] == "tvdb"
    assert insp2["animeProvider"] == "mal"
    assert insp2["searchEngines"]["mal.search.series"] is True
    assert insp2["searchEngines"]["tvmaze.search"] is False
    assert insp2["searchEngines"]["gemini.search"] is True
    assert insp2["aiSearchEnabled"] is True
    assert insp2["aiSearchProvider"] == "openrouter"
    assert insp2["posterProvider"] == "topposters"
    assert doc2["apiKeys"]["openrouter"] == "{{SECRET_OPENROUTER_API_KEY}}"
    assert doc2["apiKeys"]["gemini"] == ""
    assert doc2["apiKeys"]["topPoster"] == "{{SECRET_TOP_POSTER_API_KEY}}"

    # Verify `(id, type)` disambiguation and slot reordering
    movie_cats = [c for c in insp2["catalogs"] if c["type"] == "movie"]
    idx_top_rated = next(i for i, c in enumerate(movie_cats) if c["id"] == "tmdb.top_rated")
    idx_top = next(i for i, c in enumerate(movie_cats) if c["id"] == "tmdb.top")
    assert idx_top_rated < idx_top
    assert movie_cats[idx_top_rated]["name"] == "Critics Choice Films"
    assert movie_cats[idx_top]["showInHome"] is False
    series_top = next(c for c in insp2["catalogs"] if c["id"] == "tmdb.top" and c["type"] == "series")
    assert series_top["showInHome"] is True  # series sibling untouched!

    assert required_aiometadata_power_secrets(doc2) == (
        "SECRET_OPENROUTER_API_KEY",
        "SECRET_TMDB_API_KEY",
        "SECRET_TOP_POSTER_API_KEY",
        "SECRET_TVDB_API_KEY",
    )

    # Switch AI search to Gemini & poster provider to RPDB, then None
    gemini_rpdb_res = apply_aiometadata_power(
        doc2,
        {"aiSearchProvider": "gemini", "posterProvider": "rpdb"},
    )
    assert isinstance(gemini_rpdb_res, Ok)
    assert required_aiometadata_power_secrets(gemini_rpdb_res.value) == (
        "SECRET_GEMINI_API_KEY",
        "SECRET_RPDB_API_KEY",
        "SECRET_TMDB_API_KEY",
        "SECRET_TVDB_API_KEY",
    )

    none_res = apply_aiometadata_power(
        gemini_rpdb_res.value,
        {"aiSearchEnabled": False, "posterProvider": "none"},
    )
    assert isinstance(none_res, Ok)
    assert required_aiometadata_power_secrets(none_res.value) == (
        "SECRET_TMDB_API_KEY",
        "SECRET_TVDB_API_KEY",
    )

    assert isinstance(apply_aiometadata_power(raw, {"movieProvider": "rottentomatoes"}), Err)
    assert isinstance(apply_aiometadata_power(raw, {"aiSearchProvider": "claude_direct"}), Err)
    assert isinstance(apply_aiometadata_power(raw, {"posterProvider": "fanart_only"}), Err)


# ---------------------------------------------------------------------------
# 6. AIOManager Power & Profile Standalone Extra Addons
# ---------------------------------------------------------------------------


def test_aiomanager_power_inspect_and_apply_preserves_accounts_and_saved_addons() -> None:
    assert inspect_aiomanager_power({}, {})["present"] is False

    mgr_raw = _load_example("aiomanager.json")
    env_unconfigured = {"AIOMANAGER_URL": "https://aiomanager.elfhosted.com"}
    s1 = inspect_aiomanager_power(mgr_raw, env_unconfigured)
    assert s1["present"] is True
    assert s1["syncConfigured"] is False
    assert s1["autopilotEnabled"] is False
    assert s1["syncIntervalMinutes"] == 15
    assert s1["webhookLinked"] is False
    assert s1["vaultCount"] == 0
    assert len(s1["managedAccounts"]) == 1
    acc = s1["managedAccounts"][0]
    assert acc == {
        "id": "00000000-0000-4000-8000-000000000005",
        "name": "Demo",
        "emoji": "🎬",
        "accentColor": "#3b82f6",
        "addonsCount": 4,
        "nuvioConnected": True,
        "stremioConnected": True,
    }

    # Zero-diff round-trip on starter `aiomanager.json`
    rt = apply_aiomanager_power(mgr_raw, s1)
    assert isinstance(rt, Ok)
    assert rt.value == mgr_raw

    env_configured = {
        "AIOMANAGER_URL": "https://aiomanager.elfhosted.com",
        "AIOMANAGER_UUID": "00000000-0000-4000-8000-000000000099",
        "AIOMANAGER_SYNC_PASSWORD": "sync-secret-password",
    }
    applied_res = apply_aiomanager_power(
        mgr_raw,
        {
            "autopilotEnabled": True,
            "syncIntervalMinutes": 30,
            "autoSync": True,
            "webhookUrl": "https://hooks.example.com/failover",
            "webhookEnabled": True,
        },
    )
    assert isinstance(applied_res, Ok), applied_res
    updated_mgr = applied_res.value
    assert updated_mgr["accounts"] == mgr_raw["accounts"]
    assert updated_mgr["addons"] == mgr_raw["addons"]

    s2 = inspect_aiomanager_power(updated_mgr, env_configured)
    assert s2["syncConfigured"] is True
    assert s2["autopilotEnabled"] is True
    assert s2["syncIntervalMinutes"] == 30
    assert s2["webhookLinked"] is True

    assert isinstance(apply_aiomanager_power(mgr_raw, {"syncIntervalMinutes": 0}), Err)
    assert isinstance(apply_aiomanager_power(mgr_raw, {"webhookUrl": "ftp://bad.example"}), Err)


def test_normalize_extra_addons_preserves_cinemeta_config_and_validates_profile() -> None:
    prof_raw = _load_example("profiles/main/profile.json")
    parsed_res = parse_profile(prof_raw)
    assert isinstance(parsed_res, Ok)
    prof = parsed_res.value

    serialized = inspect_extra_addons(prof.extra_addons)
    assert [x["name"] for x in serialized] == ["bingecat", "cinemeta"]

    # Round-trip preserves `profile.json` byte-for-byte
    norm_rt = normalize_extra_addons(prof.extra_addons, serialized)
    assert isinstance(norm_rt, Ok)
    assert profile_to_doc(replace(prof, extra_addons=norm_rt.value)) == prof_raw

    # Add standalone addon, toggle `bingecat` off, and omit `metadata` on `cinemeta`
    mutated_res = normalize_extra_addons(
        prof.extra_addons,
        [
            {
                "name": "bingecat",
                "transportUrl": "https://bingecat.com/stremio/{{SECRET_BINGECAT_TOKEN}}/manifest.json",
                "customName": "BingeCat",
                "enabled": False,
            },
            {
                "name": "cinemeta",
                "transportUrl": "https://v3-cinemeta.strem.io",
                "customName": "Cinemeta",
                "metadata": {},  # empty metadata from UI must NOT clobber `cinemetaConfig`!
            },
            {
                "customName": "Anime Kitsu Catalogs!",
                "manifestUrl": "stremio://anime-kitsu.strem.fun/manifest.json",
                "enabled": True,
            },
        ],
    )
    assert isinstance(mutated_res, Ok), mutated_res
    extras2 = mutated_res.value
    assert [a.name for a in extras2] == ["bingecat", "cinemeta", "anime-kitsu-catalogs"]
    assert extras2[0].enabled is False
    assert extras2[1].metadata == {
        "cinemetaConfig": {
            "removeSearch": True,
            "removeStandardCatalogs": True,
            "removeMeta": False,
        }
    }
    assert extras2[2].transport_url == "https://anime-kitsu.strem.fun/manifest.json"

    # Resulting profile document validates cleanly against `parse_profile`
    cand_doc = profile_to_doc(replace(prof, extra_addons=extras2))
    assert isinstance(parse_profile(cand_doc), Ok)

    # Remove `anime-kitsu-catalogs`
    removed_res = normalize_extra_addons(extras2, [extras2[0], extras2[1]])
    assert isinstance(removed_res, Ok)
    assert len(removed_res.value) == 2

    assert isinstance(normalize_extra_addons(prof.extra_addons, [{"name": "bad", "transportUrl": ""}]), Err)
