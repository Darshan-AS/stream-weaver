"""
Pure functional compiler for Nuvio Meta / Details-page enrichment settings.

Declaratively drives, per Nuvio profile and across `tv` / `desktop` / `mobile`:

1. **MDBList Ratings** (`features.mdblist_settings`) — the multi-source ratings strip
   (IMDb, TMDB, Rotten Tomatoes critics + audience, Metacritic, Trakt, Letterboxd, MAL).
2. **TMDB Enrichment** (`features.tmdb_settings`) — trailers, artwork, credits, networks,
   production companies, collections, "More Like This", season posters, episodes.
3. **Trakt Comments** (`features.trakt_comments_settings` on desktop/mobile,
   `features.trakt_settings.show_meta_comments` on TV) — Trakt reviews on the details page.
3b. **Per-episode IMDb ratings** (`features.layout_settings.detail_imdb_ratings_visibility`, TV
   only — desktop/mobile always show them).
4. **Meta Screen layout** (`features.meta_screen_settings_payload`, desktop/mobile only) —
   section visibility/order, background mode, hero trailer playback, tab layout.

Secrets (MDBList / TMDB API keys) travel through Nuvio's dedicated
`sync_*_provider_credentials` RPCs (the only path honoured by NuvioTV and newer
desktop/mobile builds) and are *also* embedded in desktop/mobile blobs for older builds.

Wire formats were derived from the open-source Nuvio clients:
- `NuvioMobile` / `NuvioDesktop`: `core/sync/ProfileSettingsSync.kt`, `features/mdblist/*`,
  `features/tmdb/*`, `features/trakt/TraktCommentsStorage.*`, `features/details/MetaScreenSettingsRepository.kt`
- `NuvioTV`: `core/sync/ProfileSettingsSyncService.kt`, `core/sync/ProviderCredentialSyncService.kt`,
  `data/local/{MDBListSettingsDataStore,TmdbSettingsDataStore,TraktSettingsDataStore}.kt`
"""

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.nuvio_badges import (
    VALID_NUVIO_BADGE_PLATFORMS,
    _decode_sync_bool,
    _decode_sync_string,
    _encode_sync_bool,
    _encode_sync_string,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict

VALID_NUVIO_META_PLATFORMS: Final[tuple[str, ...]] = VALID_NUVIO_BADGE_PLATFORMS

MDBLIST_SETTINGS_FEATURE: Final = "mdblist_settings"
TMDB_SETTINGS_FEATURE: Final = "tmdb_settings"
TRAKT_SETTINGS_FEATURE: Final = "trakt_settings"
TRAKT_COMMENTS_SETTINGS_FEATURE: Final = "trakt_comments_settings"
META_SCREEN_PAYLOAD_FEATURE: Final = "meta_screen_settings_payload"

PROVIDER_CREDENTIAL_MDBLIST: Final = "mdblist"
PROVIDER_CREDENTIAL_TMDB: Final = "tmdb"
PROVIDER_CREDENTIAL_API_KEY_FIELD: Final = "api_key"

# Declarative provider id -> Nuvio preference suffix. Desktop/Mobile use `mdblist_use_<x>`,
# TV uses `mdblist_show_<x>`.
RATING_PROVIDERS: Final[tuple[str, ...]] = (
    "imdb",
    "tmdb",
    "tomatoes",
    "audience",
    "metacritic",
    "trakt",
    "letterboxd",
    "mal",
)

# Declarative module name -> Nuvio preference suffix (`tmdb_use_<suffix>`).
TMDB_MODULES: Final[tuple[tuple[str, str], ...]] = (
    ("trailers", "trailers"),
    ("artwork", "artwork"),
    ("basicInfo", "basic_info"),
    ("details", "details"),
    ("releaseDates", "release_dates"),
    ("credits", "credits"),
    ("productionCompanies", "productions"),
    ("networks", "networks"),
    ("episodes", "episodes"),
    ("seasonPosters", "season_posters"),
    ("moreLikeThis", "more_like_this"),
    ("collections", "collections"),
)
# NuvioTV's `TmdbSettingsDataStore` has no release-date or season-poster module.
TV_UNSUPPORTED_TMDB_MODULES: Final[frozenset[str]] = frozenset({"releaseDates", "seasonPosters"})

META_SCREEN_SECTION_KEYS: Final[tuple[str, ...]] = (
    "ACTIONS",
    "OVERVIEW",
    "PRODUCTION",
    "CAST",
    "COMMENTS",
    "TRAILERS",
    "EPISODES",
    "DETAILS",
    "COLLECTION",
    "MORE_LIKE_THIS",
)
META_SCREEN_BACKGROUND_MODES: Final[tuple[str, ...]] = ("normal", "cinematic", "dominant_color")
META_SCREEN_EPISODE_CARD_STYLES: Final[tuple[str, ...]] = ("horizontal", "list")

LAYOUT_SETTINGS_FEATURE: Final = "layout_settings"
# Declarative value -> NuvioTV `DetailImdbRatingsVisibility` enum name. Desktop/Mobile always
# show per-episode IMDb ratings (no preference exists), so this only compiles for TV.
EPISODE_RATINGS_MODES: Final[Mapping[str, str]] = {
    "show": "SHOW_ALL",
    "hideUnwatched": "HIDE_UNWATCHED_EPISODES",
    "hide": "HIDE_EPISODES",
}
_EPISODE_RATINGS_FROM_TV: Final[Mapping[str, str]] = {
    "SHOW_ALL": "show",
    "HIDE_UNWATCHED_EPISODES": "hideUnwatched",
    "HIDE_EPISODES": "hide",
    "HIDE_ALL": "hide",
}

_DEFAULT_LANGUAGE: Final = "en-US"


# ============================================================================
# Normalization (declarative doc -> canonical doc)
# ============================================================================


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("true", "1", "yes", "on"):
            return True
        if lower in ("false", "0", "no", "off"):
            return False
    return default


def _normalize_platforms(raw: Any, field_name: str) -> Result[list[str], ConfigValidationError]:
    if raw is None:
        return Ok(list(VALID_NUVIO_META_PLATFORMS))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return Err(
            ConfigValidationError(
                message=f"'platforms' in {field_name} must be a list of strings.",
                service="nuvio_meta",
            )
        )
    requested: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or item.strip().lower() not in VALID_NUVIO_META_PLATFORMS:
            return Err(
                ConfigValidationError(
                    message=(
                        f"Unsupported Nuvio platform '{item}'. "
                        f"Choose from: {', '.join(VALID_NUVIO_META_PLATFORMS)}"
                    ),
                    service="nuvio_meta",
                )
            )
        requested.add(item.strip().lower())
    return Ok([p for p in VALID_NUVIO_META_PLATFORMS if p in requested])


def _normalize_ratings(raw: Any) -> JsonDict:
    src: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    raw_providers = src.get("providers")
    providers_src: Mapping[str, Any] = raw_providers if isinstance(raw_providers, Mapping) else {}
    return {
        "enabled": _as_bool(src.get("enabled"), True),
        "apiKey": str(src.get("apiKey") or "").strip(),
        "providers": {pid: _as_bool(providers_src.get(pid), True) for pid in RATING_PROVIDERS},
    }


def _normalize_tmdb(raw: Any) -> JsonDict:
    src: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    raw_modules = src.get("modules")
    modules_src: Mapping[str, Any] = raw_modules if isinstance(raw_modules, Mapping) else {}
    return {
        "enabled": _as_bool(src.get("enabled"), True),
        "apiKey": str(src.get("apiKey") or "").strip(),
        "language": str(src.get("language") or _DEFAULT_LANGUAGE).strip() or _DEFAULT_LANGUAGE,
        "modules": {name: _as_bool(modules_src.get(name), True) for name, _ in TMDB_MODULES},
    }


def _normalize_meta_screen(raw: Any) -> Result[JsonDict | None, ConfigValidationError]:
    if raw is None or raw is False:
        return Ok(None)
    if not isinstance(raw, Mapping):
        return Err(
            ConfigValidationError(
                message="'metaScreen' in nuvio_meta.json must be an object or null.",
                service="nuvio_meta",
            )
        )

    bg_mode = str(raw.get("backgroundMode") or "normal").strip().lower()
    if bg_mode not in META_SCREEN_BACKGROUND_MODES:
        return Err(
            ConfigValidationError(
                message=(
                    f"Unsupported metaScreen.backgroundMode '{bg_mode}'. "
                    f"Choose from: {', '.join(META_SCREEN_BACKGROUND_MODES)}"
                ),
                service="nuvio_meta",
            )
        )
    card_style = str(raw.get("episodeCardStyle") or "horizontal").strip().lower()
    if card_style not in META_SCREEN_EPISODE_CARD_STYLES:
        return Err(
            ConfigValidationError(
                message=(
                    f"Unsupported metaScreen.episodeCardStyle '{card_style}'. "
                    f"Choose from: {', '.join(META_SCREEN_EPISODE_CARD_STYLES)}"
                ),
                service="nuvio_meta",
            )
        )

    raw_sections = raw.get("sections")
    declared: dict[str, Mapping[str, Any]] = {}
    declared_order: list[str] = []
    if isinstance(raw_sections, Sequence) and not isinstance(raw_sections, (str, bytes)):
        for item in raw_sections:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("key") or "").strip().upper()
            if key not in META_SCREEN_SECTION_KEYS:
                return Err(
                    ConfigValidationError(
                        message=(
                            f"Unknown metaScreen section '{key}'. "
                            f"Choose from: {', '.join(META_SCREEN_SECTION_KEYS)}"
                        ),
                        service="nuvio_meta",
                    )
                )
            if key in declared:
                continue
            declared[key] = item
            declared_order.append(key)

    # Declared sections keep their declared order; undeclared ones append in Nuvio's default order.
    ordered_keys = declared_order + [k for k in META_SCREEN_SECTION_KEYS if k not in declared]
    sections: list[JsonDict] = []
    for idx, key in enumerate(ordered_keys):
        item = declared.get(key, {})
        raw_group = item.get("tabGroup")
        tab_group = (
            int(raw_group)
            if isinstance(raw_group, (int, float)) and not isinstance(raw_group, bool)
            else None
        )
        entry: JsonDict = {"key": key, "enabled": _as_bool(item.get("enabled"), True), "order": idx}
        if tab_group is not None:
            entry["tabGroup"] = tab_group
        sections.append(entry)

    return Ok(
        {
            "backgroundMode": bg_mode,
            "heroTrailerPlayback": _as_bool(raw.get("heroTrailerPlayback"), False),
            "tabLayout": _as_bool(raw.get("tabLayout"), False),
            "episodeCardStyle": card_style,
            "blurUnwatchedEpisodes": _as_bool(raw.get("blurUnwatchedEpisodes"), False),
            "sections": sections,
        }
    )


def normalize_nuvio_meta_doc(data: Any) -> Result[JsonDict, ConfigValidationError]:
    """
    Validates and canonicalizes `configs/nuvio_meta.json`. The output shape is the single
    source of truth for compilation, decompilation, and `sw diff` comparisons.
    """
    if not isinstance(data, Mapping):
        return Err(
            ConfigValidationError(
                message="nuvio_meta.json must be a JSON object.",
                service="nuvio_meta",
            )
        )

    match _normalize_platforms(data.get("platforms"), "nuvio_meta.json"):
        case Err() as err:
            return err
        case Ok(platforms):
            pass

    match _normalize_meta_screen(data.get("metaScreen")):
        case Err() as err:
            return err
        case Ok(meta_screen):
            pass

    episode_ratings = str(data.get("episodeRatings") or "show").strip()
    if episode_ratings not in EPISODE_RATINGS_MODES:
        return Err(
            ConfigValidationError(
                message=(
                    f"Unsupported episodeRatings '{episode_ratings}'. "
                    f"Choose from: {', '.join(EPISODE_RATINGS_MODES)}"
                ),
                service="nuvio_meta",
            )
        )

    return Ok(
        {
            "version": str(data.get("version") or "1.0.0").strip() or "1.0.0",
            "platforms": platforms,
            "ratings": _normalize_ratings(data.get("ratings")),
            "tmdbEnrichment": _normalize_tmdb(data.get("tmdbEnrichment")),
            "traktComments": _as_bool(data.get("traktComments"), True),
            "episodeRatings": episode_ratings,
            "metaScreen": meta_screen,
        }
    )


# ============================================================================
# Compilation (canonical doc -> per-platform Nuvio feature payloads)
# ============================================================================


def compile_meta_screen_payload(meta_screen: Mapping[str, Any]) -> str:
    """Serializes the Meta Screen settings to Nuvio's compact `StoredMetaScreenSettingsPayload` JSON string."""
    items: list[JsonDict] = []
    for section in meta_screen.get("sections", []):
        if not isinstance(section, Mapping):
            continue
        item: JsonDict = {
            "key": str(section["key"]),
            "enabled": bool(section.get("enabled", True)),
            "order": int(section.get("order", 0)),
            "tabGroup": section.get("tabGroup"),
        }
        items.append(item)
    bg_mode = str(meta_screen.get("backgroundMode") or "normal")
    payload: JsonDict = {
        "items": items,
        "background_mode": bg_mode,
        "cinematicBackground": bg_mode == "cinematic",
        "hero_trailer_playback": bool(meta_screen.get("heroTrailerPlayback", False)),
        "tvStyleLayout": bool(meta_screen.get("tabLayout", False)),
        "episodeCardStyle": str(meta_screen.get("episodeCardStyle") or "horizontal"),
        "blur_unwatched_episodes": bool(meta_screen.get("blurUnwatchedEpisodes", False)),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decompile_meta_screen_payload(raw_payload: str | None) -> JsonDict | None:
    """Parses Nuvio's `meta_screen_settings_payload` string back into the declarative `metaScreen` object."""
    if not raw_payload or not raw_payload.strip():
        return None
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None

    bg_mode = parsed.get("background_mode")
    if not isinstance(bg_mode, str) or bg_mode not in META_SCREEN_BACKGROUND_MODES:
        bg_mode = "cinematic" if bool(parsed.get("cinematicBackground", False)) else "normal"

    raw_items = parsed.get("items")
    stored: list[tuple[int, JsonDict]] = []
    if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)):
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("key") or "").upper()
            if key not in META_SCREEN_SECTION_KEYS:
                continue
            raw_order = item.get("order", 0)
            order = (
                int(raw_order)
                if isinstance(raw_order, (int, float)) and not isinstance(raw_order, bool)
                else 0
            )
            entry: JsonDict = {"key": key, "enabled": bool(item.get("enabled", True))}
            raw_group = item.get("tabGroup")
            if isinstance(raw_group, (int, float)) and not isinstance(raw_group, bool):
                entry["tabGroup"] = int(raw_group)
            stored.append((order, entry))
    stored.sort(key=lambda pair: pair[0])

    declarative: JsonDict = {
        "backgroundMode": bg_mode,
        "heroTrailerPlayback": bool(parsed.get("hero_trailer_playback", False)),
        "tabLayout": bool(parsed.get("tvStyleLayout", False)),
        "episodeCardStyle": str(parsed.get("episodeCardStyle") or "horizontal"),
        "blurUnwatchedEpisodes": bool(parsed.get("blur_unwatched_episodes", False)),
        "sections": [entry for _, entry in stored],
    }
    match _normalize_meta_screen(declarative):
        case Ok(norm):
            return norm
        case Err():
            return None


def build_platform_meta_features(
    meta_doc: Mapping[str, Any],
    *,
    platform: str,
) -> dict[str, Any]:
    """
    Builds the per-platform `features.*` fragments for a normalized meta doc.

    Returns a mapping of feature name -> encoded preference object (or raw string for
    `meta_screen_settings_payload`). Only keys the target platform understands are emitted so
    read-modify-write merges never clobber unrelated preferences.
    """
    ratings = meta_doc.get("ratings", {})
    tmdb = meta_doc.get("tmdbEnrichment", {})
    providers: Mapping[str, Any] = ratings.get("providers", {})
    modules: Mapping[str, Any] = tmdb.get("modules", {})
    trakt_comments = bool(meta_doc.get("traktComments", True))

    out: dict[str, Any] = {}

    if platform == "tv":
        mdb: JsonDict = {"mdblist_enabled": _encode_sync_bool(bool(ratings.get("enabled", True)))}
        for pid in RATING_PROVIDERS:
            mdb[f"mdblist_show_{pid}"] = _encode_sync_bool(bool(providers.get(pid, True)))
        out[MDBLIST_SETTINGS_FEATURE] = mdb

        tm: JsonDict = {
            "tmdb_enabled": _encode_sync_bool(bool(tmdb.get("enabled", True))),
            "tmdb_language": _encode_sync_string(str(tmdb.get("language") or _DEFAULT_LANGUAGE)),
        }
        for name, suffix in TMDB_MODULES:
            if name in TV_UNSUPPORTED_TMDB_MODULES:
                continue
            tm[f"tmdb_use_{suffix}"] = _encode_sync_bool(bool(modules.get(name, True)))
        out[TMDB_SETTINGS_FEATURE] = tm

        out[TRAKT_SETTINGS_FEATURE] = {"show_meta_comments": _encode_sync_bool(trakt_comments)}
        tv_mode = EPISODE_RATINGS_MODES[str(meta_doc.get("episodeRatings") or "show")]
        out[LAYOUT_SETTINGS_FEATURE] = {
            "detail_imdb_ratings_visibility": _encode_sync_string(tv_mode),
        }
        return out

    # desktop / mobile (shared KMP codebase)
    mdb = {"mdblist_enabled": _encode_sync_bool(bool(ratings.get("enabled", True)))}
    api_key = str(ratings.get("apiKey") or "")
    if api_key:
        mdb["mdblist_api_key"] = _encode_sync_string(api_key)
    for pid in RATING_PROVIDERS:
        mdb[f"mdblist_use_{pid}"] = _encode_sync_bool(bool(providers.get(pid, True)))
    out[MDBLIST_SETTINGS_FEATURE] = mdb

    tm = {
        "tmdb_enabled": _encode_sync_bool(bool(tmdb.get("enabled", True))),
        "tmdb_language": _encode_sync_string(str(tmdb.get("language") or _DEFAULT_LANGUAGE)),
    }
    tmdb_key = str(tmdb.get("apiKey") or "")
    if tmdb_key:
        tm["tmdb_api_key"] = _encode_sync_string(tmdb_key)
    for name, suffix in TMDB_MODULES:
        tm[f"tmdb_use_{suffix}"] = _encode_sync_bool(bool(modules.get(name, True)))
    out[TMDB_SETTINGS_FEATURE] = tm

    # Android/iOS builds persist `comments_enabled`, desktop persists `trakt_comments_enabled`.
    out[TRAKT_COMMENTS_SETTINGS_FEATURE] = {
        "comments_enabled": _encode_sync_bool(trakt_comments),
        "trakt_comments_enabled": _encode_sync_bool(trakt_comments),
    }

    meta_screen = meta_doc.get("metaScreen")
    if isinstance(meta_screen, Mapping):
        out[META_SCREEN_PAYLOAD_FEATURE] = compile_meta_screen_payload(meta_screen)

    return out


def merge_profile_settings_blob_meta(
    remote_settings_json: Mapping[str, Any] | None,
    platform_features: Mapping[str, Any],
    *,
    platform: str,
) -> JsonDict | None:
    """
    Pure read-modify-write overlay of the meta feature fragments into a Nuvio profile settings
    blob. Existing keys inside each feature (e.g. TV `trakt_settings` watch-progress prefs,
    `tmdb_modern_home_enabled`) are preserved; only the declared keys are overwritten.

    Returns `None` for uninitialized `desktop`/`mobile` blobs: those clients apply a remote blob
    wholesale, so seeding a partial blob would reset their local theme/player settings.
    """
    if remote_settings_json is None:
        if platform != "tv":
            return None
        return {
            "version": 1,
            "features": {k: copy.deepcopy(v) for k, v in platform_features.items()},
        }

    merged: JsonDict = copy.deepcopy(dict(remote_settings_json))
    raw_features = merged.get("features")
    features: JsonDict = (
        copy.deepcopy(dict(raw_features)) if isinstance(raw_features, Mapping) else {}
    )

    for feature_name, fragment in platform_features.items():
        if isinstance(fragment, Mapping):
            existing = features.get(feature_name)
            base: JsonDict = copy.deepcopy(dict(existing)) if isinstance(existing, Mapping) else {}
            base.update(copy.deepcopy(dict(fragment)))
            features[feature_name] = base
        else:
            features[feature_name] = fragment

    merged["features"] = features
    merged.setdefault("version", 1 if platform == "tv" else 4)
    return merged


def build_provider_credentials(meta_doc: Mapping[str, Any]) -> list[JsonDict]:
    """
    Builds the `p_credentials` rows for Nuvio's `sync_*_provider_credentials` RPCs from the
    declared API keys. Empty keys are omitted so remote credentials are never blanked out.
    """
    rows: list[JsonDict] = []
    mdb_key = str(meta_doc.get("ratings", {}).get("apiKey") or "").strip()
    if mdb_key:
        rows.append(
            {
                "provider": PROVIDER_CREDENTIAL_MDBLIST,
                "credential_json": {PROVIDER_CREDENTIAL_API_KEY_FIELD: mdb_key},
            }
        )
    tmdb_key = str(meta_doc.get("tmdbEnrichment", {}).get("apiKey") or "").strip()
    if tmdb_key:
        rows.append(
            {
                "provider": PROVIDER_CREDENTIAL_TMDB,
                "credential_json": {PROVIDER_CREDENTIAL_API_KEY_FIELD: tmdb_key},
            }
        )
    return rows


def merge_provider_credential_rows(
    remote_rows: Sequence[Mapping[str, Any]],
    desired_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[JsonDict], list[JsonDict]]:
    """
    Overlays desired credential rows onto the remote set (keyed by provider).

    Returns `(merged_rows, rows_to_seed)` where `rows_to_seed` are desired providers absent
    remotely (Nuvio seeds new providers via `sync_seed_provider_credentials` before pushing).
    """
    merged: dict[str, JsonDict] = {}
    for row in remote_rows:
        provider = str(row.get("provider") or "").strip().lower()
        if not provider:
            continue
        cred = row.get("credential_json")
        merged[provider] = {
            "provider": provider,
            "credential_json": copy.deepcopy(dict(cred)) if isinstance(cred, Mapping) else {},
        }

    to_seed: list[JsonDict] = []
    for row in desired_rows:
        provider = str(row.get("provider") or "").strip().lower()
        if not provider:
            continue
        cred = row.get("credential_json")
        desired: JsonDict = {
            "provider": provider,
            "credential_json": copy.deepcopy(dict(cred)) if isinstance(cred, Mapping) else {},
        }
        if provider not in merged:
            to_seed.append(copy.deepcopy(desired))
        merged[provider] = desired

    return [merged[p] for p in sorted(merged)], to_seed


# ============================================================================
# Decompilation (remote Nuvio state -> canonical doc)
# ============================================================================


def _decode_platform_view(
    blob: Mapping[str, Any] | None,
    *,
    platform: str,
) -> JsonDict | None:
    """Decodes the meta-related subset of one platform blob, or `None` if it carries none."""
    if not isinstance(blob, Mapping):
        return None
    feats = blob.get("features")
    if not isinstance(feats, Mapping):
        return None

    mdb = feats.get(MDBLIST_SETTINGS_FEATURE)
    tm = feats.get(TMDB_SETTINGS_FEATURE)
    mdb_feat: Mapping[str, Any] = mdb if isinstance(mdb, Mapping) else {}
    tm_feat: Mapping[str, Any] = tm if isinstance(tm, Mapping) else {}

    provider_prefix = "mdblist_show_" if platform == "tv" else "mdblist_use_"
    ratings_enabled = _decode_sync_bool(mdb_feat, "mdblist_enabled")
    tmdb_enabled = _decode_sync_bool(tm_feat, "tmdb_enabled")

    if platform == "tv":
        trakt = feats.get(TRAKT_SETTINGS_FEATURE)
        trakt_feat: Mapping[str, Any] = trakt if isinstance(trakt, Mapping) else {}
        comments = _decode_sync_bool(trakt_feat, "show_meta_comments")
        layout = feats.get(LAYOUT_SETTINGS_FEATURE)
        layout_feat: Mapping[str, Any] = layout if isinstance(layout, Mapping) else {}
        raw_vis = _decode_sync_string(layout_feat, "detail_imdb_ratings_visibility")
        episode_ratings: str | None = _EPISODE_RATINGS_FROM_TV.get(raw_vis or "")
        meta_screen: JsonDict | None = None
        mdb_key: str | None = None
        tmdb_key: str | None = None
    else:
        tc = feats.get(TRAKT_COMMENTS_SETTINGS_FEATURE)
        tc_feat: Mapping[str, Any] = tc if isinstance(tc, Mapping) else {}
        comments = _decode_sync_bool(tc_feat, "comments_enabled")
        if comments is None:
            comments = _decode_sync_bool(tc_feat, "trakt_comments_enabled")
        raw_payload = feats.get(META_SCREEN_PAYLOAD_FEATURE)
        meta_screen = decompile_meta_screen_payload(
            raw_payload if isinstance(raw_payload, str) else None
        )
        mdb_key = _decode_sync_string(mdb_feat, "mdblist_api_key")
        tmdb_key = _decode_sync_string(tm_feat, "tmdb_api_key")
        episode_ratings = None

    if (
        ratings_enabled is None
        and tmdb_enabled is None
        and comments is None
        and meta_screen is None
    ):
        return None

    providers: JsonDict = {}
    for pid in RATING_PROVIDERS:
        val = _decode_sync_bool(mdb_feat, f"{provider_prefix}{pid}")
        providers[pid] = True if val is None else val

    modules: JsonDict = {}
    for name, suffix in TMDB_MODULES:
        if platform == "tv" and name in TV_UNSUPPORTED_TMDB_MODULES:
            continue
        val = _decode_sync_bool(tm_feat, f"tmdb_use_{suffix}")
        modules[name] = True if val is None else val

    view: JsonDict = {
        "ratings": {
            "enabled": True if ratings_enabled is None else ratings_enabled,
            "apiKey": mdb_key or "",
            "providers": providers,
        },
        "tmdbEnrichment": {
            "enabled": True if tmdb_enabled is None else tmdb_enabled,
            "apiKey": tmdb_key or "",
            "language": _decode_sync_string(tm_feat, "tmdb_language") or _DEFAULT_LANGUAGE,
            "modules": modules,
        },
        "traktComments": True if comments is None else comments,
        "episodeRatings": episode_ratings or "show",
        "metaScreen": meta_screen,
    }
    return view


def _comparable_platform_view(doc: Mapping[str, Any], *, platform: str) -> JsonDict:
    """Projects a canonical doc onto the fields a platform can actually represent (keys excluded)."""
    ratings = doc.get("ratings", {})
    tmdb = doc.get("tmdbEnrichment", {})
    modules = {
        name: bool(tmdb.get("modules", {}).get(name, True))
        for name, _ in TMDB_MODULES
        if not (platform == "tv" and name in TV_UNSUPPORTED_TMDB_MODULES)
    }
    view: JsonDict = {
        "ratings": {
            "enabled": bool(ratings.get("enabled", True)),
            "providers": {
                pid: bool(ratings.get("providers", {}).get(pid, True)) for pid in RATING_PROVIDERS
            },
        },
        "tmdbEnrichment": {
            "enabled": bool(tmdb.get("enabled", True)),
            "language": str(tmdb.get("language") or _DEFAULT_LANGUAGE),
            "modules": modules,
        },
        "traktComments": bool(doc.get("traktComments", True)),
    }
    if platform == "tv":
        view["episodeRatings"] = str(doc.get("episodeRatings") or "show")
    else:
        view["metaScreen"] = copy.deepcopy(doc.get("metaScreen"))
    return view


def decompile_nuvio_meta(
    platform_blobs: Mapping[str, Mapping[str, Any] | None],
    credential_rows: Sequence[Mapping[str, Any]] = (),
    *,
    version: str = "1.0.0",
) -> JsonDict:
    """
    Reverse-compiles live Nuvio profile settings blobs (+ provider credential rows) into the
    canonical `configs/nuvio_meta.json` shape. `platforms` lists only the platforms whose
    remote state matches the consensus doc (mobile > desktop > tv priority), so `sw diff`
    surfaces per-platform drift.
    """
    views: dict[str, JsonDict | None] = {
        plat: _decode_platform_view(platform_blobs.get(plat), platform=plat)
        for plat in VALID_NUVIO_META_PLATFORMS
    }
    consensus: JsonDict | None = None
    for plat in ("mobile", "desktop", "tv"):
        if views.get(plat) is not None:
            consensus = copy.deepcopy(views[plat])
            break

    # Credential rows are authoritative for API keys; blobs are the legacy fallback.
    mdb_key = ""
    tmdb_key = ""
    for row in credential_rows:
        provider = str(row.get("provider") or "").strip().lower()
        cred = row.get("credential_json")
        key = (
            str(cred.get(PROVIDER_CREDENTIAL_API_KEY_FIELD) or "").strip()
            if isinstance(cred, Mapping)
            else ""
        )
        if not key:
            continue
        if provider == PROVIDER_CREDENTIAL_MDBLIST:
            mdb_key = key
        elif provider == PROVIDER_CREDENTIAL_TMDB:
            tmdb_key = key
    for plat in ("mobile", "desktop"):
        view = views.get(plat)
        if view is None:
            continue
        mdb_key = mdb_key or str(view["ratings"].get("apiKey") or "")
        tmdb_key = tmdb_key or str(view["tmdbEnrichment"].get("apiKey") or "")

    if consensus is None:
        base_res = normalize_nuvio_meta_doc(
            {"version": version, "platforms": [], "metaScreen": None}
        )
        base = base_res.value if isinstance(base_res, Ok) else {}
        base["ratings"]["apiKey"] = mdb_key
        base["tmdbEnrichment"]["apiKey"] = tmdb_key
        return base

    # TV-only consensus lacks release-date/season-poster modules: default them on.
    for name, _ in TMDB_MODULES:
        consensus["tmdbEnrichment"]["modules"].setdefault(name, True)
    consensus["ratings"]["apiKey"] = mdb_key
    consensus["tmdbEnrichment"]["apiKey"] = tmdb_key

    synced_platforms = [
        plat
        for plat in VALID_NUVIO_META_PLATFORMS
        if views.get(plat) is not None
        and _comparable_platform_view(views[plat] or {}, platform=plat)
        == _comparable_platform_view(consensus, platform=plat)
    ]

    doc: JsonDict = {
        "version": version,
        "platforms": synced_platforms,
        "ratings": consensus["ratings"],
        "tmdbEnrichment": consensus["tmdbEnrichment"],
        "traktComments": consensus["traktComments"],
        "episodeRatings": (views.get("tv") or {}).get("episodeRatings") or "show",
        "metaScreen": consensus.get("metaScreen"),
    }
    match normalize_nuvio_meta_doc(doc):
        case Ok(norm):
            return norm
        case Err():
            return doc
