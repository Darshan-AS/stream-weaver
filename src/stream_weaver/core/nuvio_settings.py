"""
Pure, table-driven compiler for Nuvio per-profile app settings (`nuvio_settings.json`).

Nuvio stores most in-app preferences in a per-platform (`tv` / `desktop` / `mobile`) cloud blob
written through `sync_push_profile_settings_blob`. This module makes every *portable* preference
declarative: playback (incl. **Reuse Last Link**), stream auto-play, next-episode behaviour,
skip-intro, subtitles, audio, appearance/theme, the TV-only Home layout block, notifications and
TV plugin grouping.

Design
------
Everything flows from one registry, `SETTINGS`: each `Setting` row declares

* `path`      — dotted location in the declarative doc (e.g. `playback.reuseLastLink.enabled`);
* `kind`      — validation (`bool`, `int`, `float`, `enum`, `string`, `language`, `color`, `list`);
* `default`   — Nuvio's own in-app default (so an omitted key is a no-op on the device);
* `bindings`  — one or more `(platforms, feature, wire_key, codec)` tuples describing how the
  canonical value is encoded for each client.

`normalize_*`, `build_platform_settings_features`, `merge_profile_settings_blob_settings`,
`decompile_nuvio_settings` and the per-platform drift projection are all generic over the
registry, so supporting a new Nuvio preference is a one-row change.

Deliberately NOT synced (device/hardware-bound or runtime memory): Android engine/libmpv/decoder/
tunneling/DV7 keys, every `ios_*` mpv tuning key, `external_player_id`, libass toggles,
`nvidia_rtx_*`, TV buffer/audio-passthrough keys, TV `track_preference` (per-title track memory),
onboarding flags (`has_chosen_layout`, `experience_settings`) and debrid resolver settings
(AIOStreams already owns debrid resolution in this stack).

Secrets (AnimeSkip client id, IntroDB API key) travel through Nuvio's `sync_*_provider_credentials`
RPCs (`animeskip`/`client_id`, `introdb`/`api_key`) and are also embedded in the mobile blob,
which is the only client that reads credentials from the blob.

Wire formats were derived from the open-source Nuvio clients:
- `NuvioMobile` / `NuvioDesktop`: `core/sync/ProfileSettingsSync.kt`, `core/sync/SyncPreferenceJson.kt`,
  `features/player/PlayerSettingsStorage.*.kt`, `features/settings/ThemeSettingsStorage.*.kt`
- `NuvioTV`: `core/sync/ProfileSettingsSyncService.kt`, `data/local/{PlayerSettingsDataStore,
  LayoutPreferenceDataStore,ThemeDataStore,AnimeSkipSettingsDataStore,PluginDataStore}.kt`
"""

import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict

SERVICE_NAME: Final = "nuvio_settings"
VALID_NUVIO_SETTINGS_PLATFORMS: Final[tuple[str, ...]] = ("tv", "desktop", "mobile")
_CONSENSUS_PRIORITY: Final[tuple[str, ...]] = ("mobile", "desktop", "tv")

ALL: Final[frozenset[str]] = frozenset(VALID_NUVIO_SETTINGS_PLATFORMS)
TV: Final[frozenset[str]] = frozenset({"tv"})
DESKTOP: Final[frozenset[str]] = frozenset({"desktop"})
MOBILE: Final[frozenset[str]] = frozenset({"mobile"})
KMP: Final[frozenset[str]] = frozenset({"desktop", "mobile"})  # shared Compose-Multiplatform code
TV_DESKTOP: Final[frozenset[str]] = frozenset({"tv", "desktop"})

PLAYER_SETTINGS_FEATURE: Final = "player_settings"
THEME_SETTINGS_FEATURE: Final = "theme_settings"
LAYOUT_SETTINGS_FEATURE: Final = "layout_settings"
NOTIFICATIONS_SETTINGS_FEATURE: Final = "notifications_settings"
ANIMESKIP_SETTINGS_FEATURE: Final = "animeskip_settings"
PLUGIN_SETTINGS_FEATURE: Final = "plugin_settings"

PROVIDER_CREDENTIAL_ANIMESKIP: Final = "animeskip"
PROVIDER_CREDENTIAL_INTRODB: Final = "introdb"
PROVIDER_CREDENTIAL_CLIENT_ID_FIELD: Final = "client_id"
PROVIDER_CREDENTIAL_API_KEY_FIELD: Final = "api_key"

REUSE_LAST_LINK_CACHE_HOURS: Final[tuple[int, ...]] = (1, 2, 3, 6, 12, 24, 48, 72, 168)
STREAM_AUTO_PLAY_TIMEOUTS: Final[tuple[int, ...]] = (*range(11), 15, 20, 25, 30)

_LANGUAGE_CODE_RE: Final = re.compile(r"^([A-Za-z]{2,3})(?:-([A-Za-z]{2,4}))?$")
_COLOR_HEX_RE: Final = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
_PATH_SEP: Final = "."


# ============================================================================
# Codecs: canonical value <-> Nuvio `{type, value}` sync preference
# ============================================================================


class _Omit:
    """Sentinel: the platform cannot represent this value; leave the remote key untouched."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "OMIT"


OMIT: Final = _Omit()
Wire = Literal["boolean", "int", "float", "string", "string_set"]


def _identity(value: Any) -> Any:
    return value


@dataclass(frozen=True)
class Codec:
    """Encodes a canonical value to a Nuvio wire value (or `OMIT`) and decodes it back."""

    wire_type: Wire
    encode: Callable[[Any], Any] = _identity
    decode: Callable[[Any], Any] = _identity

    def project(self, value: Any) -> Any:
        """Round-trips a canonical value through this codec (what the platform can represent)."""
        wire = self.encode(value)
        if wire is OMIT:
            return OMIT
        return self.decode(wire)


BOOL: Final = Codec("boolean")
INT: Final = Codec("int")
FLOAT: Final = Codec("float", encode=float, decode=float)
STR: Final = Codec("string")
STR_SET: Final = Codec(
    "string_set",
    encode=lambda v: sorted({str(x) for x in v}),
    decode=lambda v: sorted({str(x) for x in v}) if isinstance(v, list) else None,
)


def enum_codec(mapping: Mapping[str, str]) -> Codec:
    """Maps canonical enum tokens to a platform's enum names; unsupported tokens are omitted."""
    inverse = {wire: canon for canon, wire in mapping.items()}
    return Codec(
        "string",
        encode=lambda v: mapping.get(str(v), OMIT),
        decode=lambda v: inverse.get(str(v)),
    )


def subset_codec(allowed: Sequence[str]) -> Codec:
    """String-set codec that silently drops members a platform does not support."""
    allowed_set = frozenset(allowed)
    return Codec(
        "string_set",
        encode=lambda v: sorted({str(x) for x in v if str(x) in allowed_set}),
        decode=lambda v: (
            sorted({str(x) for x in v if str(x) in allowed_set}) if isinstance(v, list) else None
        ),
    )


def json_list_codec() -> Codec:
    """A JSON array serialized into a single string preference (NuvioTV `hero_catalog_keys`)."""

    def _decode(raw: Any) -> Any:
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            return None
        return [str(x) for x in parsed] if isinstance(parsed, list) else None

    return Codec(
        "string",
        encode=lambda v: json.dumps([str(x) for x in v], separators=(",", ":")),
        decode=_decode,
    )


def normalize_color_hex(raw: Any) -> str | None:
    """Canonical colour form is `#AARRGGBB` (6-digit input is promoted to fully opaque)."""
    if not isinstance(raw, str):
        return None
    m = _COLOR_HEX_RE.match(raw.strip())
    if not m:
        return None
    digits = m.group(1).upper()
    return f"#{digits}" if len(digits) == 8 else f"#FF{digits}"


def _color_hex_to_argb_int(value: Any) -> Any:
    hex_color = normalize_color_hex(value)
    if hex_color is None:
        return OMIT
    unsigned = int(hex_color[1:], 16)
    return unsigned - (1 << 32) if unsigned >= (1 << 31) else unsigned


def _argb_int_to_color_hex(raw: Any) -> Any:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return f"#{int(raw) & 0xFFFFFFFF:08X}"


COLOR_HEX: Final = Codec(
    "string", encode=lambda v: normalize_color_hex(v) or OMIT, decode=normalize_color_hex
)
COLOR_ARGB_INT: Final = Codec("int", encode=_color_hex_to_argb_int, decode=_argb_int_to_color_hex)


# ============================================================================
# Registry model
# ============================================================================

Kind = Literal["bool", "int", "float", "enum", "string", "language", "color", "list"]


@dataclass(frozen=True)
class Binding:
    platforms: frozenset[str]
    feature: str
    key: str
    codec: Codec


@dataclass(frozen=True)
class Setting:
    path: str
    kind: Kind
    default: Any
    bindings: tuple[Binding, ...]
    choices: tuple[str, ...] = ()
    bounds: tuple[float, float] | None = None
    nullable: bool = False
    secret: bool = False
    # `int` kind: exact values the Nuvio UI picker offers (clients snap anything else).
    allowed: tuple[int, ...] = ()
    # `language` kind: special tokens accepted in addition to ISO codes.
    tokens: tuple[str, ...] = ()
    doc: str = ""

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.path.split(_PATH_SEP))

    @property
    def block(self) -> str:
        return self.parts[0]


def _b(platforms: frozenset[str], feature: str, key: str, codec: Codec) -> Binding:
    return Binding(platforms, feature, key, codec)


def _player(platforms: frozenset[str], key: str, codec: Codec) -> Binding:
    return _b(platforms, PLAYER_SETTINGS_FEATURE, key, codec)


def _layout(key: str, codec: Codec) -> Binding:
    return _b(TV, LAYOUT_SETTINGS_FEATURE, key, codec)


def _theme(platforms: frozenset[str], key: str, codec: Codec) -> Binding:
    return _b(platforms, THEME_SETTINGS_FEATURE, key, codec)


# --- enum tables (canonical token -> Nuvio enum name) ---------------------------------------

THEMES_MOBILE: Final[Mapping[str, str]] = {
    t.lower(): t for t in ("CRIMSON", "OCEAN", "VIOLET", "EMERALD", "AMBER", "ROSE", "WHITE")
}
THEMES_DESKTOP: Final[Mapping[str, str]] = {
    **THEMES_MOBILE,
    **{t.lower(): t for t in ("GOLD", "JADE", "ROSE_GOLD", "ARCTIC_BLUE", "GRAPHITE")},
}
THEMES_TV: Final[Mapping[str, str]] = {**THEMES_DESKTOP, "custom": "CUSTOM"}
NAV_BAR_STYLES: Final[Mapping[str, str]] = {
    k: k for k in ("adaptive", "expanded", "compact", "classic")
}
DESKTOP_NAVIGATION: Final[Mapping[str, str]] = {"sidebar": "Sidebar", "topBar": "TopBar"}
TV_FONTS: Final[Mapping[str, str]] = {
    "inter": "INTER",
    "dmSans": "DM_SANS",
    "openSans": "OPEN_SANS",
}
TV_SETTINGS_UI_STYLES: Final[Mapping[str, str]] = {
    "classic": "CLASSIC",
    "zen": "ZEN",
    "horizon": "HORIZON",
}
STREAM_SELECTION_MODES: Final[Mapping[str, str]] = {
    "manual": "MANUAL",
    "firstStream": "FIRST_STREAM",
    "regexMatch": "REGEX_MATCH",
}
STREAM_SELECTION_SCOPES: Final[Mapping[str, str]] = {
    "all": "ALL_SOURCES",
    "installedAddons": "INSTALLED_ADDONS_ONLY",
    "enabledPlugins": "ENABLED_PLUGINS_ONLY",
}
THRESHOLD_MODES: Final[Mapping[str, str]] = {
    "percentage": "PERCENTAGE",
    "minutesBeforeEnd": "MINUTES_BEFORE_END",
}
ADDON_SUBTITLE_STARTUP_MODES: Final[Mapping[str, str]] = {
    "fastStartup": "FAST_STARTUP",
    "preferredOnly": "PREFERRED_ONLY",
    "allSubtitles": "ALL_SUBTITLES",
}
SUBTITLE_ORGANIZATION_MODES: Final[Mapping[str, str]] = {
    "none": "NONE",
    "byLanguage": "BY_LANGUAGE",
    "byAddon": "BY_ADDON",
}
AUTO_SKIP_SEGMENTS: Final[tuple[str, ...]] = ("intro", "recap", "outro", "movie-credits")
AUTO_SKIP_SEGMENTS_DESKTOP: Final[tuple[str, ...]] = ("intro", "recap", "outro")
TV_HOME_LAYOUTS: Final[Mapping[str, str]] = {
    "classic": "CLASSIC",
    "grid": "GRID",
    "modern": "MODERN",
}
TV_DISCOVER_LOCATIONS: Final[Mapping[str, str]] = {
    "off": "OFF",
    "inSearch": "IN_SEARCH",
    "inSidebar": "IN_SIDEBAR",
}
TV_TRAILER_TARGETS: Final[Mapping[str, str]] = {
    "expandedCard": "EXPANDED_CARD",
    "heroMedia": "HERO_MEDIA",
}
TV_EPISODE_OVERLAY_STYLES: Final[Mapping[str, str]] = {
    "blur": "BLUR",
    "artwork": "ARTWORK",
    "none": "NONE",
}
TV_HOME_RATINGS: Final[Mapping[str, str]] = {"showAll": "SHOW_ALL", "hideAll": "HIDE_ALL"}
TV_CW_CARD_STYLES: Final[Mapping[str, str]] = {"card": "CARD", "wide": "WIDE", "poster": "POSTER"}
TV_CW_SORT_MODES: Final[Mapping[str, str]] = {
    "default": "DEFAULT",
    "streaming": "STREAMING_STYLE",
    "splitUpcoming": "SPLIT_UPCOMING",
}

AUDIO_LANGUAGE_TOKENS: Final[tuple[str, ...]] = ("default", "device", "original")
SUBTITLE_LANGUAGE_TOKENS: Final[tuple[str, ...]] = ("none", "device", "forced")


def _choices(table: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(table)


# --- the registry ---------------------------------------------------------------------------

SETTINGS: Final[tuple[Setting, ...]] = (
    # ---------------------------------------------------------------- appearance
    Setting(
        "appearance.theme",
        "enum",
        "white",
        (
            _theme(MOBILE, "selected_theme", enum_codec(THEMES_MOBILE)),
            _theme(DESKTOP, "selected_theme", enum_codec(THEMES_DESKTOP)),
            _theme(TV, "selected_theme", enum_codec(THEMES_TV)),
        ),
        choices=_choices(THEMES_TV),
        doc="Accent theme. Mobile supports the first 7; desktop/TV add gold/jade/roseGold/arcticBlue/graphite.",
    ),
    Setting(
        "appearance.amoledBlack",
        "bool",
        False,
        (_theme(KMP, "amoled_enabled", BOOL), _theme(TV, "amoled_mode", BOOL)),
        doc="Pure-black backgrounds for OLED panels.",
    ),
    Setting(
        "appearance.navBarStyle",
        "enum",
        "adaptive",
        (_theme(KMP, "nav_bar_style", enum_codec(NAV_BAR_STYLES)),),
        choices=_choices(NAV_BAR_STYLES),
    ),
    Setting(
        "appearance.desktopNavigation",
        "enum",
        "sidebar",
        (_theme(DESKTOP, "desktop_navigation_layout", enum_codec(DESKTOP_NAVIGATION)),),
        choices=_choices(DESKTOP_NAVIGATION),
    ),
    Setting(
        "appearance.tv.font",
        "enum",
        "inter",
        (_theme(TV, "selected_font", enum_codec(TV_FONTS)),),
        choices=_choices(TV_FONTS),
    ),
    Setting(
        "appearance.tv.amoledSurfaces",
        "bool",
        False,
        (_theme(TV, "amoled_surfaces_mode", BOOL),),
    ),
    Setting(
        "appearance.tv.settingsUiStyle",
        "enum",
        "classic",
        (_theme(TV, "settings_ui_style", enum_codec(TV_SETTINGS_UI_STYLES)),),
        choices=_choices(TV_SETTINGS_UI_STYLES),
    ),
    Setting(
        "appearance.tv.startupSplash",
        "bool",
        True,
        (_layout("startup_splash_enabled", BOOL),),
    ),
    # ---------------------------------------------------------------- playback: reuse last link
    Setting(
        "playback.reuseLastLink.enabled",
        "bool",
        False,
        (_player(ALL, "stream_reuse_last_link_enabled", BOOL),),
        doc="Auto-play the last working stream for the same movie/episode while the cache is valid.",
    ),
    Setting(
        "playback.reuseLastLink.cacheHours",
        "int",
        24,
        (_player(ALL, "stream_reuse_last_link_cache_hours", INT),),
        bounds=(1, 168),
        allowed=REUSE_LAST_LINK_CACHE_HOURS,
    ),
    # ---------------------------------------------------------------- playback: stream selection
    Setting(
        "playback.streamSelection.mode",
        "enum",
        "manual",
        (_player(ALL, "stream_auto_play_mode", enum_codec(STREAM_SELECTION_MODES)),),
        choices=_choices(STREAM_SELECTION_MODES),
    ),
    Setting(
        "playback.streamSelection.scope",
        "enum",
        "all",
        (_player(ALL, "stream_auto_play_source", enum_codec(STREAM_SELECTION_SCOPES)),),
        choices=_choices(STREAM_SELECTION_SCOPES),
    ),
    Setting(
        "playback.streamSelection.allowedAddons",
        "list",
        [],
        (_player(ALL, "stream_auto_play_selected_addons", STR_SET),),
        doc="Addon ids eligible for auto-play (empty = all installed addons).",
    ),
    Setting(
        "playback.streamSelection.regex",
        "string",
        "",
        (_player(ALL, "stream_auto_play_regex", STR),),
    ),
    Setting(
        "playback.streamSelection.timeoutSeconds",
        "int",
        3,
        (_player(ALL, "stream_auto_play_timeout_seconds", INT),),
        bounds=(0, 30),
        allowed=STREAM_AUTO_PLAY_TIMEOUTS,
    ),
    # ---------------------------------------------------------------- playback: next episode
    Setting(
        "playback.nextEpisode.autoPlay",
        "bool",
        False,
        (_player(ALL, "stream_auto_play_next_episode_enabled", BOOL),),
    ),
    Setting(
        "playback.nextEpisode.preferBingeGroup",
        "bool",
        True,
        (
            _player(KMP, "stream_auto_play_prefer_binge_group", BOOL),
            _player(TV, "stream_auto_play_prefer_bingegroup_next_episode", BOOL),
        ),
    ),
    Setting(
        "playback.nextEpisode.reuseBingeGroup",
        "bool",
        True,
        (_player(ALL, "stream_auto_play_reuse_binge_group", BOOL),),
    ),
    Setting(
        "playback.nextEpisode.fallbackToFirstStream",
        "bool",
        True,
        (_player(TV_DESKTOP, "stream_auto_play_next_episode_fallback_enabled", BOOL),),
        doc="When the binge group is missing, auto-pick the first stream instead of showing the picker.",
    ),
    Setting(
        "playback.nextEpisode.thresholdMode",
        "enum",
        "percentage",
        (_player(ALL, "next_episode_threshold_mode", enum_codec(THRESHOLD_MODES)),),
        choices=_choices(THRESHOLD_MODES),
    ),
    Setting(
        "playback.nextEpisode.thresholdPercent",
        "float",
        99.0,
        (_player(ALL, "next_episode_threshold_percent_v2", FLOAT),),
        bounds=(50.0, 100.0),
    ),
    Setting(
        "playback.nextEpisode.thresholdMinutesBeforeEnd",
        "float",
        2.0,
        (_player(ALL, "next_episode_threshold_minutes_before_end_v2", FLOAT),),
        bounds=(0.0, 10.0),
    ),
    Setting(
        "playback.nextEpisode.preloadNextEpisodeSources",
        "bool",
        False,
        (_player(TV, "preload_next_episode_sources", BOOL),),
    ),
    Setting(
        "playback.nextEpisode.stillWatchingPrompt.enabled",
        "bool",
        False,
        (_player(TV, "still_watching_enabled", BOOL),),
    ),
    Setting(
        "playback.nextEpisode.stillWatchingPrompt.episodeThreshold",
        "int",
        3,
        (_player(TV, "still_watching_episode_threshold", INT),),
        bounds=(2, 6),
    ),
    # ---------------------------------------------------------------- playback: skip
    Setting(
        "playback.skip.skipIntro",
        "bool",
        True,
        (_player(ALL, "skip_intro_enabled", BOOL),),
        doc="IntroDB-powered Skip Intro / Recap button.",
    ),
    Setting(
        "playback.skip.autoSkip",
        "list",
        [],
        (
            _player(DESKTOP, "auto_skip_segment_types", subset_codec(AUTO_SKIP_SEGMENTS_DESKTOP)),
            _player(TV, "auto_skip_segment_types", subset_codec(AUTO_SKIP_SEGMENTS)),
        ),
        choices=AUTO_SKIP_SEGMENTS,
        doc="Segments skipped automatically without pressing the button (`movie-credits` is TV-only).",
    ),
    Setting(
        "playback.skip.animeSkip.enabled",
        "bool",
        False,
        (
            _player(KMP, "animeskip_enabled", BOOL),
            _b(TV, ANIMESKIP_SETTINGS_FEATURE, "animeskip_enabled", BOOL),
        ),
    ),
    Setting(
        "playback.skip.animeSkip.clientId",
        "string",
        "",
        (_player(MOBILE, "animeskip_client_id", STR),),
        secret=True,
        doc="Also pushed via provider credentials (`animeskip` / `client_id`).",
    ),
    Setting(
        "playback.skip.introDbApiKey",
        "string",
        "",
        (_player(MOBILE, "introdb_api_key", STR),),
        secret=True,
        doc="Also pushed via provider credentials (`introdb` / `api_key`).",
    ),
    # ---------------------------------------------------------------- playback: UI
    Setting(
        "playback.ui.loadingOverlay",
        "bool",
        True,
        (
            _player(KMP, "show_loading_overlay", BOOL),
            _player(TV, "loading_overlay_enabled", BOOL),
        ),
    ),
    Setting(
        "playback.ui.contentWarnings",
        "bool",
        True,
        (
            _player(KMP, "show_parental_guide", BOOL),
            _player(TV, "parental_guide_enabled", BOOL),
        ),
        doc="Parental-guidance overlay at playback start.",
    ),
    Setting(
        "playback.ui.holdToSpeed.enabled",
        "bool",
        True,
        (_player(KMP, "hold_to_speed_enabled", BOOL),),
    ),
    Setting(
        "playback.ui.holdToSpeed.speed",
        "float",
        2.0,
        (_player(KMP, "hold_to_speed_value", FLOAT),),
        bounds=(1.0, 4.0),
    ),
    Setting(
        "playback.ui.touchGestures",
        "bool",
        True,
        (_player(KMP, "touch_gestures_enabled", BOOL),),
    ),
    Setting(
        "playback.ui.tv.pauseOverlay", "bool", True, (_player(TV, "pause_overlay_enabled", BOOL),)
    ),
    Setting("playback.ui.tv.osdClock", "bool", True, (_player(TV, "osd_clock_enabled", BOOL),)),
    Setting(
        "playback.ui.tv.showLoadingStatus",
        "bool",
        True,
        (_player(TV, "show_player_loading_status", BOOL),),
    ),
    Setting(
        "playback.ui.tv.postPlayRecommendations",
        "bool",
        True,
        (_player(TV, "post_play_recommendations_enabled", BOOL),),
    ),
    Setting(
        "playback.ui.tv.postPlayMovieThresholdPercent",
        "int",
        96,
        (_player(TV, "post_play_movie_threshold_percent", INT),),
        bounds=(80, 100),
    ),
    Setting(
        "playback.ui.tv.playbackIssueReports",
        "bool",
        False,
        (_player(TV, "playback_issue_reports_enabled", BOOL),),
    ),
    # ---------------------------------------------------------------- playback: external player
    Setting(
        "playback.externalPlayer.enabled",
        "bool",
        False,
        (_player(KMP, "external_player_enabled", BOOL),),
    ),
    Setting(
        "playback.externalPlayer.forwardSubtitles",
        "bool",
        False,
        (_player(KMP, "external_player_forward_subtitles", BOOL),),
    ),
    Setting(
        "playback.externalPlayer.sendSkipSegments",
        "bool",
        False,
        (_player(KMP, "external_player_send_skip_segments", BOOL),),
    ),
    # ---------------------------------------------------------------- audio
    Setting(
        "audio.preferredLanguage",
        "language",
        "device",
        (_player(ALL, "preferred_audio_language", STR),),
        tokens=AUDIO_LANGUAGE_TOKENS,
    ),
    Setting(
        "audio.secondaryLanguage",
        "language",
        None,
        (_player(ALL, "secondary_preferred_audio_language", STR),),
        nullable=True,
    ),
    Setting("audio.tv.skipSilence", "bool", False, (_player(TV, "skip_silence", BOOL),)),
    # ---------------------------------------------------------------- subtitles
    Setting(
        "subtitles.preferredLanguage",
        "language",
        "none",
        (
            _player(KMP, "preferred_subtitle_language", STR),
            _player(TV, "subtitle_preferred_language", STR),
        ),
        tokens=SUBTITLE_LANGUAGE_TOKENS,
    ),
    Setting(
        "subtitles.secondaryLanguage",
        "language",
        None,
        (
            _player(KMP, "secondary_preferred_subtitle_language", STR),
            _player(TV, "subtitle_secondary_language", STR),
        ),
        nullable=True,
    ),
    Setting(
        "subtitles.useForced",
        "bool",
        False,
        (_player(ALL, "subtitle_use_forced_subtitles", BOOL),),
    ),
    Setting(
        "subtitles.showOnlyPreferredLanguages",
        "bool",
        False,
        (_player(ALL, "subtitle_show_only_preferred_languages", BOOL),),
    ),
    Setting(
        "subtitles.stripSdh",
        "bool",
        False,
        (_player(TV_DESKTOP, "subtitle_strip_sdh", BOOL),),
        doc="Hide sound descriptions / speaker labels in text subtitles.",
    ),
    Setting(
        "subtitles.addonStartupMode",
        "enum",
        "allSubtitles",
        (_player(KMP, "addon_subtitle_startup_mode", enum_codec(ADDON_SUBTITLE_STARTUP_MODES)),),
        choices=_choices(ADDON_SUBTITLE_STARTUP_MODES),
    ),
    Setting(
        "subtitles.style.textColor",
        "color",
        "#FFFFFFFF",
        (
            _player(KMP, "subtitle_text_color", COLOR_HEX),
            _player(TV, "subtitle_text_color", COLOR_ARGB_INT),
        ),
    ),
    Setting(
        "subtitles.style.backgroundColor",
        "color",
        "#00000000",
        (
            _player(KMP, "subtitle_background_color", COLOR_HEX),
            _player(TV, "subtitle_background_color", COLOR_ARGB_INT),
        ),
    ),
    Setting(
        "subtitles.style.outline.enabled",
        "bool",
        True,
        (_player(ALL, "subtitle_outline_enabled", BOOL),),
    ),
    Setting(
        "subtitles.style.outline.color",
        "color",
        "#FF000000",
        (
            _player(KMP, "subtitle_outline_color", COLOR_HEX),
            _player(TV, "subtitle_outline_color", COLOR_ARGB_INT),
        ),
    ),
    Setting(
        "subtitles.style.outline.width",
        "int",
        2,
        (_player(ALL, "subtitle_outline_width", INT),),
        bounds=(1, 5),
    ),
    Setting("subtitles.style.bold", "bool", False, (_player(ALL, "subtitle_bold", BOOL),)),
    Setting(
        "subtitles.style.sizeSp",
        "int",
        18,
        (_player(KMP, "subtitle_font_size_sp", INT),),
        bounds=(12, 40),
    ),
    Setting(
        "subtitles.style.bottomOffset",
        "int",
        20,
        (_player(KMP, "subtitle_bottom_offset", INT),),
        bounds=(0, 200),
    ),
    Setting(
        "subtitles.tv.sizePercent",
        "int",
        100,
        (_player(TV, "subtitle_size", INT),),
        bounds=(50, 200),
    ),
    Setting(
        "subtitles.tv.verticalOffsetPercent",
        "int",
        5,
        (_player(TV, "subtitle_vertical_offset", INT),),
        bounds=(-20, 50),
    ),
    Setting(
        "subtitles.tv.organizationMode",
        "enum",
        "none",
        (_player(TV, "subtitle_organization_mode", enum_codec(SUBTITLE_ORGANIZATION_MODES)),),
        choices=_choices(SUBTITLE_ORGANIZATION_MODES),
    ),
    Setting(
        "subtitles.tv.hdrStyle.enabled",
        "bool",
        False,
        (_player(TV, "subtitle_hdr_style_enabled", BOOL),),
        doc="Separate subtitle style for HDR / Dolby Vision playback.",
    ),
    Setting(
        "subtitles.tv.hdrStyle.textColor",
        "color",
        "#FFFFFFFF",
        (_player(TV, "subtitle_hdr_text_color", COLOR_ARGB_INT),),
    ),
    Setting(
        "subtitles.tv.hdrStyle.brightness",
        "int",
        40,
        (_player(TV, "subtitle_hdr_brightness", INT),),
        bounds=(0, 100),
    ),
    Setting(
        "subtitles.tv.hdrStyle.dimPlayerControls",
        "bool",
        False,
        (_player(TV, "subtitle_hdr_dim_player_controls", BOOL),),
    ),
    # ---------------------------------------------------------------- TV home layout
    Setting(
        "tvLayout.homeLayout",
        "enum",
        "modern",
        (_layout("selected_layout", enum_codec(TV_HOME_LAYOUTS)),),
        choices=_choices(TV_HOME_LAYOUTS),
    ),
    Setting("tvLayout.heroSection", "bool", True, (_layout("hero_section_enabled", BOOL),)),
    Setting(
        "tvLayout.heroCatalogKeys",
        "list",
        [],
        (_layout("hero_catalog_keys", json_list_codec()),),
        doc="Catalog keys feeding the hero carousel (empty = Nuvio default).",
    ),
    Setting(
        "tvLayout.heroFullScreenBackdrop",
        "bool",
        False,
        (_layout("modern_hero_full_screen_backdrop", BOOL),),
    ),
    Setting(
        "tvLayout.sidebar.collapsedByDefault",
        "bool",
        False,
        (_layout("sidebar_collapsed_by_default", BOOL),),
    ),
    Setting("tvLayout.sidebar.modern", "bool", False, (_layout("modern_sidebar_enabled", BOOL),)),
    Setting(
        "tvLayout.sidebar.blur",
        "bool",
        False,
        (_layout("modern_sidebar_blur_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.discoverLocation",
        "enum",
        "inSearch",
        (_layout("discover_location", enum_codec(TV_DISCOVER_LOCATIONS)),),
        choices=_choices(TV_DISCOVER_LOCATIONS),
    ),
    Setting("tvLayout.posters.labels", "bool", True, (_layout("poster_labels_enabled", BOOL),)),
    Setting(
        "tvLayout.posters.addonName",
        "bool",
        True,
        (_layout("catalog_addon_name_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.posters.catalogType",
        "bool",
        True,
        (_layout("catalog_type_suffix_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.posters.landscapeMode",
        "bool",
        False,
        (_layout("modern_landscape_posters_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.posters.alwaysLandscapeClearlogo",
        "bool",
        False,
        (_layout("always_show_landscape_clearlogo", BOOL),),
    ),
    Setting(
        "tvLayout.posters.classicFocusGradient",
        "bool",
        False,
        (_layout("classic_focus_gradient_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.posters.widthDp",
        "int",
        126,
        (_layout("poster_card_width_dp", INT),),
        bounds=(60, 400),
    ),
    Setting(
        "tvLayout.posters.heightDp",
        "int",
        189,
        (_layout("poster_card_height_dp", INT),),
        bounds=(90, 600),
    ),
    Setting(
        "tvLayout.posters.cornerRadiusDp",
        "int",
        12,
        (_layout("poster_card_corner_radius_dp", INT),),
        bounds=(0, 48),
    ),
    Setting(
        "tvLayout.focusedPoster.expandToBackdrop",
        "bool",
        True,
        (_layout("focused_poster_backdrop_expand_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.focusedPoster.expandDelaySeconds",
        "int",
        3,
        (_layout("focused_poster_backdrop_expand_delay_seconds", INT),),
        bounds=(0, 30),
    ),
    Setting(
        "tvLayout.focusedPoster.autoplayTrailer",
        "bool",
        False,
        (_layout("focused_poster_backdrop_trailer_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.focusedPoster.trailerMuted",
        "bool",
        True,
        (_layout("focused_poster_backdrop_trailer_muted", BOOL),),
    ),
    Setting(
        "tvLayout.focusedPoster.trailerTarget",
        "enum",
        "heroMedia",
        (
            _layout(
                "focused_poster_backdrop_trailer_playback_target", enum_codec(TV_TRAILER_TARGETS)
            ),
        ),
        choices=_choices(TV_TRAILER_TARGETS),
    ),
    Setting("tvLayout.cardDepth.enabled", "bool", False, (_layout("card_depth_enabled", BOOL),)),
    Setting(
        "tvLayout.cardDepth.edgeStrength",
        "int",
        28,
        (_layout("card_depth_edge_strength", INT),),
        bounds=(0, 100),
    ),
    Setting(
        "tvLayout.cardDepth.sheenStrength",
        "int",
        10,
        (_layout("card_depth_sheen_strength", INT),),
        bounds=(0, 100),
    ),
    Setting(
        "tvLayout.cardDepth.edgeCoverage",
        "int",
        0,
        (_layout("card_depth_edge_coverage", INT),),
        bounds=(0, 100),
    ),
    Setting(
        "tvLayout.cardDepth.posters", "bool", True, (_layout("card_depth_posters_enabled", BOOL),)
    ),
    Setting(
        "tvLayout.cardDepth.continueWatching",
        "bool",
        True,
        (_layout("card_depth_continue_watching_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.cardDepth.episodeCards",
        "bool",
        True,
        (_layout("card_depth_episode_cards_enabled", BOOL),),
    ),
    Setting("tvLayout.cardDepth.cast", "bool", True, (_layout("card_depth_cast_enabled", BOOL),)),
    Setting(
        "tvLayout.cardDepth.trailers",
        "bool",
        True,
        (_layout("card_depth_trailers_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.continueWatching.enabled",
        "bool",
        True,
        (_layout("continue_watching_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.continueWatching.cardStyle",
        "enum",
        "card",
        (_layout("continue_watching_card_style", enum_codec(TV_CW_CARD_STYLES)),),
        choices=_choices(TV_CW_CARD_STYLES),
    ),
    Setting(
        "tvLayout.continueWatching.sortMode",
        "enum",
        "default",
        (_layout("continue_watching_sort_mode", enum_codec(TV_CW_SORT_MODES)),),
        choices=_choices(TV_CW_SORT_MODES),
    ),
    Setting(
        "tvLayout.continueWatching.episodeThumbnails",
        "bool",
        True,
        (_layout("use_episode_thumbnails_in_cw", BOOL),),
    ),
    Setting(
        "tvLayout.continueWatching.showUnairedNextUp",
        "bool",
        True,
        (_layout("show_unaired_next_up", BOOL),),
    ),
    Setting(
        "tvLayout.continueWatching.nextUpFromFurthestEpisode",
        "bool",
        True,
        (_layout("next_up_from_furthest_episode", BOOL),),
    ),
    Setting(
        "tvLayout.continueWatching.blurNextUp",
        "bool",
        False,
        (_layout("blur_continue_watching_next_up", BOOL),),
    ),
    Setting(
        "tvLayout.details.trailerButton",
        "bool",
        True,
        (_layout("detail_page_trailer_button_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.details.preferExternalMetaAddon",
        "bool",
        True,
        (_layout("prefer_external_meta_addon_detail", BOOL),),
    ),
    Setting(
        "tvLayout.details.blurUnwatchedEpisodes",
        "bool",
        False,
        (_layout("blur_unwatched_episodes", BOOL),),
    ),
    Setting(
        "tvLayout.details.showFullReleaseDate",
        "bool",
        True,
        (_layout("show_full_release_date", BOOL),),
    ),
    Setting(
        "tvLayout.details.episodeOptionsOverlay",
        "enum",
        "blur",
        (_layout("episode_options_overlay_style", enum_codec(TV_EPISODE_OVERLAY_STYLES)),),
        choices=_choices(TV_EPISODE_OVERLAY_STYLES),
    ),
    Setting(
        "tvLayout.details.randomEpisodeShuffle",
        "bool",
        False,
        (_layout("random_episode_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.homeImdbRatings",
        "enum",
        "showAll",
        (_layout("home_imdb_ratings_visibility", enum_codec(TV_HOME_RATINGS)),),
        choices=_choices(TV_HOME_RATINGS),
    ),
    Setting(
        "tvLayout.hideUnreleasedContent",
        "bool",
        False,
        (_layout("hide_unreleased_content", BOOL),),
    ),
    Setting("tvLayout.followAddonsOrder", "bool", False, (_layout("follow_addons_order", BOOL),)),
    Setting(
        "tvLayout.performance.memoryOnlyVerticalScroll",
        "bool",
        True,
        (_layout("memory_only_vertical_scroll", BOOL),),
    ),
    Setting(
        "tvLayout.performance.smoothBringIntoView",
        "bool",
        True,
        (_layout("smooth_bring_into_view_enabled", BOOL),),
    ),
    Setting(
        "tvLayout.performance.fastHorizontalNavigation",
        "bool",
        False,
        (_layout("fast_horizontal_navigation_enabled", BOOL),),
    ),
    # ---------------------------------------------------------------- notifications / plugins
    Setting(
        "notifications.episodeReleaseAlerts",
        "bool",
        False,
        (_b(KMP, NOTIFICATIONS_SETTINGS_FEATURE, "episode_release_alerts_enabled", BOOL),),
        doc="Local notifications when a new episode of a saved show airs.",
    ),
    Setting(
        "tvPlugins.groupStreamsByRepository",
        "bool",
        False,
        (_b(TV, PLUGIN_SETTINGS_FEATURE, "group_streams_by_repository", BOOL),),
    ),
)

# `notifications_settings` is a typed Kotlin class on desktop/mobile: raw JSON booleans, not the
# `{type, value}` envelope every other feature uses.
_RAW_VALUE_FEATURES: Final[frozenset[str]] = frozenset({NOTIFICATIONS_SETTINGS_FEATURE})

BLOCKS: Final[tuple[str, ...]] = tuple(dict.fromkeys(s.block for s in SETTINGS))
SETTINGS_BY_PATH: Final[Mapping[str, Setting]] = {s.path: s for s in SETTINGS}


def _assert_registry_consistent() -> None:
    seen_paths: set[str] = set()
    seen_wire: set[tuple[str, str, str]] = set()
    for s in SETTINGS:
        if s.path in seen_paths:
            raise AssertionError(f"duplicate setting path {s.path}")
        seen_paths.add(s.path)
        for b in s.bindings:
            for plat in b.platforms:
                wire_id = (plat, b.feature, b.key)
                if wire_id in seen_wire:
                    raise AssertionError(f"duplicate wire binding {wire_id} on {s.path}")
                seen_wire.add(wire_id)


_assert_registry_consistent()


# ============================================================================
# Doc path helpers
# ============================================================================

_MISSING: Final = object()


def _get_path(doc: Mapping[str, Any], parts: Sequence[str]) -> Any:
    cur: Any = doc
    for part in parts:
        if not isinstance(cur, Mapping) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _set_path(doc: JsonDict, parts: Sequence[str], value: Any) -> None:
    cur: JsonDict = doc
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ============================================================================
# Normalization (declarative doc -> canonical doc)
# ============================================================================


def _err(message: str) -> Err[ConfigValidationError]:
    return Err(ConfigValidationError(message=message, service=SERVICE_NAME))


def _normalize_platforms(raw: Any) -> Result[list[str], ConfigValidationError]:
    if raw is None:
        return Ok(list(VALID_NUVIO_SETTINGS_PLATFORMS))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return _err("'platforms' in nuvio_settings.json must be a list of strings.")
    requested: set[str] = set()
    for item in raw:
        token = str(item).strip().lower() if isinstance(item, str) else ""
        if token not in VALID_NUVIO_SETTINGS_PLATFORMS:
            return _err(
                f"Unsupported Nuvio platform '{item}'. "
                f"Choose from: {', '.join(VALID_NUVIO_SETTINGS_PLATFORMS)}"
            )
        requested.add(token)
    return Ok([p for p in VALID_NUVIO_SETTINGS_PLATFORMS if p in requested])


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lower = raw.strip().lower()
        if lower in ("true", "1", "yes", "on"):
            return True
        if lower in ("false", "0", "no", "off"):
            return False
    return None


def _normalize_language(raw: Any, setting: Setting) -> str | None:
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    if token.lower() in setting.tokens:
        return token.lower()
    m = _LANGUAGE_CODE_RE.match(token)
    if not m:
        return None
    base, region = m.group(1).lower(), m.group(2)
    return f"{base}-{region}" if region else base


def normalize_setting_value(setting: Setting, raw: Any) -> Result[Any, ConfigValidationError]:
    """Validates/coerces one declarative value against its registry row."""
    if raw is _MISSING:
        return Ok(copy.deepcopy(setting.default))
    if raw is None:
        if setting.nullable:
            return Ok(None)
        return _err(f"{setting.path} must not be null.")

    match setting.kind:
        case "bool":
            val = _coerce_bool(raw)
            if val is None:
                return _err(f"{setting.path} must be a boolean.")
            return Ok(val)
        case "int" | "float":
            if not _is_number(raw):
                return _err(f"{setting.path} must be a number.")
            num: int | float = int(raw) if setting.kind == "int" else float(raw)
            if setting.bounds is not None:
                lo, hi = setting.bounds
                if num < lo or num > hi:
                    return _err(f"{setting.path} must be between {lo:g} and {hi:g}.")
            if setting.allowed and num not in setting.allowed:
                return _err(
                    f"{setting.path} must be one of: {', '.join(str(a) for a in setting.allowed)}"
                )
            return Ok(num)
        case "enum":
            if not isinstance(raw, str):
                return _err(f"{setting.path} must be a string.")
            by_lower = {c.lower(): c for c in setting.choices}
            canon = by_lower.get(raw.strip().lower())
            if canon is None:
                return _err(
                    f"Unsupported {setting.path} '{raw}'. Choose from: {', '.join(setting.choices)}"
                )
            return Ok(canon)
        case "string":
            if not isinstance(raw, str):
                return _err(f"{setting.path} must be a string.")
            return Ok(raw.strip())
        case "language":
            lang = _normalize_language(raw, setting)
            if lang is None:
                hint = f" or one of: {', '.join(setting.tokens)}" if setting.tokens else ""
                return _err(f"{setting.path} must be an ISO language code (e.g. 'en'){hint}.")
            return Ok(lang)
        case "color":
            color = normalize_color_hex(raw)
            if color is None:
                return _err(f"{setting.path} must be a '#RRGGBB' or '#AARRGGBB' colour.")
            return Ok(color)
        case "list":
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                return _err(f"{setting.path} must be a list of strings.")
            items: list[str] = []
            for item in raw:
                if not isinstance(item, str) or not item.strip():
                    return _err(f"{setting.path} must be a list of non-empty strings.")
                token = item.strip()
                if setting.choices:
                    by_lower = {c.lower(): c for c in setting.choices}
                    if token.lower() not in by_lower:
                        return _err(
                            f"Unsupported {setting.path} entry '{item}'. "
                            f"Choose from: {', '.join(setting.choices)}"
                        )
                    token = by_lower[token.lower()]
                items.append(token)
            return Ok(sorted(dict.fromkeys(items)))
    return _err(f"{setting.path}: unknown kind {setting.kind}")  # pragma: no cover


def normalize_nuvio_settings_doc(data: Any) -> Result[JsonDict, ConfigValidationError]:
    """
    Validates and canonicalizes `nuvio_settings.json`.

    Any top-level block (`appearance`, `playback`, `audio`, `subtitles`, `tvLayout`,
    `notifications`, `tvPlugins`) that is omitted or `null` is left untouched on every device and
    emitted as `null` in the canonical doc ("you manage what you declare"). Omitted leaf keys
    *inside* a declared block take Nuvio's in-app default.
    """
    if not isinstance(data, Mapping):
        return _err("nuvio_settings.json must be a JSON object.")

    match _normalize_platforms(data.get("platforms")):
        case Err() as err:
            return err
        case Ok(platforms):
            pass

    out: JsonDict = {
        "version": str(data.get("version") or "1.0.0").strip() or "1.0.0",
        "platforms": platforms,
    }
    active_blocks: set[str] = set()
    for block in BLOCKS:
        raw_block = data.get(block)
        if raw_block is None or raw_block is False:
            out[block] = None
            continue
        if not isinstance(raw_block, Mapping):
            return _err(f"'{block}' in nuvio_settings.json must be an object or null.")
        active_blocks.add(block)
        out[block] = {}

    for setting in SETTINGS:
        if setting.block not in active_blocks:
            continue
        match normalize_setting_value(setting, _get_path(data, setting.parts)):
            case Err() as err:
                return err
            case Ok(value):
                _set_path(out, setting.parts, value)
    return Ok(out)


def active_blocks(doc: Mapping[str, Any]) -> frozenset[str]:
    """Blocks a canonical doc manages (everything not explicitly `null`)."""
    return frozenset(b for b in BLOCKS if isinstance(doc.get(b), Mapping))


# ============================================================================
# Compilation (canonical doc -> per-platform feature fragments)
# ============================================================================


def _encode_pref(wire_type: Wire, value: Any) -> JsonDict:
    return {"type": wire_type, "value": value}


def build_platform_settings_features(
    doc: Mapping[str, Any],
    *,
    platform: str,
) -> dict[str, dict[str, Any]]:
    """
    Builds `features.<feature>` fragments for one platform.

    Each fragment maps wire key -> `{type, value}` preference. A `None` value marks a key to be
    *deleted* from the remote blob (nullable settings such as secondary languages), which resets
    the client to its default because every Nuvio client clears the feature before applying a blob.
    """
    blocks = active_blocks(doc)
    out: dict[str, dict[str, Any]] = {}
    for setting in SETTINGS:
        if setting.block not in blocks:
            continue
        value = _get_path(doc, setting.parts)
        if value is _MISSING:
            value = setting.default
        if setting.secret and not value:
            continue  # never blank out a credential the device already has
        for binding in setting.bindings:
            if platform not in binding.platforms:
                continue
            fragment = out.setdefault(binding.feature, {})
            if value is None:
                fragment[binding.key] = None
                continue
            wire = binding.codec.encode(value)
            if wire is OMIT:
                continue
            if binding.feature in _RAW_VALUE_FEATURES:
                fragment[binding.key] = wire
            else:
                fragment[binding.key] = _encode_pref(binding.codec.wire_type, wire)
    return out


def merge_profile_settings_blob_settings(
    remote_settings_json: Mapping[str, Any] | None,
    platform_features: Mapping[str, Mapping[str, Any]],
    *,
    platform: str,
) -> JsonDict | None:
    """
    Pure read-modify-write overlay of settings fragments into a Nuvio profile settings blob.
    Unrelated keys inside each feature (device-local engine/decoder prefs, Trakt pins, …) are
    preserved; declared keys are overwritten and `None` fragments delete the remote key.

    Returns `None` for uninitialized `desktop`/`mobile` blobs: those clients apply a remote blob
    wholesale, so seeding a partial blob would wipe their local preferences.
    """
    if remote_settings_json is None:
        if platform != "tv":
            return None
        seeded: JsonDict = {}
        for feature_name, fragment in platform_features.items():
            seeded[feature_name] = {
                k: copy.deepcopy(v) for k, v in fragment.items() if v is not None
            }
        return {"version": 1, "features": seeded}

    merged: JsonDict = copy.deepcopy(dict(remote_settings_json))
    raw_features = merged.get("features")
    features: JsonDict = (
        copy.deepcopy(dict(raw_features)) if isinstance(raw_features, Mapping) else {}
    )
    for feature_name, fragment in platform_features.items():
        existing = features.get(feature_name)
        base: JsonDict = copy.deepcopy(dict(existing)) if isinstance(existing, Mapping) else {}
        for key, value in fragment.items():
            if value is None:
                base.pop(key, None)
            else:
                base[key] = copy.deepcopy(value)
        features[feature_name] = base
    merged["features"] = features
    merged.setdefault("version", 1 if platform == "tv" else 3)
    return merged


def build_settings_provider_credentials(doc: Mapping[str, Any]) -> list[JsonDict]:
    """`p_credentials` rows for the AnimeSkip client id / IntroDB API key (empty values omitted)."""
    rows: list[JsonDict] = []
    client_id = _get_path(doc, ("playback", "skip", "animeSkip", "clientId"))
    if isinstance(client_id, str) and client_id.strip():
        rows.append(
            {
                "provider": PROVIDER_CREDENTIAL_ANIMESKIP,
                "credential_json": {PROVIDER_CREDENTIAL_CLIENT_ID_FIELD: client_id.strip()},
            }
        )
    introdb_key = _get_path(doc, ("playback", "skip", "introDbApiKey"))
    if isinstance(introdb_key, str) and introdb_key.strip():
        rows.append(
            {
                "provider": PROVIDER_CREDENTIAL_INTRODB,
                "credential_json": {PROVIDER_CREDENTIAL_API_KEY_FIELD: introdb_key.strip()},
            }
        )
    return rows


# ============================================================================
# Decompilation (remote Nuvio blobs -> canonical doc)
# ============================================================================


def _decode_pref(binding: Binding, raw_entry: Any) -> Any:
    """Decodes a `{type, value}` (or raw, for typed features) entry; `None` when unusable."""
    if binding.feature in _RAW_VALUE_FEATURES:
        wire_value = raw_entry
    else:
        if not isinstance(raw_entry, Mapping) or raw_entry.get("type") != binding.codec.wire_type:
            return None
        wire_value = raw_entry.get("value")
    match binding.codec.wire_type:
        case "boolean":
            if not isinstance(wire_value, bool):
                return None
        case "int":
            if isinstance(wire_value, bool) or not isinstance(wire_value, (int, float)):
                return None
            wire_value = int(wire_value)
        case "float":
            if isinstance(wire_value, bool) or not isinstance(wire_value, (int, float)):
                return None
        case "string":
            if not isinstance(wire_value, str):
                return None
        case "string_set":
            if not isinstance(wire_value, list):
                return None
    return binding.codec.decode(wire_value)


def _binding_for(setting: Setting, platform: str) -> Binding | None:
    for b in setting.bindings:
        if platform in b.platforms:
            return b
    return None


@dataclass(frozen=True)
class PlatformView:
    """Canonical view of one platform blob plus the setting paths it explicitly carried."""

    doc: JsonDict
    present: frozenset[str]


def _decode_platform_view(
    blob: Mapping[str, Any] | None,
    *,
    platform: str,
    blocks: frozenset[str],
) -> PlatformView | None:
    """Decodes one platform blob restricted to `blocks`; `None` if the blob is absent."""
    if not isinstance(blob, Mapping):
        return None
    feats = blob.get("features")
    if not isinstance(feats, Mapping):
        return None

    view: JsonDict = {}
    present: set[str] = set()
    for setting in SETTINGS:
        if setting.block not in blocks or setting.secret:
            continue
        binding = _binding_for(setting, platform)
        if binding is None:
            continue
        feature = feats.get(binding.feature)
        decoded: Any = None
        if isinstance(feature, Mapping) and binding.key in feature:
            decoded = _decode_pref(binding, feature[binding.key])
        if decoded is None:
            decoded = None if setting.nullable else copy.deepcopy(setting.default)
        else:
            present.add(setting.path)
        _set_path(view, setting.parts, decoded)
    return PlatformView(view, frozenset(present))


def comparable_platform_view(
    doc: Mapping[str, Any],
    *,
    platform: str,
    blocks: frozenset[str] | None = None,
) -> JsonDict:
    """
    Projects a canonical doc onto what `platform` can represent: settings without a binding on
    that platform are dropped and the rest are round-tripped through the platform codec (e.g.
    desktop cannot express `movie-credits` auto-skip, mobile has fewer themes).
    """
    wanted = active_blocks(doc) if blocks is None else blocks
    view: JsonDict = {}
    for setting in SETTINGS:
        if setting.block not in wanted or setting.secret:
            continue
        binding = _binding_for(setting, platform)
        if binding is None:
            continue
        value = _get_path(doc, setting.parts)
        if value is _MISSING:
            value = setting.default
        projected = None if value is None else binding.codec.project(value)
        if projected is OMIT:
            continue
        _set_path(view, setting.parts, projected)
    return view


def _consensus_value(setting: Setting, views: Mapping[str, PlatformView | None]) -> Any:
    """
    Picks the canonical value for one setting across platform views.

    Candidates are ordered mobile > desktop > tv with platforms that explicitly carry the key
    first. The first candidate whose value, projected through every *other* platform's codec,
    reproduces that platform's view wins — so the most expressive platform's value survives
    (e.g. TV's `movie-credits` auto-skip that desktop can only store as a subset). Falls back to
    the first explicit candidate, then Nuvio's default.
    """
    bound = [
        (plat, view)
        for plat in _CONSENSUS_PRIORITY
        if (view := views.get(plat)) is not None and _binding_for(setting, plat) is not None
    ]
    if not bound:
        return copy.deepcopy(setting.default)
    ordered = [c for c in bound if setting.path in c[1].present] + [
        c for c in bound if setting.path not in c[1].present
    ]
    for plat, view in ordered:
        value = _get_path(view.doc, setting.parts)
        compatible = True
        for other_plat, other_view in bound:
            if other_plat == plat:
                continue
            binding = _binding_for(setting, other_plat)
            assert binding is not None
            projected = None if value is None else binding.codec.project(value)
            if projected is OMIT:
                continue
            if projected != _get_path(other_view.doc, setting.parts):
                compatible = False
                break
        if compatible:
            return copy.deepcopy(value)
    return copy.deepcopy(_get_path(ordered[0][1].doc, setting.parts))


def _leaf_paths(doc: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> frozenset[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for key, value in doc.items():
        path = (*prefix, key)
        if isinstance(value, Mapping):
            out |= _leaf_paths(value, path)
        else:
            out.add(path)
    return frozenset(out)


def _restrict_to_paths(doc: Mapping[str, Any], paths: frozenset[tuple[str, ...]]) -> JsonDict:
    out: JsonDict = {}
    for path in sorted(paths):
        value = _get_path(doc, path)
        if value is not _MISSING:
            _set_path(out, path, value)
    return out


def decompile_nuvio_settings(
    platform_blobs: Mapping[str, Mapping[str, Any] | None],
    credential_rows: Sequence[Mapping[str, Any]] = (),
    *,
    version: str = "1.0.0",
    blocks: Sequence[str] | None = None,
) -> JsonDict:
    """
    Reverse-compiles live Nuvio profile settings blobs into the canonical
    `nuvio_settings.json` shape.

    Each setting's value comes from the highest-priority platform (mobile > desktop > tv) that
    both has a blob and a binding for it, so TV-only knobs come from the TV blob. `platforms`
    lists only the platforms whose own blob matches that consensus, so `sw diff` surfaces drift.
    `blocks` restricts the output to the blocks managed by the local config (others become `null`).
    """
    wanted = frozenset(blocks) if blocks is not None else frozenset(BLOCKS)
    views: dict[str, PlatformView | None] = {
        plat: _decode_platform_view(platform_blobs.get(plat), platform=plat, blocks=wanted)
        for plat in VALID_NUVIO_SETTINGS_PLATFORMS
    }

    # Per setting: first platform (by priority) whose blob explicitly carries the key wins; a
    # platform that merely falls back to its default (e.g. mobile for a desktop-only theme)
    # must not outvote one that has a real value.
    consensus: JsonDict = {}
    for setting in SETTINGS:
        if setting.block not in wanted:
            continue
        if setting.secret:
            _set_path(consensus, setting.parts, "")
            continue
        _set_path(consensus, setting.parts, _consensus_value(setting, views))

    for row in credential_rows:
        provider = str(row.get("provider") or "").strip().lower()
        cred = row.get("credential_json")
        if not isinstance(cred, Mapping):
            continue
        if provider == PROVIDER_CREDENTIAL_ANIMESKIP and "playback" in wanted:
            value = str(cred.get(PROVIDER_CREDENTIAL_CLIENT_ID_FIELD) or "").strip()
            if value:
                _set_path(consensus, ("playback", "skip", "animeSkip", "clientId"), value)
        elif provider == PROVIDER_CREDENTIAL_INTRODB and "playback" in wanted:
            value = str(cred.get(PROVIDER_CREDENTIAL_API_KEY_FIELD) or "").strip()
            if value:
                _set_path(consensus, ("playback", "skip", "introDbApiKey"), value)

    synced_platforms: list[str] = []
    for plat in VALID_NUVIO_SETTINGS_PLATFORMS:
        view = views.get(plat)
        if view is None:
            continue
        expected = comparable_platform_view(consensus, platform=plat, blocks=wanted)
        # A consensus value the platform cannot represent (e.g. a desktop-only theme on mobile)
        # is dropped from `expected`; drop it from the platform's own view too so the device's
        # fallback default does not read as drift.
        actual = _restrict_to_paths(
            comparable_platform_view(view.doc, platform=plat, blocks=wanted),
            _leaf_paths(expected),
        )
        if actual == expected:
            synced_platforms.append(plat)

    doc: JsonDict = {"version": version, "platforms": synced_platforms}
    for block in BLOCKS:
        doc[block] = consensus.get(block) if block in wanted else None
    match normalize_nuvio_settings_doc(doc):
        case Ok(norm):
            return norm
        case Err():
            return doc


def describe_settings_summary(doc: Mapping[str, Any]) -> str:
    """Short human summary used in `sw push` output."""
    blocks = sorted(active_blocks(doc), key=BLOCKS.index)
    count = sum(1 for s in SETTINGS if s.block in blocks)
    reuse = _get_path(doc, ("playback", "reuseLastLink", "enabled"))
    extras = ", reuse-last-link enabled" if reuse is True else ""
    return f"app settings ({count} preferences across {', '.join(blocks)}{extras})"
