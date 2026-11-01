"""Unit + property tests for the table-driven Nuvio app settings compiler (`nuvio_settings`)."""

import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from stream_weaver.core.coercions import (
    compute_config_diff,
    dehydrate_aiomanager,
    rehydrate_aiomanager,
)
from stream_weaver.core.crypto import encrypt_field
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.nuvio_settings import (
    BLOCKS,
    OMIT,
    SETTINGS,
    SETTINGS_BY_PATH,
    VALID_NUVIO_SETTINGS_PLATFORMS,
    Setting,
    active_blocks,
    build_platform_settings_features,
    build_settings_provider_credentials,
    comparable_platform_view,
    decompile_nuvio_settings,
    merge_profile_settings_blob_settings,
    normalize_nuvio_settings_doc,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets
from stream_weaver.effects.http import HttpResponse
from stream_weaver.providers.services.clients import NuvioClientSync

REPO_CONFIG = Path(__file__).resolve().parents[2] / "library" / "settings" / "default.json"


def _normalized(doc: Mapping[str, Any]) -> dict[str, Any]:
    res = normalize_nuvio_settings_doc(doc)
    assert isinstance(res, Ok), res
    return res.value


def _get(doc: Mapping[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        cur = cur[part]
    return cur


def _pref(feats: Mapping[str, Any], feature: str, key: str) -> Any:
    return feats[feature][key]["value"]


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


def test_registry_covers_reuse_last_link_on_every_platform() -> None:
    enabled = SETTINGS_BY_PATH["playback.reuseLastLink.enabled"]
    hours = SETTINGS_BY_PATH["playback.reuseLastLink.cacheHours"]
    for setting in (enabled, hours):
        covered: set[str] = set()
        for b in setting.bindings:
            assert b.feature == "player_settings"
            covered |= b.platforms
        assert covered == set(VALID_NUVIO_SETTINGS_PLATFORMS)
    assert enabled.default is False
    assert hours.default == 24
    assert hours.allowed == (1, 2, 3, 6, 12, 24, 48, 72, 168)


def test_registry_defaults_validate_against_their_own_rows() -> None:
    """Every row's default must be accepted by its own validator (guards registry typos)."""
    full_defaults: dict[str, Any] = {b: {} for b in BLOCKS}
    norm = _normalized(full_defaults)
    for setting in SETTINGS:
        assert _get(norm, setting.path) == setting.default, setting.path


def test_repo_config_is_valid_and_enables_reuse_last_link() -> None:
    raw = json.loads(REPO_CONFIG.read_text(encoding="utf-8"))["settings"]
    norm = _normalized(raw)
    assert norm["playback"]["reuseLastLink"] == {"enabled": True, "cacheHours": 24}
    assert active_blocks(norm) == frozenset(BLOCKS)
    assert norm["platforms"] == ["tv", "desktop", "mobile"]
    # Secrets default to empty so the implicit `sw push all` path never needs .env keys.
    assert norm["playback"]["skip"]["animeSkip"]["clientId"] == ""
    assert norm["playback"]["skip"]["introDbApiKey"] == ""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_omitted_blocks_are_untouched_and_leaf_defaults_fill() -> None:
    norm = _normalized({"playback": {"reuseLastLink": {"enabled": "yes"}}})
    assert norm["playback"]["reuseLastLink"] == {"enabled": True, "cacheHours": 24}
    assert norm["playback"]["streamSelection"]["mode"] == "manual"
    for block in BLOCKS:
        if block != "playback":
            assert norm[block] is None
    assert active_blocks(norm) == frozenset({"playback"})
    assert build_platform_settings_features(norm, platform="tv").keys() == {
        "player_settings",
        "animeskip_settings",
    }


def test_normalize_rejects_bad_values_with_actionable_errors() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({"playback": {"reuseLastLink": {"cacheHours": 5}}}, "must be one of"),
        ({"playback": {"streamSelection": {"mode": "yolo"}}}, "Choose from: manual"),
        ({"subtitles": {"style": {"textColor": "red"}}}, "colour"),
        ({"audio": {"preferredLanguage": "english"}}, "ISO language code"),
        ({"playback": {"skip": {"autoSkip": ["credits"]}}}, "Choose from: intro"),
        ({"tvLayout": {"posters": {"widthDp": 10}}}, "between 60 and 400"),
        ({"appearance": "white"}, "must be an object or null"),
        ({"platforms": ["roku"]}, "Unsupported Nuvio platform"),
        ({"audio": {"preferredLanguage": None}}, "must not be null"),
    ]
    for raw, needle in cases:
        res = normalize_nuvio_settings_doc(raw)
        assert isinstance(res, Err), raw
        assert needle in res.error.message, (raw, res.error.message)


def test_normalize_canonicalizes_case_colours_and_languages() -> None:
    norm = _normalized(
        {
            "appearance": {"theme": "ROSE_GOLD"},
            "subtitles": {"style": {"textColor": "ffcc00"}, "preferredLanguage": "PT-br"},
            "audio": {"preferredLanguage": "Original", "secondaryLanguage": None},
            "playback": {"skip": {"autoSkip": ["recap", "intro", "intro"]}},
        }
    )
    assert norm["appearance"]["theme"] == "rose_gold"
    assert norm["subtitles"]["style"]["textColor"] == "#FFFFCC00"
    assert norm["subtitles"]["preferredLanguage"] == "pt-br"
    assert norm["audio"]["preferredLanguage"] == "original"
    assert norm["audio"]["secondaryLanguage"] is None
    assert norm["playback"]["skip"]["autoSkip"] == ["intro", "recap"]


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def test_platform_fragments_translate_keys_units_and_enums_per_platform() -> None:
    norm = _normalized(
        {
            "appearance": {"theme": "graphite", "amoledBlack": True},
            "playback": {
                "reuseLastLink": {"enabled": True, "cacheHours": 72},
                "nextEpisode": {"preferBingeGroup": False},
                "skip": {"autoSkip": ["intro", "movie-credits"], "animeSkip": {"enabled": True}},
                "ui": {"contentWarnings": False},
            },
            "subtitles": {"style": {"textColor": "#FFFFFFFF", "outline": {"color": "#80000000"}}},
            "audio": {"secondaryLanguage": None},
            "notifications": {"episodeReleaseAlerts": True},
            "tvPlugins": {"groupStreamsByRepository": True},
        }
    )
    mobile = build_platform_settings_features(norm, platform="mobile")
    desktop = build_platform_settings_features(norm, platform="desktop")
    tv = build_platform_settings_features(norm, platform="tv")

    # Reuse Last Link: identical wire keys everywhere.
    for feats in (mobile, desktop, tv):
        assert feats["player_settings"]["stream_reuse_last_link_enabled"] == {
            "type": "boolean",
            "value": True,
        }
        assert _pref(feats, "player_settings", "stream_reuse_last_link_cache_hours") == 72

    # Divergent wire keys for the same canonical setting.
    assert _pref(mobile, "player_settings", "stream_auto_play_prefer_binge_group") is False
    assert _pref(tv, "player_settings", "stream_auto_play_prefer_bingegroup_next_episode") is False
    assert _pref(mobile, "player_settings", "show_parental_guide") is False
    assert _pref(tv, "player_settings", "parental_guide_enabled") is False
    assert _pref(mobile, "theme_settings", "amoled_enabled") is True
    assert _pref(tv, "theme_settings", "amoled_mode") is True

    # Themes mobile cannot render are omitted (remote key preserved) but desktop/TV get them.
    assert "selected_theme" not in mobile["theme_settings"]
    assert _pref(desktop, "theme_settings", "selected_theme") == "GRAPHITE"
    assert _pref(tv, "theme_settings", "selected_theme") == "GRAPHITE"

    # Auto-skip: mobile has no auto-skip; desktop drops the TV-only `movie-credits`.
    assert "auto_skip_segment_types" not in mobile["player_settings"]
    assert _pref(desktop, "player_settings", "auto_skip_segment_types") == ["intro"]
    assert _pref(tv, "player_settings", "auto_skip_segment_types") == ["intro", "movie-credits"]

    # Colours: `#AARRGGBB` strings on KMP clients, signed ARGB ints on TV.
    assert mobile["player_settings"]["subtitle_text_color"] == {
        "type": "string",
        "value": "#FFFFFFFF",
    }
    assert tv["player_settings"]["subtitle_text_color"] == {"type": "int", "value": -1}
    assert _pref(tv, "player_settings", "subtitle_outline_color") == -2147483648

    # Nullable -> delete marker; typed notifications payload uses raw JSON booleans.
    assert mobile["player_settings"]["secondary_preferred_audio_language"] is None
    assert mobile["notifications_settings"] == {"episode_release_alerts_enabled": True}
    assert "notifications_settings" not in tv
    assert tv["animeskip_settings"] == {"animeskip_enabled": {"type": "boolean", "value": True}}
    assert _pref(tv, "plugin_settings", "group_streams_by_repository") is True
    assert "plugin_settings" not in desktop


def test_secrets_are_blob_embedded_on_mobile_only_and_become_credential_rows() -> None:
    norm = _normalized(
        {"playback": {"skip": {"animeSkip": {"clientId": "ask-123"}, "introDbApiKey": "idb-456"}}}
    )
    mobile = build_platform_settings_features(norm, platform="mobile")
    assert _pref(mobile, "player_settings", "animeskip_client_id") == "ask-123"
    assert _pref(mobile, "player_settings", "introdb_api_key") == "idb-456"
    for plat in ("desktop", "tv"):
        feats = build_platform_settings_features(norm, platform=plat)
        assert "animeskip_client_id" not in feats["player_settings"]
        assert "introdb_api_key" not in feats["player_settings"]
    assert build_settings_provider_credentials(norm) == [
        {"provider": "animeskip", "credential_json": {"client_id": "ask-123"}},
        {"provider": "introdb", "credential_json": {"api_key": "idb-456"}},
    ]
    # Empty secrets are never pushed (so devices keep whatever they already have).
    empty = _normalized({"playback": {}})
    assert (
        "animeskip_client_id"
        not in build_platform_settings_features(empty, platform="mobile")["player_settings"]
    )
    assert build_settings_provider_credentials(empty) == []


def test_merge_preserves_device_local_keys_deletes_nullable_and_refuses_to_seed_kmp() -> None:
    norm = _normalized({"playback": {"reuseLastLink": {"enabled": True}}, "audio": {}})
    frag = build_platform_settings_features(norm, platform="mobile")
    remote: dict[str, Any] = {
        "version": 3,
        "features": {
            "player_settings": {
                "android_playback_engine": {"type": "string", "value": "Libmpv"},
                "ios_gamma": {"type": "int", "value": 7},
                "secondary_preferred_audio_language": {"type": "string", "value": "hi"},
                "stream_reuse_last_link_enabled": {"type": "boolean", "value": False},
            },
            "theme_settings": {"selected_theme": {"type": "string", "value": "OCEAN"}},
            "debrid_settings": {"debrid_enabled": {"type": "boolean", "value": True}},
        },
    }
    merged = merge_profile_settings_blob_settings(remote, frag, platform="mobile")
    assert merged is not None
    ps = merged["features"]["player_settings"]
    assert ps["android_playback_engine"]["value"] == "Libmpv"
    assert ps["ios_gamma"]["value"] == 7
    assert ps["stream_reuse_last_link_enabled"]["value"] is True
    assert "secondary_preferred_audio_language" not in ps
    assert merged["features"]["theme_settings"]["selected_theme"]["value"] == "OCEAN"
    assert merged["features"]["debrid_settings"]["debrid_enabled"]["value"] is True
    assert remote["features"]["player_settings"]["stream_reuse_last_link_enabled"]["value"] is False

    assert merge_profile_settings_blob_settings(None, frag, platform="mobile") is None
    assert merge_profile_settings_blob_settings(None, frag, platform="desktop") is None
    seeded = merge_profile_settings_blob_settings(
        None, build_platform_settings_features(norm, platform="tv"), platform="tv"
    )
    assert seeded is not None
    assert seeded["version"] == 1
    assert isinstance(seeded["features"], dict)
    assert "secondary_preferred_audio_language" not in seeded["features"]["player_settings"]


# ---------------------------------------------------------------------------
# Decompilation / drift
# ---------------------------------------------------------------------------


def test_decompile_reports_consensus_and_per_platform_drift() -> None:
    norm = _normalized(
        {
            "playback": {"reuseLastLink": {"enabled": True, "cacheHours": 48}},
            "appearance": {"theme": "jade"},
        }
    )
    blobs: dict[str, dict[str, Any] | None] = {}
    for plat in VALID_NUVIO_SETTINGS_PLATFORMS:
        base: dict[str, Any] | None = None if plat == "tv" else {"version": 3, "features": {}}
        blobs[plat] = merge_profile_settings_blob_settings(
            base, build_platform_settings_features(norm, platform=plat), platform=plat
        )
    pulled = decompile_nuvio_settings(blobs, blocks=["playback", "appearance"])
    assert pulled["platforms"] == ["tv", "desktop", "mobile"]
    assert pulled["playback"] == norm["playback"]
    assert pulled["appearance"] == norm["appearance"]
    assert pulled["subtitles"] is None
    assert pulled["tvLayout"] is None
    assert compute_config_diff(norm, pulled, target="nuvio_settings").in_sync is True

    # Someone toggles Reuse Last Link off on the TV only -> TV drops out of `platforms`.
    tv_blob = blobs["tv"]
    assert tv_blob is not None
    tv_blob["features"]["player_settings"]["stream_reuse_last_link_enabled"]["value"] = False
    drifted = decompile_nuvio_settings(blobs, blocks=["playback", "appearance"])
    assert drifted["platforms"] == ["desktop", "mobile"]
    assert drifted["playback"]["reuseLastLink"]["enabled"] is True  # mobile wins consensus
    assert compute_config_diff(norm, drifted, target="nuvio_settings").in_sync is False

    # Mobile cannot represent `jade`; its view projects the theme away and still counts as synced.
    assert "theme" not in comparable_platform_view(norm, platform="mobile").get("appearance", {})
    assert comparable_platform_view(norm, platform="tv")["appearance"]["theme"] == "jade"


def test_decompile_prefers_credential_rows_and_handles_malformed_entries() -> None:
    blob = {
        "version": 3,
        "features": {
            "player_settings": {
                "stream_reuse_last_link_enabled": {"type": "string", "value": "true"},  # wrong type
                "stream_reuse_last_link_cache_hours": {"type": "int", "value": "12"},  # wrong type
                "subtitle_text_color": {"type": "string", "value": "not-a-colour"},
                "stream_auto_play_mode": {"type": "string", "value": "UNKNOWN_MODE"},
            }
        },
    }
    rows = [{"provider": "animeskip", "credential_json": {"client_id": "ask-from-rows"}}]
    pulled = decompile_nuvio_settings({"mobile": blob}, rows, blocks=["playback", "subtitles"])
    # Unusable entries fall back to Nuvio defaults instead of crashing or inventing values.
    assert pulled["playback"]["reuseLastLink"] == {"enabled": False, "cacheHours": 24}
    assert pulled["playback"]["streamSelection"]["mode"] == "manual"
    assert pulled["subtitles"]["style"]["textColor"] == "#FFFFFFFF"
    assert pulled["playback"]["skip"]["animeSkip"]["clientId"] == "ask-from-rows"
    assert pulled["platforms"] == ["mobile"]


# ---------------------------------------------------------------------------
# Property: compile -> merge -> decompile is lossless for any valid doc
# ---------------------------------------------------------------------------


def _value_strategy(setting: Setting) -> st.SearchStrategy[Any]:
    base: st.SearchStrategy[Any]
    match setting.kind:
        case "bool":
            base = st.booleans()
        case "int":
            if setting.allowed:
                base = st.sampled_from(setting.allowed)
            else:
                lo, hi = setting.bounds or (0, 100)
                base = st.integers(min_value=int(lo), max_value=int(hi))
        case "float":
            lo, hi = setting.bounds or (0.0, 100.0)
            base = st.floats(min_value=lo, max_value=hi, allow_nan=False).map(lambda f: round(f, 2))
        case "enum":
            base = st.sampled_from(setting.choices)
        case "language":
            base = st.sampled_from([*setting.tokens, "en", "hi", "ja", "pt-BR"])
        case "color":
            base = st.integers(min_value=0, max_value=0xFFFFFFFF).map(lambda n: f"#{n:08X}")
        case "list":
            pool = list(setting.choices) if setting.choices else ["a.b", "cat-1", "zz"]
            base = st.lists(st.sampled_from(pool), max_size=len(pool), unique=True)
        case _:  # string
            base = st.text(alphabet="abc-_", max_size=8)
    if setting.nullable:
        return st.one_of(st.none(), base)
    return base


@st.composite
def _settings_docs(draw: st.DrawFn) -> dict[str, Any]:
    chosen_blocks = draw(st.sets(st.sampled_from(BLOCKS), min_size=1))
    doc: dict[str, Any] = {"platforms": list(VALID_NUVIO_SETTINGS_PLATFORMS)}
    for block in BLOCKS:
        doc[block] = {} if block in chosen_blocks else None
    for setting in SETTINGS:
        if setting.block not in chosen_blocks or setting.secret:
            continue
        cur = doc
        for part in setting.parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[setting.parts[-1]] = draw(_value_strategy(setting))
    return doc


@settings(max_examples=40, deadline=None)
@given(_settings_docs())
def test_roundtrip_through_all_platform_blobs_is_lossless(raw: dict[str, Any]) -> None:
    norm = _normalized(raw)
    blobs: dict[str, dict[str, Any] | None] = {}
    for plat in VALID_NUVIO_SETTINGS_PLATFORMS:
        blobs[plat] = merge_profile_settings_blob_settings(
            {"version": 3, "features": {}},
            build_platform_settings_features(norm, platform=plat),
            platform=plat,
        )
    pulled = decompile_nuvio_settings(blobs, blocks=sorted(active_blocks(norm)))
    assert pulled["platforms"] == list(VALID_NUVIO_SETTINGS_PLATFORMS)
    for block in BLOCKS:
        assert pulled[block] == norm[block], block


def test_every_codec_projection_is_idempotent() -> None:
    """`project(project(v)) == project(v)` guarantees drift comparisons are stable."""
    for setting in SETTINGS:
        for binding in setting.bindings:
            value = setting.default
            if value is None:
                continue
            once = binding.codec.project(value)
            if once is OMIT:
                continue
            assert binding.codec.project(once) == once, (setting.path, binding.key)


# ---------------------------------------------------------------------------
# Secrets, coercions, and end-to-end client sync
# ---------------------------------------------------------------------------


def test_secret_lenses_scrub_and_reinject_skip_credentials() -> None:
    norm = _normalized(
        {
            "playback": {
                "skip": {"animeSkip": {"clientId": "ask-secret-1"}, "introDbApiKey": "idb-secret-2"}
            }
        }
    )
    scrubbed, env = harvest_and_scrub("aiomanager", norm, {})
    assert scrubbed["playback"]["skip"]["animeSkip"]["clientId"] == "{{SECRET_ANIMESKIP_CLIENT_ID}}"
    assert scrubbed["playback"]["skip"]["introDbApiKey"] == "{{SECRET_INTRODB_API_KEY}}"
    assert env == {
        "SECRET_ANIMESKIP_CLIENT_ID": "ask-secret-1",
        "SECRET_INTRODB_API_KEY": "idb-secret-2",
    }
    reinjected = inject_secrets(scrubbed, env)
    assert isinstance(reinjected, Ok)
    assert reinjected.value == norm
    # Empty credentials are left alone (no bogus placeholders).
    untouched, env2 = harvest_and_scrub("aiomanager", _normalized({"playback": {}}), {})
    assert untouched["playback"]["skip"]["introDbApiKey"] == ""
    assert env2 == {}


def test_aiomanager_coercions_propagate_and_strip_nuvio_settings() -> None:
    raw_mgr: dict[str, Any] = {
        "accounts": [{"id": "acc-1", "addons": []}],
        "nuvioSettings": {"playback": {"reuseLastLink": {"enabled": True}}},
    }
    rehydrated = rehydrate_aiomanager(raw_mgr)
    assert "nuvioSettings" not in rehydrated
    assert rehydrated["accounts"][0]["nuvioSettings"] == {
        "playback": {"reuseLastLink": {"enabled": True}}
    }
    dehydrated_res = dehydrate_aiomanager(rehydrated)
    assert isinstance(dehydrated_res, Ok)
    assert "nuvioSettings" not in dehydrated_res.value["accounts"][0]


def test_nuvio_client_sync_push_and_pull_settings_e2e() -> None:
    password = "master-sync-pass"
    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    enc_access = encrypt_field("nuvio-jwt-tok", password, salt_b64)
    conn = {
        "platform": "nuvio",
        "enabled": True,
        "credentials": {"accessToken": enc_access, "profileId": "p-uuid-1"},
    }
    env = {"NUVIO_SUPABASE_ANON_KEY": "anon-key-123"}
    raw_doc = json.loads(REPO_CONFIG.read_text(encoding="utf-8"))["settings"]
    raw_doc["playback"]["skip"]["animeSkip"]["clientId"] = "ask-e2e"

    remote_blobs: dict[str, dict[str, Any] | None] = {
        "tv": {
            "version": 1,
            "features": {
                "player_settings": {
                    "tunneling_enabled": {"type": "boolean", "value": True},
                    "stream_reuse_last_link_enabled": {"type": "boolean", "value": False},
                },
                "trakt_settings": {"watch_progress_source": {"type": "string", "value": "TRAKT"}},
                "track_preference": {"audio_lang|tt1": {"type": "string", "value": "ja"}},
            },
        },
        "desktop": {
            "version": 3,
            "features": {
                "player_settings": {
                    "nvidia_rtx_super_resolution_enabled": {"type": "boolean", "value": True}
                },
                "meta_screen_settings_payload": '{"background_mode":"cinematic"}',
            },
        },
        "mobile": None,  # never initialised -> must be skipped, not seeded
    }
    remote_creds: dict[str, dict[str, Any]] = {
        "mdblist": {"provider": "mdblist", "credential_json": {"api_key": "mdb-secret"}},
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
            if url.endswith("/sync_push_addons"):
                return Ok(HttpResponse(204, {}, ""))
            if url.endswith("/sync_pull_profile_settings_blob"):
                assert isinstance(json_body, Mapping)
                blob = remote_blobs[str(json_body["p_platform"])]
                rows = [{"profile_id": 2, "settings_json": blob}] if blob is not None else []
                return Ok(HttpResponse(200, rows, "[]"))
            if url.endswith("/sync_push_profile_settings_blob"):
                assert isinstance(json_body, Mapping)
                assert json_body["p_origin_client_id"]
                assert json_body["p_profile_id"] == 2
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
    push_res = syncer.push_settings(raw_doc, conn, password, salt_b64, env)
    assert isinstance(push_res, Ok), push_res
    msg, _ = push_res.value
    assert "reuse-last-link enabled" in msg
    assert "on tv, desktop)" in msg
    assert "1 provider credential(s)" in msg
    assert "profile #2" in msg

    assert remote_blobs["mobile"] is None
    tv_feats = (remote_blobs["tv"] or {})["features"]
    assert tv_feats["player_settings"]["tunneling_enabled"]["value"] is True  # device-local kept
    assert tv_feats["player_settings"]["stream_reuse_last_link_enabled"]["value"] is True
    assert tv_feats["player_settings"]["auto_skip_segment_types"]["value"] == ["intro", "recap"]
    assert tv_feats["trakt_settings"]["watch_progress_source"]["value"] == "TRAKT"  # nuvio_meta's
    assert tv_feats["track_preference"] == {"audio_lang|tt1": {"type": "string", "value": "ja"}}
    assert tv_feats["layout_settings"]["discover_location"]["value"] == "IN_SIDEBAR"
    assert "animeskip_client_id" not in tv_feats.get("animeskip_settings", {})
    desk_feats = (remote_blobs["desktop"] or {})["features"]
    assert desk_feats["player_settings"]["nvidia_rtx_super_resolution_enabled"]["value"] is True
    assert desk_feats["player_settings"]["stream_reuse_last_link_cache_hours"]["value"] == 24
    assert desk_feats["meta_screen_settings_payload"] == '{"background_mode":"cinematic"}'
    assert "layout_settings" not in desk_feats
    assert set(remote_creds) == {"mdblist", "animeskip"}
    assert remote_creds["animeskip"]["credential_json"] == {"client_id": "ask-e2e"}
    assert rpc_log.index("sync_seed_provider_credentials") < rpc_log.index(
        "sync_push_provider_credentials"
    )

    pull_res = syncer.pull_settings(conn, password, salt_b64, env, blocks=list(BLOCKS))
    assert isinstance(pull_res, Ok)
    pulled_doc, _ = pull_res.value
    expected = _normalized(raw_doc)
    expected["platforms"] = ["tv", "desktop"]  # mobile skipped above
    assert pulled_doc == expected
    assert compute_config_diff(expected, pulled_doc, target="nuvio_settings").in_sync is True

    # The account-level sync path (`sw push nuvio` / `aiomanager`) runs the same compiler.
    acc_res = syncer.sync_account(
        {"addons": [], "nuvioSettings": raw_doc}, conn, password, salt_b64, env
    )
    assert isinstance(acc_res, Ok)
    assert "app settings" in acc_res.value[0]
