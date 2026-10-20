"""Unit tests for config normalizers and AIOManager dehydration/rehydration."""

from typing import Any

from stream_weaver.core.coercions import (
    compute_config_diff,
    dehydrate_aiomanager,
    normalize_aiometadata,
    normalize_aiostreams,
    rehydrate_aiomanager,
    sanitize_manifest_home_catalogs,
)
from stream_weaver.core.result import Ok
from stream_weaver.providers.services import extract_home_catalog_urls
from stream_weaver.providers.services.clients import (
    compile_nuvio_collections,
    decompile_nuvio_collections,
)


def test_normalize_aiostreams_unwraps_and_strips_volatile() -> None:
    wrapped = {
        "data": {
            "userData": {
                "uuid": "u-123",
                "encryptedPassword": "enc",
                "ip": "1.2.3.4",
                "services": [{"id": "torbox"}],
            }
        }
    }
    res = normalize_aiostreams(wrapped)
    assert isinstance(res, Ok)
    assert res.value == {"services": [{"id": "torbox"}]}


def test_normalize_aiometadata_strips_volatile() -> None:
    wrapped = {
        "config": {
            "lastModified": "2026-10-17",
            "configVersion": 4,
            "search": {"enabled": True},
        }
    }
    res = normalize_aiometadata(wrapped)
    assert isinstance(res, Ok)
    assert res.value == {"search": {"enabled": True}}


def test_aiomanager_dehydrate_and_rehydrate_roundtrip() -> None:
    huge_manifest = {
        "id": "aio-metadata",
        "version": "3.5.1",
        "name": "AIOMetadata",
        "catalogs": [{"id": f"cat_{i}"} for i in range(50)],
    }
    cinemeta_saved_mf = {
        "id": "com.linvo.cinemeta",
        "version": "3.0.14",
        "name": "Cinemeta",
        "catalogs": [{"id": "top"}],
    }
    cinemeta_account_mf = {
        "id": "com.linvo.cinemeta",
        "version": "3.0.14",
        "name": "Cinemeta",
        "catalogs": [],  # Customized override in account!
    }

    raw_state: dict[str, Any] = {
        "version": "2.0.0",
        "syncedAt": "2026-10-17T00:00:00Z",
        "watchEvents": [{"id": "evt1"}, {"id": "evt2"}],
        "changelog": [{"action": "sync"}],
        "manifests": {
            "aio-metadata:3.5.1": huge_manifest,
            "com.linvo.cinemeta:3.0.14": cinemeta_saved_mf,
        },
        "addons": {
            "savedAddons": [
                {"name": "AIOMetadata", "manifest": huge_manifest},
                {"name": "Cinemeta", "manifest": cinemeta_saved_mf},
            ]
        },
        "accounts": [
            {
                "id": "acc-1",
                "addons": [
                    {
                        "manifestId": "aio-metadata:3.5.1",
                        "manifest": huge_manifest,
                    },
                    {
                        "manifestId": "com.linvo.cinemeta:3.0.14",
                        "manifest": cinemeta_account_mf,
                    },
                ],
            }
        ],
    }

    dehydrated_res = dehydrate_aiomanager(raw_state)
    assert isinstance(dehydrated_res, Ok)
    dehydrated = dehydrated_res.value

    # Telemetry and top-level manifests cache must be stripped
    assert "watchEvents" not in dehydrated
    assert "changelog" not in dehydrated
    assert "manifests" not in dehydrated

    # Duplicate manifest in account addon 0 must be compacted, while custom Cinemeta override stays intact
    acc_addons = dehydrated["accounts"][0]["addons"]
    assert acc_addons[0]["manifest"]["_deduped"] is True
    assert "catalogs" not in acc_addons[0]["manifest"]
    assert acc_addons[1]["manifest"]["catalogs"] == []

    # Rehydrating must restore full manifests and default telemetry fields
    rehydrated = rehydrate_aiomanager(dehydrated)
    assert rehydrated["accounts"][0]["addons"][0]["manifest"] == huge_manifest
    assert rehydrated["accounts"][0]["addons"][1]["manifest"] == cinemeta_account_mf
    assert rehydrated["manifests"]["aio-metadata:3.5.1"] == huge_manifest
    assert rehydrated["watchEvents"] == []


def test_sanitize_manifest_home_catalogs_and_extract_urls() -> None:
    manifest: dict[str, Any] = {
        "id": "aio-metadata",
        "version": "3.5.1",
        "catalogs": [
            {
                "type": "movie",
                "id": "streaming.nfx_movie",
                "extra": [
                    {"name": "genre", "isRequired": False, "default": "Action"},
                    {"name": "skip"},
                ],
            },
            {
                "type": "movie",
                "id": "tmdb.trending",
                "extra": [
                    {"name": "genre", "isRequired": True, "default": "Day"},
                ],
            },
        ],
    }
    sanitized = sanitize_manifest_home_catalogs(manifest)
    assert "default" not in sanitized["catalogs"][0]["extra"][0]
    assert sanitized["catalogs"][1]["extra"][0]["default"] == "Day"

    state: dict[str, Any] = {
        "manifests": {"aio-metadata:3.5.1": sanitized},
        "accounts": [
            {
                "addons": [
                    {
                        "transportUrl": "https://example.com/stremio/u-1/manifest.json",
                        "manifestId": "aio-metadata:3.5.1",
                        "manifest": sanitized,
                        "flags": {"enabled": True},
                    }
                ]
            }
        ],
    }
    urls = extract_home_catalog_urls(state)
    assert urls == ["https://example.com/stremio/u-1/catalog/movie/streaming.nfx_movie.json"]


def test_compile_nuvio_collections_expands_dynamic_bingecat_lists_and_builds_catalog_sources() -> None:
    addons: list[dict[str, Any]] = [
        {
            "manifest": {
                "id": "com.aicat.41792326-0a8b-4c9d-b1d1-9a5aae34d13b",
                "catalogs": [
                    {
                        "id": "aicat_list_for_you_238348",
                        "name": "Mystery Thrillers",
                        "type": "series",
                        "showInHome": True,
                    },
                    {
                        "id": "aicat_list_for_you_238351",
                        "name": "Machines, AI & Cyberpunk",
                        "type": "movie",
                        "showInHome": True,
                    },
                    {
                        "id": "aicat_list_for_you_238347",
                        "name": "When Hearts Meet",
                        "type": "movie",
                        "showInHome": False,
                    },
                ],
            }
        }
    ]
    raw_collections: list[dict[str, Any]] = [
        {
            "id": "col-for-you",
            "title": "For You",
            "folders": [
                {
                    "id": "fld-dynamic",
                    "title": "Daily AI Micro-Genres",
                    "dynamicBingecatLists": True,
                    "sources": [
                        {
                            "addonId": "aio-metadata",
                            "catalogId": "trakt.trending.movies",
                            "type": "movie",
                            "title": "Trending Movies",
                        }
                    ],
                }
            ],
        }
    ]
    compiled = compile_nuvio_collections(raw_collections, addons)
    assert len(compiled) == 1
    fld = compiled[0]["folders"][0]
    assert "dynamicBingecatLists" not in fld
    assert len(fld["sources"]) == 3
    assert len(fld["catalogSources"]) == 3
    assert fld["sources"][1]["catalogId"] == "aicat_list_for_you_238348"
    assert fld["sources"][1]["title"] == "Mystery Thrillers (Series)"
    assert fld["sources"][2]["catalogId"] == "aicat_list_for_you_238351"
    assert fld["sources"][2]["title"] == "Machines, AI & Cyberpunk (Movies)"
    assert fld["catalogSources"][0] == {
        "addonId": "aio-metadata",
        "catalogId": "trakt.trending.movies",
        "type": "movie",
        "genre": "None",
    }


def test_compute_config_diff_identifies_in_sync_and_drift() -> None:
    base = {"language": "en-US", "catalogs": [{"id": "trakt.upnext", "enabled": True}]}
    same = compute_config_diff(base, dict(base), target="aiometadata")
    assert same.in_sync is True
    assert same.changed_keys == ()
    assert same.unified_diff == ""

    remote = {"language": "ja-JP", "catalogs": [{"id": "trakt.upnext", "enabled": True}]}
    diff = compute_config_diff(base, remote, target="aiometadata")
    assert diff.in_sync is False
    assert diff.changed_keys == ("language",)
    assert diff.added_lines == 1
    assert diff.removed_lines == 1
    assert "-  \"language\": \"ja-JP\"" in diff.unified_diff
    assert "+  \"language\": \"en-US\"" in diff.unified_diff


def test_decompile_nuvio_collections_strips_catalog_sources_and_unwraps_rpc() -> None:
    raw = [
        {
            "id": "col-1",
            "title": "For You",
            "pinToTop": True,
            "focusGlowEnabled": True,
            "viewMode": "TABBED_GRID",
            "showAllTab": True,
            "folders": [
                {
                    "id": "fld-1",
                    "title": "Picks",
                    "tileShape": "LANDSCAPE",
                    "focusGifEnabled": False,
                    "hideTitle": False,
                    "sources": [
                        {
                            "provider": "addon",
                            "addonId": "aio-metadata",
                            "catalogId": "trakt.trending.movies",
                            "type": "movie",
                            "genre": "None",
                            "title": "Trending Movies",
                        }
                    ],
                }
            ],
        }
    ]
    compiled = compile_nuvio_collections(raw, addons=[])
    assert "catalogSources" in compiled[0]["folders"][0]

    # Wrap in Supabase RPC envelope and decompile back to declarative form
    rpc_payload = [{"collections_json": compiled}]
    decompiled = decompile_nuvio_collections(rpc_payload)
    assert decompiled == raw
