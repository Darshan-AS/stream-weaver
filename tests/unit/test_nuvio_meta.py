"""Unit tests for Nuvio Meta enrichment compilation, safe blob merging, credentials, and round-trip sync."""

import base64
import json
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.coercions import (
    compute_config_diff,
    dehydrate_aiomanager,
    rehydrate_aiomanager,
)
from stream_weaver.core.crypto import encrypt_field
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.nuvio_meta import (
    RATING_PROVIDERS,
    TMDB_MODULES,
    build_platform_meta_features,
    build_provider_credentials,
    compile_meta_screen_payload,
    decompile_meta_screen_payload,
    decompile_nuvio_meta,
    merge_profile_settings_blob_meta,
    merge_provider_credential_rows,
    normalize_nuvio_meta_doc,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets
from stream_weaver.effects.http import HttpResponse
from stream_weaver.providers.services.clients import NuvioClientSync

FULL_DOC: dict[str, Any] = {
    "version": "1.0.0",
    "platforms": ["tv", "desktop", "mobile"],
    "ratings": {
        "enabled": True,
        "apiKey": "mdb-key-0123456789",
        "providers": dict.fromkeys(RATING_PROVIDERS, True),
    },
    "tmdbEnrichment": {
        "enabled": True,
        "apiKey": "tmdb-key-0123456789",
        "language": "en-US",
        "modules": {name: True for name, _ in TMDB_MODULES},
    },
    "traktComments": True,
    "episodeRatings": "show",
    "metaScreen": {
        "backgroundMode": "cinematic",
        "heroTrailerPlayback": False,
        "tabLayout": False,
        "episodeCardStyle": "horizontal",
        "blurUnwatchedEpisodes": False,
        "sections": [
            {"key": "ACTIONS", "enabled": True},
            {"key": "OVERVIEW", "enabled": True},
            {"key": "EPISODES", "enabled": True},
            {"key": "TRAILERS", "enabled": True},
            {"key": "CAST", "enabled": True},
            {"key": "PRODUCTION", "enabled": True},
            {"key": "DETAILS", "enabled": True},
            {"key": "COMMENTS", "enabled": True},
            {"key": "COLLECTION", "enabled": True},
            {"key": "MORE_LIKE_THIS", "enabled": True},
        ],
    },
}


def _normalized(doc: Mapping[str, Any]) -> dict[str, Any]:
    res = normalize_nuvio_meta_doc(doc)
    assert isinstance(res, Ok), res
    return res.value


def test_normalize_fills_defaults_and_validates() -> None:
    minimal = _normalized({})
    assert minimal["platforms"] == ["tv", "desktop", "mobile"]
    assert minimal["ratings"]["enabled"] is True
    assert set(minimal["ratings"]["providers"]) == set(RATING_PROVIDERS)
    assert minimal["tmdbEnrichment"]["language"] == "en-US"
    assert minimal["traktComments"] is True
    assert minimal["episodeRatings"] == "show"
    assert minimal["metaScreen"] is None
    assert isinstance(normalize_nuvio_meta_doc({"episodeRatings": "sometimes"}), Err)

    assert isinstance(normalize_nuvio_meta_doc({"platforms": ["roku"]}), Err)
    assert isinstance(normalize_nuvio_meta_doc({"metaScreen": {"backgroundMode": "neon"}}), Err)
    assert isinstance(
        normalize_nuvio_meta_doc({"metaScreen": {"sections": [{"key": "NOPE"}]}}), Err
    )

    # Partial section lists are completed in Nuvio default order; declared order wins.
    partial = _normalized({"metaScreen": {"sections": [{"key": "comments", "enabled": False}]}})
    keys = [s["key"] for s in partial["metaScreen"]["sections"]]
    assert keys[0] == "COMMENTS"
    assert len(keys) == 10
    assert len(set(keys)) == 10
    assert partial["metaScreen"]["sections"][0]["enabled"] is False
    assert [s["order"] for s in partial["metaScreen"]["sections"]] == list(range(10))


def test_meta_screen_payload_roundtrip_matches_nuvio_shape() -> None:
    norm = _normalized(FULL_DOC)
    payload = compile_meta_screen_payload(norm["metaScreen"])
    parsed = json.loads(payload)
    assert parsed["background_mode"] == "cinematic"
    assert parsed["cinematicBackground"] is True
    assert parsed["tvStyleLayout"] is False
    assert parsed["items"][2] == {"key": "EPISODES", "enabled": True, "order": 2, "tabGroup": None}
    assert decompile_meta_screen_payload(payload) == norm["metaScreen"]
    assert decompile_meta_screen_payload("") is None
    assert decompile_meta_screen_payload("not-json") is None


def test_platform_feature_fragments_use_platform_specific_keys() -> None:
    norm = _normalized(FULL_DOC)

    tv = build_platform_meta_features(norm, platform="tv")
    assert set(tv) == {"mdblist_settings", "tmdb_settings", "trakt_settings", "layout_settings"}
    assert tv["layout_settings"] == {
        "detail_imdb_ratings_visibility": {"type": "string", "value": "SHOW_ALL"}
    }
    hidden = build_platform_meta_features(
        _normalized({**FULL_DOC, "episodeRatings": "hideUnwatched"}), platform="tv"
    )
    assert (
        hidden["layout_settings"]["detail_imdb_ratings_visibility"]["value"]
        == "HIDE_UNWATCHED_EPISODES"
    )
    assert "mdblist_api_key" not in tv["mdblist_settings"]
    assert tv["mdblist_settings"]["mdblist_show_tomatoes"] == {"type": "boolean", "value": True}
    assert "tmdb_api_key" not in tv["tmdb_settings"]
    assert "tmdb_use_release_dates" not in tv["tmdb_settings"]
    assert "tmdb_use_season_posters" not in tv["tmdb_settings"]
    assert tv["trakt_settings"] == {"show_meta_comments": {"type": "boolean", "value": True}}

    mobile = build_platform_meta_features(norm, platform="mobile")
    assert set(mobile) == {
        "mdblist_settings",
        "tmdb_settings",
        "trakt_comments_settings",
        "meta_screen_settings_payload",
    }
    assert mobile["mdblist_settings"]["mdblist_api_key"] == {
        "type": "string",
        "value": "mdb-key-0123456789",
    }
    assert mobile["mdblist_settings"]["mdblist_use_letterboxd"] == {
        "type": "boolean",
        "value": True,
    }
    assert mobile["tmdb_settings"]["tmdb_api_key"] == {
        "type": "string",
        "value": "tmdb-key-0123456789",
    }
    assert mobile["tmdb_settings"]["tmdb_use_season_posters"] == {"type": "boolean", "value": True}
    assert mobile["trakt_comments_settings"] == {
        "comments_enabled": {"type": "boolean", "value": True},
        "trakt_comments_enabled": {"type": "boolean", "value": True},
    }
    assert isinstance(mobile["meta_screen_settings_payload"], str)

    assert build_platform_meta_features(norm, platform="desktop") == mobile


def test_merge_preserves_unrelated_prefs_and_refuses_to_seed_mobile_blobs() -> None:
    norm = _normalized(FULL_DOC)
    tv_features = build_platform_meta_features(norm, platform="tv")

    # Fresh TV blob is safe to create (TV imports feature-by-feature).
    fresh_tv = merge_profile_settings_blob_meta(None, tv_features, platform="tv")
    assert fresh_tv is not None
    assert fresh_tv["version"] == 1
    assert fresh_tv["features"]["trakt_settings"]["show_meta_comments"]["value"] is True

    # Existing TV trakt_settings (watch-progress source etc.) must survive the overlay.
    remote_tv: dict[str, Any] = {
        "version": 1,
        "features": {
            "trakt_settings": {
                "watch_progress_source": {"type": "string", "value": "TRAKT"},
                "show_meta_comments": {"type": "boolean", "value": False},
            },
            "tmdb_settings": {"tmdb_modern_home_enabled": {"type": "boolean", "value": True}},
            "theme_settings": {"dark": {"type": "boolean", "value": True}},
        },
    }
    merged_tv = merge_profile_settings_blob_meta(remote_tv, tv_features, platform="tv")
    assert merged_tv is not None
    assert merged_tv["features"]["trakt_settings"]["watch_progress_source"]["value"] == "TRAKT"
    assert merged_tv["features"]["trakt_settings"]["show_meta_comments"]["value"] is True
    assert merged_tv["features"]["tmdb_settings"]["tmdb_modern_home_enabled"]["value"] is True
    assert merged_tv["features"]["tmdb_settings"]["tmdb_enabled"]["value"] is True
    assert merged_tv["features"]["theme_settings"] == remote_tv["features"]["theme_settings"]
    # Pure: input untouched
    assert "tmdb_enabled" not in remote_tv["features"]["tmdb_settings"]

    # Desktop/mobile never get a partial blob seeded from scratch.
    mobile_features = build_platform_meta_features(norm, platform="mobile")
    assert merge_profile_settings_blob_meta(None, mobile_features, platform="mobile") is None
    assert merge_profile_settings_blob_meta(None, mobile_features, platform="desktop") is None


def test_provider_credentials_merge_seeds_missing_and_preserves_debrid() -> None:
    norm = _normalized(FULL_DOC)
    desired = build_provider_credentials(norm)
    assert desired == [
        {"provider": "mdblist", "credential_json": {"api_key": "mdb-key-0123456789"}},
        {"provider": "tmdb", "credential_json": {"api_key": "tmdb-key-0123456789"}},
    ]
    assert build_provider_credentials(_normalized({})) == []

    remote = [
        {
            "provider": "debrid:torbox",
            "credential_json": {"api_key": "tb-secret"},
            "updated_at": "x",
        },
        {"provider": "mdblist", "credential_json": {"api_key": "old-mdb"}},
    ]
    merged, to_seed = merge_provider_credential_rows(remote, desired)
    assert to_seed == [{"provider": "tmdb", "credential_json": {"api_key": "tmdb-key-0123456789"}}]
    assert merged == [
        {"provider": "debrid:torbox", "credential_json": {"api_key": "tb-secret"}},
        {"provider": "mdblist", "credential_json": {"api_key": "mdb-key-0123456789"}},
        {"provider": "tmdb", "credential_json": {"api_key": "tmdb-key-0123456789"}},
    ]


def test_decompile_reports_only_synced_platforms_and_prefers_credential_rows() -> None:
    norm = _normalized(FULL_DOC)
    blobs: dict[str, Mapping[str, Any] | None] = {}
    for plat in ("tv", "desktop", "mobile"):
        base = (
            {"version": 1, "features": {}}
            if plat == "tv"
            else {"version": 4, "features": {"theme_settings": {}}}
        )
        blobs[plat] = merge_profile_settings_blob_meta(
            base, build_platform_meta_features(norm, platform=plat), platform=plat
        )

    roundtrip = decompile_nuvio_meta(blobs, [], version="1.0.0")
    assert roundtrip == norm

    # Credential rows override legacy in-blob keys.
    with_rows = decompile_nuvio_meta(
        blobs,
        [{"provider": "mdblist", "credential_json": {"api_key": "rotated-mdb-key"}}],
    )
    assert with_rows["ratings"]["apiKey"] == "rotated-mdb-key"
    assert with_rows["tmdbEnrichment"]["apiKey"] == "tmdb-key-0123456789"

    # Drift on one platform drops it from `platforms`.
    drifted = json.loads(json.dumps(blobs["tv"]))
    drifted["features"]["mdblist_settings"]["mdblist_show_metacritic"]["value"] = False
    drift_doc = decompile_nuvio_meta({**blobs, "tv": drifted}, [])
    assert drift_doc["platforms"] == ["desktop", "mobile"]
    assert compute_config_diff(norm, drift_doc, target="nuvio_meta").in_sync is False

    # Nothing remote -> empty platforms + defaults
    empty = decompile_nuvio_meta({"tv": None, "desktop": None, "mobile": None}, [])
    assert empty["platforms"] == []
    assert empty["ratings"]["apiKey"] == ""


def test_secret_lenses_scrub_and_reinject_api_keys() -> None:
    norm = _normalized(FULL_DOC)
    scrubbed, env = harvest_and_scrub("aiomanager", norm, {})
    assert scrubbed["ratings"]["apiKey"] == "{{SECRET_MDBLIST_API_KEY}}"
    assert scrubbed["tmdbEnrichment"]["apiKey"] == "{{SECRET_TMDB_API_KEY}}"
    assert env["SECRET_MDBLIST_API_KEY"] == "mdb-key-0123456789"
    assert env["SECRET_TMDB_API_KEY"] == "tmdb-key-0123456789"
    reinjected = inject_secrets(scrubbed, env)
    assert isinstance(reinjected, Ok)
    assert reinjected.value == norm


def test_aiomanager_coercions_propagate_and_strip_nuvio_meta() -> None:
    raw_mgr: dict[str, Any] = {
        "accounts": [{"id": "acc-1", "addons": []}],
        "nuvioMeta": {"traktComments": True},
    }
    rehydrated = rehydrate_aiomanager(raw_mgr)
    assert "nuvioMeta" not in rehydrated
    assert rehydrated["accounts"][0]["nuvioMeta"] == {"traktComments": True}
    dehydrated_res = dehydrate_aiomanager(rehydrated)
    assert isinstance(dehydrated_res, Ok)
    assert "nuvioMeta" not in dehydrated_res.value["accounts"][0]


def test_nuvio_client_sync_push_and_pull_meta_e2e() -> None:
    password = "master-sync-pass"
    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    enc_access = encrypt_field("nuvio-jwt-tok", password, salt_b64)
    conn = {
        "platform": "nuvio",
        "enabled": True,
        "credentials": {"accessToken": enc_access, "profileId": "p-uuid-1"},
    }
    env = {"NUVIO_SUPABASE_ANON_KEY": "anon-key-123"}

    remote_blobs: dict[str, dict[str, Any] | None] = {
        "tv": {
            "version": 1,
            "features": {
                "trakt_settings": {"watch_progress_source": {"type": "string", "value": "TRAKT"}},
                "layout_settings": {
                    "discover_location": {"type": "string", "value": "HOME"},
                    "detail_imdb_ratings_visibility": {"type": "string", "value": "HIDE_EPISODES"},
                },
            },
        },
        "desktop": {
            "version": 4,
            "features": {"theme_settings": {"amoled": {"type": "boolean", "value": False}}},
        },
        "mobile": None,  # never initialised -> must be skipped, not seeded
    }
    remote_creds: dict[str, dict[str, Any]] = {
        "debrid:torbox": {"provider": "debrid:torbox", "credential_json": {"api_key": "tb-secret"}},
    }
    rpc_log: list[str] = []

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
            rpc_log.append(url.rsplit("/", 1)[-1])
            if url.endswith("/sync_pull_profiles"):
                return Ok(HttpResponse(200, [{"id": "p-uuid-1", "profile_index": 2}], "[]"))
            if url.endswith("/sync_pull_profile_settings_blob"):
                assert isinstance(json_body, Mapping)
                assert json_body["p_profile_id"] == 2
                blob = remote_blobs[str(json_body["p_platform"])]
                rows = [{"profile_id": 2, "settings_json": blob}] if blob is not None else []
                return Ok(HttpResponse(200, rows, "[]"))
            if url.endswith("/sync_push_profile_settings_blob"):
                assert isinstance(json_body, Mapping)
                assert json_body["p_origin_client_id"]
                remote_blobs[str(json_body["p_platform"])] = dict(json_body["p_settings_json"])
                return Ok(HttpResponse(204, {}, ""))
            if url.endswith("/sync_pull_provider_credentials"):
                return Ok(HttpResponse(200, list(remote_creds.values()), "[]"))
            if url.endswith("/sync_seed_provider_credentials"):
                assert isinstance(json_body, Mapping)
                for row in json_body["p_credentials"]:
                    remote_creds.setdefault(row["provider"], dict(row))
                return Ok(HttpResponse(204, {}, ""))
            if url.endswith("/sync_push_provider_credentials"):
                assert isinstance(json_body, Mapping)
                for row in json_body["p_credentials"]:
                    remote_creds[row["provider"]] = dict(row)
                return Ok(HttpResponse(204, {}, ""))
            return Err(NetworkError(message=f"Unexpected URL {url}", status_code=404, url=url))

    syncer = NuvioClientSync(FakeNuvioHttp())
    push_res = syncer.push_meta(FULL_DOC, conn, password, salt_b64, env)
    assert isinstance(push_res, Ok), push_res
    msg, _ = push_res.value
    assert "8 rating providers, TMDB, Trakt comments across tv, desktop" in msg
    assert "2 provider credential(s)" in msg
    assert "profile #2" in msg

    # Mobile was uninitialised -> untouched
    assert remote_blobs["mobile"] is None
    # TV kept its Trakt watch-progress source alongside the new comments flag
    tv_feats = (remote_blobs["tv"] or {})["features"]
    assert tv_feats["trakt_settings"]["watch_progress_source"]["value"] == "TRAKT"
    assert tv_feats["trakt_settings"]["show_meta_comments"]["value"] is True
    assert "mdblist_api_key" not in tv_feats["mdblist_settings"]
    assert tv_feats["layout_settings"]["discover_location"]["value"] == "HOME"
    assert tv_feats["layout_settings"]["detail_imdb_ratings_visibility"]["value"] == "SHOW_ALL"
    # Desktop kept theme settings and got the meta-screen payload
    desk_feats = (remote_blobs["desktop"] or {})["features"]
    assert desk_feats["theme_settings"] == {"amoled": {"type": "boolean", "value": False}}
    assert json.loads(desk_feats["meta_screen_settings_payload"])["background_mode"] == "cinematic"
    # Credentials: debrid preserved, mdblist+tmdb seeded then upserted
    assert set(remote_creds) == {"debrid:torbox", "mdblist", "tmdb"}
    assert remote_creds["mdblist"]["credential_json"] == {"api_key": "mdb-key-0123456789"}
    assert rpc_log.index("sync_seed_provider_credentials") < rpc_log.index(
        "sync_push_provider_credentials"
    )

    pull_res = syncer.pull_meta(conn, password, salt_b64, env)
    assert isinstance(pull_res, Ok)
    pulled_doc, _ = pull_res.value
    expected = _normalized(FULL_DOC)
    expected["platforms"] = ["tv", "desktop"]  # mobile skipped above
    assert pulled_doc == expected
    assert compute_config_diff(expected, pulled_doc, target="nuvio_meta").in_sync is True
