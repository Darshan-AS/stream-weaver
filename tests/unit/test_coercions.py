"""Unit tests for config normalizers and AIOManager dehydration/rehydration."""

from collections.abc import Mapping
from typing import Any

from stream_weaver.core.coercions import (
    compute_config_diff,
    dehydrate_aiomanager,
    normalize_aiometadata,
    normalize_aiostreams,
    rehydrate_aiomanager,
    sanitize_manifest_home_catalogs,
)
from stream_weaver.core.crypto import decrypt_envelope, encrypt_envelope
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.result import Ok, Result
from stream_weaver.effects.http import HttpResponse
from stream_weaver.providers.services import AIOManagerService, extract_home_catalog_urls
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

    # Rehydrating with live remote telemetry preserves remote watch history & changelog
    rehydrated_with_remote = rehydrate_aiomanager(
        dehydrated,
        remote_telemetry={
            "watchEvents": [{"id": "evt1"}, {"id": "evt2"}],
            "changelog": [{"action": "sync"}],
            "watchSnapshot": {"tt123": 42},
        },
    )
    assert rehydrated_with_remote["watchEvents"] == [{"id": "evt1"}, {"id": "evt2"}]
    assert rehydrated_with_remote["changelog"] == [{"action": "sync"}]
    assert rehydrated_with_remote["watchSnapshot"] == {"tt123": 42}


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


def test_aiomanager_push_remote_preserves_encrypted_telemetry() -> None:
    password = "test-sync-password-123"
    live_remote_state: dict[str, Any] = {
        "version": "2.0.0",
        "accounts": [],
        "watchEvents": [{"id": "we-99", "imdbId": "tt0111161"}],
        "changelog": [{"id": "cl-1", "action": "addon_update"}],
        "watchSnapshot": {"tt0111161": 1800},
    }
    enc_res = encrypt_envelope(live_remote_state, password)
    assert isinstance(enc_res, Ok)
    remote_envelope = enc_res.value

    pushed_bodies: list[Any] = []

    class FakeHttpClient:
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
            _ = url, headers, raw_body, timeout
            if method == "GET":
                return Ok(HttpResponse(status_code=200, data=remote_envelope, text="{}"))
            pushed_bodies.append(json_body)
            return Ok(HttpResponse(status_code=200, data={"ok": True}, text='{"ok": true}'))

    svc = AIOManagerService(FakeHttpClient())
    local_dehydrated: dict[str, Any] = {
        "version": "2.0.0",
        "accounts": [{"id": "acc-1", "name": "Test", "addons": []}],
    }
    env = {
        "AIOMANAGER_URL": "https://aiomanager.example.com",
        "AIOMANAGER_UUID": "u-test-1",
        "AIOMANAGER_SYNC_PASSWORD": password,
    }
    push_res = svc.push_remote(local_dehydrated, env)
    assert isinstance(push_res, Ok)
    _, dehydrated_for_disk, _ = push_res.value

    # Disk copy stays compact and stripped of volatile telemetry
    assert "watchEvents" not in dehydrated_for_disk
    assert "changelog" not in dehydrated_for_disk

    # Encrypted cloud envelope retains remote watchEvents, changelog, and watchSnapshot
    assert len(pushed_bodies) == 1
    dec_pushed = decrypt_envelope(pushed_bodies[0], password)
    assert isinstance(dec_pushed, Ok)
    assert dec_pushed.value["watchEvents"] == [{"id": "we-99", "imdbId": "tt0111161"}]
    assert dec_pushed.value["changelog"] == [{"id": "cl-1", "action": "addon_update"}]
    assert dec_pushed.value["watchSnapshot"] == {"tt0111161": 1800}
    assert dec_pushed.value["accounts"][0]["id"] == "acc-1"
