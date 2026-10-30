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
3d. **Tracking source pins** (`trakt_settings_payload` on desktop/mobile, `trakt_settings.*` on TV)
   — which connected tracker drives resume/Up Next (`watchProgressSource`), the Library
   (`librarySource`), More Like This, the Simkl anime id scheme, and the Continue Watching window.
   Trakt/Simkl OAuth itself is device-local in Nuvio and cannot be synced.
3c. **TV detail-page trailer autoplay** (`features.trailer_settings`, TV only) — mode
   (`background` keeps poster/logo/info visible and swaps only the backdrop, `fullscreen`, `off`),
   start delay, pause-on-scroll. Set `tvTrailer: null` to leave the TV untouched.
4. **Meta Screen layout** (`features.meta_screen_settings_payload`, desktop/mobile only) —
   section visibility/order, background mode, hero trailer playback, tab layout.
5. **Poster Card Style, Card Depth & Continue Watching**
   (`poster_card_style_settings_payload`, `card_depth_style_settings_payload`,
   `continue_watching_settings_payload`, desktop/mobile only) — shared card dimensions/radius,
   3D edge/sheen strengths, and Continue Watching row presentation, while preserving Desktop-only
   `hoverPreview*` fields and device-local `dismissedNextUpKeys`.

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
TRAKT_SETTINGS_PAYLOAD_FEATURE: Final = "trakt_settings_payload"
POSTER_CARD_STYLE_PAYLOAD_FEATURE: Final = "poster_card_style_settings_payload"
CARD_DEPTH_STYLE_PAYLOAD_FEATURE: Final = "card_depth_style_settings_payload"
CONTINUE_WATCHING_PAYLOAD_FEATURE: Final = "continue_watching_settings_payload"
_JSON_OBJECT_OVERLAY_FEATURES: Final[frozenset[str]] = frozenset(
    {POSTER_CARD_STYLE_PAYLOAD_FEATURE}
)
_CONTINUE_WATCHING_PRESERVED_KEYS: Final[tuple[str, ...]] = ("dismissedNextUpKeys",)

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

TRAILER_SETTINGS_FEATURE: Final = "trailer_settings"
# NuvioTV detail-page trailer autoplay. `trailer_allow_4k` is deliberately NOT here: NuvioTV keeps
# it device-local (RAM dependent) and strips it from the synced blob.
TV_TRAILER_MODES: Final[tuple[str, ...]] = ("background", "fullscreen", "off")
_DEFAULT_TV_TRAILER_DELAY: Final = 7

# Tracking source pins. Declarative value -> Nuvio enum name. Desktop/Mobile persist these in the
# `trakt_settings_payload` JSON string; TV persists them as `trakt_settings` preferences.
TRACKING_PROGRESS_SOURCES: Final[Mapping[str, str]] = {
    "trakt": "TRAKT",
    "simkl": "SIMKL",
    "nuvio": "NUVIO_SYNC",
}
TRACKING_LIBRARY_SOURCES: Final[Mapping[str, str]] = {
    "trakt": "TRAKT",
    "simkl": "SIMKL",
    "local": "LOCAL",
}
TRACKING_MORE_LIKE_THIS_SOURCES: Final[Mapping[str, str]] = {"trakt": "TRAKT", "tmdb": "TMDB"}
TRACKING_SIMKL_ANIME_IDS: Final[Mapping[str, str]] = {
    "imdb": "IMDB",
    "mal": "MAL",
    "kitsu": "KITSU",
}
_DEFAULT_CONTINUE_WATCHING_DAYS: Final = 60

CONTINUE_WATCHING_STYLES: Final[Mapping[str, str]] = {
    "card": "Card",
    "poster": "Poster",
}
CONTINUE_WATCHING_SORT_MODES: Final[Mapping[str, str]] = {
    "streaming": "STREAMING_STYLE",
    "default": "DEFAULT",
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


def _normalize_tv_trailer(raw: Any) -> Result[JsonDict | None, ConfigValidationError]:
    if raw is None or raw is False:
        return Ok(None)
    if not isinstance(raw, Mapping):
        return Err(
            ConfigValidationError(
                message="'tvTrailer' in nuvio_meta.json must be an object or null.",
                service="nuvio_meta",
            )
        )
    mode = str(raw.get("mode") or "fullscreen").strip().lower()
    if mode not in TV_TRAILER_MODES:
        return Err(
            ConfigValidationError(
                message=(
                    f"Unsupported tvTrailer.mode '{mode}'. Choose from: {', '.join(TV_TRAILER_MODES)}"
                ),
                service="nuvio_meta",
            )
        )
    raw_delay = raw.get("delaySeconds", _DEFAULT_TV_TRAILER_DELAY)
    if isinstance(raw_delay, bool) or not isinstance(raw_delay, (int, float)) or raw_delay < 0:
        return Err(
            ConfigValidationError(
                message="tvTrailer.delaySeconds must be a non-negative integer.",
                service="nuvio_meta",
            )
        )
    return Ok(
        {
            "mode": mode,
            "delaySeconds": int(raw_delay),
            "pauseOnScroll": _as_bool(raw.get("pauseOnScroll"), True),
        }
    )


def _normalize_tracking(raw: Any) -> Result[JsonDict | None, ConfigValidationError]:
    if raw is None or raw is False:
        return Ok(None)
    if not isinstance(raw, Mapping):
        return Err(
            ConfigValidationError(
                message="'tracking' in nuvio_meta.json must be an object or null.",
                service="nuvio_meta",
            )
        )
    out: JsonDict = {}
    for field, table, default in (
        ("watchProgressSource", TRACKING_PROGRESS_SOURCES, "trakt"),
        ("librarySource", TRACKING_LIBRARY_SOURCES, "trakt"),
        ("moreLikeThisSource", TRACKING_MORE_LIKE_THIS_SOURCES, "trakt"),
        ("simklAnimeId", TRACKING_SIMKL_ANIME_IDS, "imdb"),
    ):
        value = str(raw.get(field) or default).strip().lower()
        if value not in table:
            return Err(
                ConfigValidationError(
                    message=f"Unsupported tracking.{field} '{value}'. Choose from: {', '.join(table)}",
                    service="nuvio_meta",
                )
            )
        out[field] = value
    raw_days = raw.get("continueWatchingDays", _DEFAULT_CONTINUE_WATCHING_DAYS)
    if isinstance(raw_days, bool) or not isinstance(raw_days, (int, float)) or raw_days < 0:
        return Err(
            ConfigValidationError(
                message="tracking.continueWatchingDays must be a non-negative integer (0 = all).",
                service="nuvio_meta",
            )
        )
    out["continueWatchingDays"] = int(raw_days)
    return Ok(out)


def _require_bounded_int(
    raw: Any,
    *,
    default: int,
    lo: int,
    hi: int,
    label: str,
) -> Result[int, ConfigValidationError]:
    val = default if raw is None else raw
    if isinstance(val, bool) or not isinstance(val, (int, float)) or int(val) < lo or int(val) > hi:
        return Err(
            ConfigValidationError(
                message=f"{label} must be an integer between {lo} and {hi}.",
                service="nuvio_meta",
            )
        )
    return Ok(int(val))


def _normalize_poster_card_style(raw: Any) -> Result[JsonDict | None, ConfigValidationError]:
    if raw is None or raw is False:
        return Ok(None)
    if not isinstance(raw, Mapping):
        return Err(
            ConfigValidationError(
                message="'posterCardStyle' in nuvio_meta.json must be an object or null.",
                service="nuvio_meta",
            )
        )
    match _require_bounded_int(
        raw.get("widthDp"), default=126, lo=60, hi=400, label="posterCardStyle.widthDp"
    ):
        case Err() as err:
            return err
        case Ok(width_dp):
            pass
    match _require_bounded_int(
        raw.get("heightDp"),
        default=(width_dp * 3) // 2,
        lo=90,
        hi=600,
        label="posterCardStyle.heightDp",
    ):
        case Err() as err:
            return err
        case Ok(height_dp):
            pass
    match _require_bounded_int(
        raw.get("cornerRadiusDp"),
        default=12,
        lo=0,
        hi=48,
        label="posterCardStyle.cornerRadiusDp",
    ):
        case Err() as err:
            return err
        case Ok(radius_dp):
            pass
    return Ok(
        {
            "widthDp": width_dp,
            "heightDp": height_dp,
            "cornerRadiusDp": radius_dp,
            "landscapeMode": _as_bool(raw.get("landscapeMode"), False),
            "hideLabels": _as_bool(raw.get("hideLabels"), False),
        }
    )


def _normalize_card_depth(raw: Any) -> Result[JsonDict | None, ConfigValidationError]:
    if raw is None or raw is False:
        return Ok(None)
    if not isinstance(raw, Mapping):
        return Err(
            ConfigValidationError(
                message="'cardDepth' in nuvio_meta.json must be an object or null.",
                service="nuvio_meta",
            )
        )
    strengths: dict[str, int] = {}
    for key, default in (("edgeStrength", 28), ("sheenStrength", 10), ("edgeCoverage", 0)):
        match _require_bounded_int(
            raw.get(key), default=default, lo=0, hi=100, label=f"cardDepth.{key}"
        ):
            case Err() as err:
                return err
            case Ok(val):
                strengths[key] = val
    return Ok(
        {
            "enabled": _as_bool(raw.get("enabled"), True),
            **strengths,
            "posters": _as_bool(raw.get("posters"), True),
            "continueWatching": _as_bool(raw.get("continueWatching"), True),
            "episodeCards": _as_bool(raw.get("episodeCards"), True),
            "cast": _as_bool(raw.get("cast"), True),
            "trailers": _as_bool(raw.get("trailers"), True),
        }
    )


def _normalize_continue_watching(raw: Any) -> Result[JsonDict | None, ConfigValidationError]:
    if raw is None or raw is False:
        return Ok(None)
    if not isinstance(raw, Mapping):
        return Err(
            ConfigValidationError(
                message="'continueWatching' in nuvio_meta.json must be an object or null.",
                service="nuvio_meta",
            )
        )
    style = str(raw.get("style") or "card").strip().lower()
    if style not in CONTINUE_WATCHING_STYLES:
        return Err(
            ConfigValidationError(
                message=(
                    f"Unsupported continueWatching.style '{style}'. "
                    f"Choose from: {', '.join(CONTINUE_WATCHING_STYLES)}"
                ),
                service="nuvio_meta",
            )
        )
    sort_mode = str(raw.get("sortMode") or "streaming").strip().lower()
    if sort_mode not in CONTINUE_WATCHING_SORT_MODES:
        return Err(
            ConfigValidationError(
                message=(
                    f"Unsupported continueWatching.sortMode '{sort_mode}'. "
                    f"Choose from: {', '.join(CONTINUE_WATCHING_SORT_MODES)}"
                ),
                service="nuvio_meta",
            )
        )
    return Ok(
        {
            "visible": _as_bool(raw.get("visible"), True),
            "style": style,
            "upNextFromFurthestEpisode": _as_bool(raw.get("upNextFromFurthestEpisode"), True),
            "episodeThumbnails": _as_bool(raw.get("episodeThumbnails"), True),
            "showUnairedNextUp": _as_bool(raw.get("showUnairedNextUp"), True),
            "blurNextUp": _as_bool(raw.get("blurNextUp"), False),
            "resumePromptOnLaunch": _as_bool(raw.get("resumePromptOnLaunch"), True),
            "sortMode": sort_mode,
        }
    )


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

    match _normalize_tv_trailer(data.get("tvTrailer")):
        case Err() as err:
            return err
        case Ok(tv_trailer):
            pass

    match _normalize_tracking(data.get("tracking")):
        case Err() as err:
            return err
        case Ok(tracking):
            pass

    match _normalize_poster_card_style(data.get("posterCardStyle")):
        case Err() as err:
            return err
        case Ok(poster_card_style):
            pass

    match _normalize_card_depth(data.get("cardDepth")):
        case Err() as err:
            return err
        case Ok(card_depth):
            pass

    match _normalize_continue_watching(data.get("continueWatching")):
        case Err() as err:
            return err
        case Ok(continue_watching):
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

    out_doc: JsonDict = {
        "version": str(data.get("version") or "1.0.0").strip() or "1.0.0",
        "platforms": platforms,
        "ratings": _normalize_ratings(data.get("ratings")),
        "tmdbEnrichment": _normalize_tmdb(data.get("tmdbEnrichment")),
        "traktComments": _as_bool(data.get("traktComments"), True),
        "episodeRatings": episode_ratings,
        "tracking": tracking,
        "tvTrailer": tv_trailer,
        "metaScreen": meta_screen,
    }
    if poster_card_style is not None:
        out_doc["posterCardStyle"] = poster_card_style
    if card_depth is not None:
        out_doc["cardDepth"] = card_depth
    if continue_watching is not None:
        out_doc["continueWatching"] = continue_watching
    return Ok(out_doc)


# ============================================================================
# Compilation (canonical doc -> per-platform Nuvio feature payloads)
# ============================================================================


def _encode_sync_int(value: int) -> JsonDict:
    return {"type": "int", "value": int(value)}


def _decode_sync_int(feature_json: Mapping[str, Any], key: str) -> int | None:
    entry = feature_json.get(key)
    if not isinstance(entry, Mapping) or entry.get("type") != "int":
        return None
    val = entry.get("value")
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str) and val.strip().lstrip("-").isdigit():
        return int(val.strip())
    return None


def compile_tracking_payload(tracking: Mapping[str, Any]) -> str:
    """Serializes tracking pins to Nuvio desktop/mobile `StoredTraktSettings` JSON string."""
    payload: JsonDict = {
        "watchProgressSource": TRACKING_PROGRESS_SOURCES[str(tracking["watchProgressSource"])],
        "continueWatchingDaysCap": int(tracking["continueWatchingDays"]),
        "librarySourceMode": TRACKING_LIBRARY_SOURCES[str(tracking["librarySource"])],
        "moreLikeThisSource": TRACKING_MORE_LIKE_THIS_SOURCES[str(tracking["moreLikeThisSource"])],
        "simklAnimeIdPreference": TRACKING_SIMKL_ANIME_IDS[str(tracking["simklAnimeId"])],
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _invert(table: Mapping[str, str]) -> dict[str, str]:
    return {v: k for k, v in table.items()}


def decompile_tracking_payload(raw_payload: str | None) -> JsonDict | None:
    """Parses Nuvio's `trakt_settings_payload` string back into the declarative `tracking` object."""
    if not raw_payload or not raw_payload.strip():
        return None
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    return _tracking_from_enum_values(
        progress=parsed.get("watchProgressSource"),
        library=parsed.get("librarySourceMode"),
        more_like_this=parsed.get("moreLikeThisSource"),
        simkl_anime=parsed.get("simklAnimeIdPreference"),
        days=parsed.get("continueWatchingDaysCap"),
    )


def _tracking_from_enum_values(
    *,
    progress: Any,
    library: Any,
    more_like_this: Any,
    simkl_anime: Any,
    days: Any,
) -> JsonDict | None:
    if (
        progress is None
        and library is None
        and more_like_this is None
        and simkl_anime is None
        and days is None
    ):
        return None
    declarative: JsonDict = {
        "watchProgressSource": _invert(TRACKING_PROGRESS_SOURCES).get(str(progress or ""), "trakt"),
        "librarySource": _invert(TRACKING_LIBRARY_SOURCES).get(str(library or ""), "trakt"),
        "moreLikeThisSource": _invert(TRACKING_MORE_LIKE_THIS_SOURCES).get(
            str(more_like_this or ""), "trakt"
        ),
        "simklAnimeId": _invert(TRACKING_SIMKL_ANIME_IDS).get(str(simkl_anime or ""), "imdb"),
        "continueWatchingDays": (
            int(days)
            if isinstance(days, (int, float)) and not isinstance(days, bool)
            else _DEFAULT_CONTINUE_WATCHING_DAYS
        ),
    }
    match _normalize_tracking(declarative):
        case Ok(norm):
            return norm
        case Err():
            return None


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


def compile_poster_card_style_payload(poster_card_style: Mapping[str, Any]) -> str:
    """Serializes shared poster card dimensions/flags to Nuvio's `poster_card_style_settings_payload`."""
    payload: JsonDict = {
        "widthDp": int(poster_card_style["widthDp"]),
        "heightDp": int(poster_card_style["heightDp"]),
        "cornerRadiusDp": int(poster_card_style["cornerRadiusDp"]),
        "catalogLandscapeModeEnabled": bool(poster_card_style.get("landscapeMode", False)),
        "hideLabelsEnabled": bool(poster_card_style.get("hideLabels", False)),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decompile_poster_card_style_payload(raw_payload: str | None) -> JsonDict | None:
    """Parses Nuvio's `poster_card_style_settings_payload` into the declarative `posterCardStyle` object."""
    if not raw_payload or not raw_payload.strip():
        return None
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    declarative: JsonDict = {
        "widthDp": parsed.get("widthDp"),
        "heightDp": parsed.get("heightDp"),
        "cornerRadiusDp": parsed.get("cornerRadiusDp"),
        "landscapeMode": bool(parsed.get("catalogLandscapeModeEnabled", False)),
        "hideLabels": bool(parsed.get("hideLabelsEnabled", False)),
    }
    match _normalize_poster_card_style(declarative):
        case Ok(norm):
            return norm
        case Err():
            return None


def compile_card_depth_payload(card_depth: Mapping[str, Any]) -> str:
    """Serializes card 3D depth/sheen settings to Nuvio's `card_depth_style_settings_payload`."""
    payload: JsonDict = {
        "enabled": bool(card_depth.get("enabled", True)),
        "edgeStrength": int(card_depth["edgeStrength"]),
        "sheenStrength": int(card_depth["sheenStrength"]),
        "edgeCoverage": int(card_depth["edgeCoverage"]),
        "postersEnabled": bool(card_depth.get("posters", True)),
        "continueWatchingEnabled": bool(card_depth.get("continueWatching", True)),
        "episodeCardsEnabled": bool(card_depth.get("episodeCards", True)),
        "castEnabled": bool(card_depth.get("cast", True)),
        "trailersEnabled": bool(card_depth.get("trailers", True)),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decompile_card_depth_payload(raw_payload: str | None) -> JsonDict | None:
    """Parses Nuvio's `card_depth_style_settings_payload` into the declarative `cardDepth` object."""
    if not raw_payload or not raw_payload.strip():
        return None
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    declarative: JsonDict = {
        "enabled": bool(parsed.get("enabled", True)),
        "edgeStrength": parsed.get("edgeStrength"),
        "sheenStrength": parsed.get("sheenStrength"),
        "edgeCoverage": parsed.get("edgeCoverage"),
        "posters": bool(parsed.get("postersEnabled", True)),
        "continueWatching": bool(parsed.get("continueWatchingEnabled", True)),
        "episodeCards": bool(parsed.get("episodeCardsEnabled", True)),
        "cast": bool(parsed.get("castEnabled", True)),
        "trailers": bool(parsed.get("trailersEnabled", True)),
    }
    match _normalize_card_depth(declarative):
        case Ok(norm):
            return norm
        case Err():
            return None


def compile_continue_watching_payload(continue_watching: Mapping[str, Any]) -> str:
    """Serializes Continue Watching row settings to Nuvio's `continue_watching_settings_payload`."""
    payload: JsonDict = {
        "isVisible": bool(continue_watching.get("visible", True)),
        "style": CONTINUE_WATCHING_STYLES[str(continue_watching["style"])],
        "upNextFromFurthestEpisode": bool(continue_watching.get("upNextFromFurthestEpisode", True)),
        "use_episode_thumbnails_in_cw": bool(continue_watching.get("episodeThumbnails", True)),
        "show_unaired_next_up": bool(continue_watching.get("showUnairedNextUp", True)),
        "blur_continue_watching_next_up": bool(continue_watching.get("blurNextUp", False)),
        "dismissedNextUpKeys": [],
        "showResumePromptOnLaunch": bool(continue_watching.get("resumePromptOnLaunch", True)),
        "sort_mode": CONTINUE_WATCHING_SORT_MODES[str(continue_watching["sortMode"])],
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decompile_continue_watching_payload(raw_payload: str | None) -> JsonDict | None:
    """Parses Nuvio's `continue_watching_settings_payload` into the declarative `continueWatching` object."""
    if not raw_payload or not raw_payload.strip():
        return None
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    raw_style = str(parsed.get("style") or "")
    raw_sort = str(parsed.get("sort_mode") or "")
    declarative: JsonDict = {
        "visible": bool(parsed.get("isVisible", True)),
        "style": _invert(CONTINUE_WATCHING_STYLES).get(raw_style, "card"),
        "upNextFromFurthestEpisode": bool(parsed.get("upNextFromFurthestEpisode", True)),
        "episodeThumbnails": bool(parsed.get("use_episode_thumbnails_in_cw", True)),
        "showUnairedNextUp": bool(parsed.get("show_unaired_next_up", True)),
        "blurNextUp": bool(parsed.get("blur_continue_watching_next_up", False)),
        "resumePromptOnLaunch": bool(parsed.get("showResumePromptOnLaunch", True)),
        "sortMode": _invert(CONTINUE_WATCHING_SORT_MODES).get(raw_sort, "streaming"),
    }
    match _normalize_continue_watching(declarative):
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

        trakt_feat: JsonDict = {"show_meta_comments": _encode_sync_bool(trakt_comments)}
        tracking = meta_doc.get("tracking")
        if isinstance(tracking, Mapping):
            trakt_feat["watch_progress_source"] = _encode_sync_string(
                TRACKING_PROGRESS_SOURCES[str(tracking["watchProgressSource"])]
            )
            trakt_feat["library_source_mode"] = _encode_sync_string(
                TRACKING_LIBRARY_SOURCES[str(tracking["librarySource"])]
            )
            trakt_feat["more_like_this_source"] = _encode_sync_string(
                TRACKING_MORE_LIKE_THIS_SOURCES[str(tracking["moreLikeThisSource"])]
            )
            trakt_feat["simkl_anime_id_preference"] = _encode_sync_string(
                TRACKING_SIMKL_ANIME_IDS[str(tracking["simklAnimeId"])]
            )
            trakt_feat["continue_watching_days_cap"] = _encode_sync_int(
                int(tracking["continueWatchingDays"])
            )
        out[TRAKT_SETTINGS_FEATURE] = trakt_feat
        tv_mode = EPISODE_RATINGS_MODES[str(meta_doc.get("episodeRatings") or "show")]
        out[LAYOUT_SETTINGS_FEATURE] = {
            "detail_imdb_ratings_visibility": _encode_sync_string(tv_mode),
        }
        tv_trailer = meta_doc.get("tvTrailer")
        if isinstance(tv_trailer, Mapping):
            mode = str(tv_trailer.get("mode") or "fullscreen")
            out[TRAILER_SETTINGS_FEATURE] = {
                "trailer_enabled": _encode_sync_bool(mode != "off"),
                "trailer_play_in_background": _encode_sync_bool(mode == "background"),
                "trailer_delay_seconds": _encode_sync_int(
                    int(tv_trailer.get("delaySeconds", _DEFAULT_TV_TRAILER_DELAY))
                ),
                "trailer_pause_on_scroll": _encode_sync_bool(
                    bool(tv_trailer.get("pauseOnScroll", True))
                ),
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

    tracking = meta_doc.get("tracking")
    if isinstance(tracking, Mapping):
        out[TRAKT_SETTINGS_PAYLOAD_FEATURE] = compile_tracking_payload(tracking)

    poster_card_style = meta_doc.get("posterCardStyle")
    if isinstance(poster_card_style, Mapping):
        out[POSTER_CARD_STYLE_PAYLOAD_FEATURE] = compile_poster_card_style_payload(
            poster_card_style
        )

    card_depth = meta_doc.get("cardDepth")
    if isinstance(card_depth, Mapping):
        out[CARD_DEPTH_STYLE_PAYLOAD_FEATURE] = compile_card_depth_payload(card_depth)

    continue_watching = meta_doc.get("continueWatching")
    if isinstance(continue_watching, Mapping):
        out[CONTINUE_WATCHING_PAYLOAD_FEATURE] = compile_continue_watching_payload(
            continue_watching
        )

    return out


def _overlay_json_payload_string(
    existing_raw: Any,
    fragment_raw: str,
    *,
    preserved_list_keys: Sequence[str] = (),
) -> str:
    """Overlays `fragment_raw` JSON keys onto `existing_raw` while preserving listed array fields."""
    if not isinstance(existing_raw, str) or not existing_raw.strip():
        return fragment_raw
    try:
        existing_obj = json.loads(existing_raw)
        fragment_obj = json.loads(fragment_raw)
    except json.JSONDecodeError:
        return fragment_raw
    if not isinstance(existing_obj, Mapping) or not isinstance(fragment_obj, Mapping):
        return fragment_raw
    merged_obj: JsonDict = {**dict(existing_obj), **dict(fragment_obj)}
    for key in preserved_list_keys:
        prev_val = existing_obj.get(key)
        if isinstance(prev_val, Sequence) and not isinstance(prev_val, (str, bytes)):
            merged_obj[key] = [str(item) for item in prev_val if isinstance(item, str)]
    return json.dumps(merged_obj, separators=(",", ":"), ensure_ascii=False)


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
        elif isinstance(fragment, str) and feature_name in _JSON_OBJECT_OVERLAY_FEATURES:
            features[feature_name] = _overlay_json_payload_string(
                features.get(feature_name),
                fragment,
            )
        elif isinstance(fragment, str) and feature_name == CONTINUE_WATCHING_PAYLOAD_FEATURE:
            features[feature_name] = _overlay_json_payload_string(
                features.get(feature_name),
                fragment,
                preserved_list_keys=_CONTINUE_WATCHING_PRESERVED_KEYS,
            )
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
        trailer = feats.get(TRAILER_SETTINGS_FEATURE)
        trailer_feat: Mapping[str, Any] = trailer if isinstance(trailer, Mapping) else {}
        tracking_view: JsonDict | None = _tracking_from_enum_values(
            progress=_decode_sync_string(trakt_feat, "watch_progress_source"),
            library=_decode_sync_string(trakt_feat, "library_source_mode"),
            more_like_this=_decode_sync_string(trakt_feat, "more_like_this_source"),
            simkl_anime=_decode_sync_string(trakt_feat, "simkl_anime_id_preference"),
            days=_decode_sync_int(trakt_feat, "continue_watching_days_cap"),
        )
        tv_trailer: JsonDict | None = None
        if trailer_feat:
            enabled = _decode_sync_bool(trailer_feat, "trailer_enabled")
            in_bg = _decode_sync_bool(trailer_feat, "trailer_play_in_background")
            delay = _decode_sync_int(trailer_feat, "trailer_delay_seconds")
            tv_trailer = {
                "mode": ("off" if enabled is False else ("background" if in_bg else "fullscreen")),
                "delaySeconds": delay if delay is not None else _DEFAULT_TV_TRAILER_DELAY,
                "pauseOnScroll": _decode_sync_bool(trailer_feat, "trailer_pause_on_scroll")
                is not False,
            }
        meta_screen: JsonDict | None = None
        poster_card_style: JsonDict | None = None
        card_depth: JsonDict | None = None
        continue_watching: JsonDict | None = None
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
        raw_poster = feats.get(POSTER_CARD_STYLE_PAYLOAD_FEATURE)
        poster_card_style = decompile_poster_card_style_payload(
            raw_poster if isinstance(raw_poster, str) else None
        )
        raw_depth = feats.get(CARD_DEPTH_STYLE_PAYLOAD_FEATURE)
        card_depth = decompile_card_depth_payload(raw_depth if isinstance(raw_depth, str) else None)
        raw_cw = feats.get(CONTINUE_WATCHING_PAYLOAD_FEATURE)
        continue_watching = decompile_continue_watching_payload(
            raw_cw if isinstance(raw_cw, str) else None
        )
        mdb_key = _decode_sync_string(mdb_feat, "mdblist_api_key")
        tmdb_key = _decode_sync_string(tm_feat, "tmdb_api_key")
        episode_ratings = None
        tv_trailer = None
        raw_tracking = feats.get(TRAKT_SETTINGS_PAYLOAD_FEATURE)
        tracking_view = decompile_tracking_payload(
            raw_tracking if isinstance(raw_tracking, str) else None
        )

    if (
        ratings_enabled is None
        and tmdb_enabled is None
        and comments is None
        and meta_screen is None
        and poster_card_style is None
        and card_depth is None
        and continue_watching is None
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
        "tracking": tracking_view,
        "tvTrailer": tv_trailer,
        "metaScreen": meta_screen,
    }
    if poster_card_style is not None:
        view["posterCardStyle"] = poster_card_style
    if card_depth is not None:
        view["cardDepth"] = card_depth
    if continue_watching is not None:
        view["continueWatching"] = continue_watching
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
        "tracking": copy.deepcopy(doc.get("tracking")),
    }
    if platform == "tv":
        view["episodeRatings"] = str(doc.get("episodeRatings") or "show")
        view["tvTrailer"] = copy.deepcopy(doc.get("tvTrailer"))
    else:
        view["metaScreen"] = copy.deepcopy(doc.get("metaScreen"))
        view["posterCardStyle"] = copy.deepcopy(doc.get("posterCardStyle"))
        view["cardDepth"] = copy.deepcopy(doc.get("cardDepth"))
        view["continueWatching"] = copy.deepcopy(doc.get("continueWatching"))
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
    # TV-only fields always come from the TV view (desktop/mobile cannot represent them).
    tv_view = views.get("tv") or {}
    consensus["episodeRatings"] = tv_view.get("episodeRatings") or "show"
    consensus["tvTrailer"] = copy.deepcopy(tv_view.get("tvTrailer"))
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
        "episodeRatings": consensus["episodeRatings"],
        "tracking": consensus.get("tracking"),
        "tvTrailer": consensus["tvTrailer"],
        "metaScreen": consensus.get("metaScreen"),
        "posterCardStyle": consensus.get("posterCardStyle"),
        "cardDepth": consensus.get("cardDepth"),
        "continueWatching": consensus.get("continueWatching"),
    }
    match normalize_nuvio_meta_doc(doc):
        case Ok(norm):
            return norm
        case Err():
            return doc
