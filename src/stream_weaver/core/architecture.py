"""Pure functional LikeC4 (`.c4`) and Markdown (`ARCHITECTURE.md`) topology compiler."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from stream_weaver.core.secrets import SECRET_PLACEHOLDER_RE

_IDENT_CLEAN_RE = re.compile(r"[^a-zA-Z0-9_]+")
_LIKEC4_RESERVED: frozenset[str] = frozenset(
    {
        "specification",
        "model",
        "views",
        "view",
        "dynamic",
        "element",
        "tag",
        "style",
        "include",
        "exclude",
        "of",
        "title",
        "description",
        "technology",
        "link",
        "metadata",
        "shape",
        "color",
        "icon",
        "border",
        "opacity",
        "multiple",
        "group",
        "where",
        "with",
        "navigateTo",
        "autoLayout",
        "TopBottom",
        "LeftRight",
        "BottomTop",
        "RightLeft",
        "this",
        "it",
    }
)


@dataclass(frozen=True, slots=True)
class ArchitectureBundle:
    """Generated LikeC4 and Markdown architecture artifacts."""

    likec4_dsl: str
    likec4_config_json: str
    architecture_markdown: str


@dataclass(frozen=True, slots=True)
class CollectionSnapshot:
    """Normalized Nuvio Home Collection metadata shared by LikeC4 and Markdown compilers."""

    raw_id: str
    c4_id: str
    title: str
    view_mode: str
    folder_titles: tuple[str, ...]
    feeding_addon_ids: tuple[str, ...]
    feeding_addon_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DebridServerSpec:
    """Metadata for an external Debrid/Usenet provider server node."""

    service_key: str
    c4_id: str
    title: str
    description: str
    domain: str
    portal_url: str
    secret_env_key: str


_DEBRID_SERVERS: Mapping[str, DebridServerSpec] = {
    "torbox": DebridServerSpec(
        service_key="torbox",
        c4_id="torbox_srv",
        title="TorBox Cloud & Debrid API",
        description="Cached Debrid Streams, Usenet/Torrent Offloading & Personal Cloud Library",
        domain="api.torbox.app",
        portal_url="https://torbox.app/settings",
        secret_env_key="SECRET_TORBOX_API_KEY",
    ),
    "realdebrid": DebridServerSpec(
        service_key="realdebrid",
        c4_id="realdebrid_srv",
        title="Real-Debrid API",
        description="Cached Debrid Torrent & Hoster Stream Resolution",
        domain="api.real-debrid.com",
        portal_url="https://real-debrid.com/apitoken",
        secret_env_key="SECRET_RD_API_KEY",
    ),
    "alldebrid": DebridServerSpec(
        service_key="alldebrid",
        c4_id="alldebrid_srv",
        title="AllDebrid API",
        description="Cached Multi-Hoster & Magnet Stream Resolution",
        domain="api.alldebrid.com",
        portal_url="https://alldebrid.com/apikeys/",
        secret_env_key="SECRET_ALLDEBRID_API_KEY",
    ),
    "premiumize": DebridServerSpec(
        service_key="premiumize",
        c4_id="premiumize_srv",
        title="Premiumize.me API",
        description="Cloud Torrent, Usenet & Debrid Stream Resolution",
        domain="premiumize.me/api",
        portal_url="https://www.premiumize.me/account",
        secret_env_key="SECRET_PREMIUMIZE_API_KEY",
    ),
    "easynews": DebridServerSpec(
        service_key="easynews",
        c4_id="easynews_srv",
        title="Easynews Usenet HTTP",
        description="Direct Usenet NZB & Video Stream Backbone",
        domain="members.easynews.com",
        portal_url="https://account.easynews.com",
        secret_env_key="SECRET_EASYNEWS_PASSWORD",
    ),
}

_GITHUB_RAW_REPO_RE = re.compile(
    r"https?://raw\.githubusercontent\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/"
)


@dataclass(frozen=True, slots=True)
class TopologySnapshot:
    """Single-pass normalized snapshot of all declarative AIO configs for architecture compilation."""

    aiomanager: Mapping[str, Any]
    aiostreams: Mapping[str, Any]
    aiometadata: Mapping[str, Any]
    collections: tuple[CollectionSnapshot, ...]
    total_collection_folders: int
    as_enabled_services: tuple[Mapping[str, Any], ...]
    active_debrid_specs: tuple[DebridServerSpec, ...]
    as_all_presets: tuple[Mapping[str, Any], ...]
    as_enabled_presets: tuple[Mapping[str, Any], ...]
    group1_ids: frozenset[str]
    group2_ids: frozenset[str]
    group2_cond: str
    g1_presets: tuple[Mapping[str, Any], ...]
    g2_presets: tuple[Mapping[str, Any], ...]
    sub_presets: tuple[Mapping[str, Any], ...]
    am_enabled_catalogs: tuple[Mapping[str, Any], ...]
    am_home_catalogs: tuple[Mapping[str, Any], ...]
    am_cats_by_source: Mapping[str, tuple[Mapping[str, Any], ...]]
    am_movie_provider: str
    am_series_provider: str
    am_anime_provider: str
    am_anime_id_provider: str
    am_active_engines: tuple[str, ...]
    am_gemini_search_on: bool
    am_poster_provider: str
    am_custom_poster_url: str
    all_secrets: tuple[str, ...]
    aiostreams_url: str
    aiometadata_url: str
    has_bingecat: bool
    has_contraband: bool
    contraband_addon_id: str
    contraband_repo_url: str
    contraband_catalogs: tuple[str, ...]
    contraband_preset_fqn: str
    subtitle_languages: tuple[str, ...]
    badge_imports: tuple[str, ...] = ()
    badge_platforms: tuple[str, ...] = ()
    badge_placement: str = "BOTTOM"
    badge_show_in_player: bool = False


_ADDON_DISPLAY_NAMES: Mapping[str, str] = {
    "bingecat": "BingeCat",
    "aiometadata": "AIOMetadata",
    "aiostreams": "AIOStreams",
}


def _safe_id(prefix: str, raw: str) -> str:
    cleaned = _IDENT_CLEAN_RE.sub("_", raw.strip()).strip("_").lower()
    if not cleaned:
        cleaned = "node"
    ident = f"{prefix}_{cleaned}" if prefix else cleaned
    if ident[0].isdigit() or ident in _LIKEC4_RESERVED:
        ident = f"n_{ident}"
    return ident


def _escape_c4_str(val: str) -> str:
    """Safely escapes single-quoted strings for LikeC4 DSL and strips control characters."""
    single_line = " ".join(val.replace("\r", " ").replace("\n", " ").split())
    return single_line.replace("\\", "\\\\").replace("'", "\\'")


def _extract_secret_names(value: Any) -> tuple[str, ...]:
    """Extracts canonical `SECRET_*` variable names from a JSON subtree without exposing raw values."""
    found: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                _walk(k)
                _walk(v)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            for item in node:
                _walk(item)
        elif isinstance(node, str):
            for match in SECRET_PLACEHOLDER_RE.finditer(node):
                found.add(match.group(1))

    _walk(value)
    return tuple(sorted(found))


def _safe_origin(url: str) -> str:
    if not url:
        return ""
    scrubbed = SECRET_PLACEHOLDER_RE.sub("secret", url)
    try:
        parsed = urlparse(scrubbed)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        pass
    return ""


def _discover_github_repo_url(node: Any) -> str:
    """Finds the first `https://github.com/<owner>/<repo>` referenced via `raw.githubusercontent.com`."""
    if isinstance(node, Mapping):
        for v in node.values():
            if found := _discover_github_repo_url(v):
                return found
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for item in node:
            if found := _discover_github_repo_url(item):
                return found
    elif isinstance(node, str):
        m = _GITHUB_RAW_REPO_RE.search(node)
        if m:
            owner, repo = m.group(1), m.group(2)
            if owner.lower() != "vidhin05":
                return f"https://github.com/{owner}/{repo}"
    return ""


def _safe_hostname(url: str) -> str:
    origin = _safe_origin(url)
    if not origin:
        return "configured-endpoint"
    return origin.split("://", 1)[-1]


def _emit_metadata_block(indent: str, meta: Mapping[str, str]) -> list[str]:
    non_empty = {k: v for k, v in meta.items() if v}
    if not non_empty:
        return []
    out = [f"{indent}metadata {{"]
    for k, v in non_empty.items():
        safe_k = _safe_id("", k)
        out.append(f"{indent}  {safe_k} '{_escape_c4_str(v)}'")
    out.append(f"{indent}}}")
    return out


def compile_likec4_config() -> str:
    """Returns the canonical `likec4.config.json` project configuration."""
    cfg = {
        "$schema": "https://likec4.dev/schemas/config.json",
        "name": "stream-weaver-topology",
        "title": "Stream Weaver — AIO Streaming Stack Architecture",
    }
    return json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"


def build_topology_snapshot(
    *,
    aiomanager: Mapping[str, Any],
    aiostreams: Mapping[str, Any],
    aiometadata: Mapping[str, Any],
    nuvio_collections: Mapping[str, Any] | None = None,
    nuvio_badges: Mapping[str, Any] | None = None,
    nuvio_meta: Mapping[str, Any] | None = None,
) -> TopologySnapshot:
    """Pure single-pass normalizer extracting shared topology state from raw service configs."""
    raw_cols = (
        nuvio_collections.get("collections", [])
        if isinstance(nuvio_collections, Mapping)
        else []
    )
    collections: list[CollectionSnapshot] = []
    total_folders = 0
    if isinstance(raw_cols, Sequence):
        for col in raw_cols:
            if not isinstance(col, Mapping):
                continue
            raw_col_id = str(col.get("id") or col.get("title") or "col")
            col_c4_id = _safe_id("", raw_col_id)
            col_title = str(col.get("title") or raw_col_id)
            folders = col.get("folders", [])
            folder_seq = folders if isinstance(folders, Sequence) else ()
            total_folders += len(folder_seq)
            folder_titles = tuple(
                str(f.get("title") or f.get("id"))
                for f in folder_seq
                if isinstance(f, Mapping)
            )
            feeding_ids: set[str] = set()
            for f in folder_seq:
                if not isinstance(f, Mapping):
                    continue
                if f.get("dynamicBingecatLists"):
                    feeding_ids.add("bingecat")
                for src in f.get("sources", []):
                    if not isinstance(src, Mapping):
                        continue
                    aid = str(src.get("addonId") or "").lower()
                    if "aicat" in aid or "bingecat" in aid:
                        feeding_ids.add("bingecat")
                    elif "aio-metadata" in aid or "aiometadata" in aid:
                        feeding_ids.add("aiometadata")
                    elif "aiostreams" in aid:
                        feeding_ids.add("aiostreams")

            sorted_ids = tuple(sorted(feeding_ids))
            sorted_names = tuple(
                sorted(_ADDON_DISPLAY_NAMES.get(fid, fid) for fid in sorted_ids)
            )
            collections.append(
                CollectionSnapshot(
                    raw_id=raw_col_id,
                    c4_id=col_c4_id,
                    title=col_title,
                    view_mode=str(col.get("viewMode", "TABBED_GRID")),
                    folder_titles=folder_titles,
                    feeding_addon_ids=sorted_ids,
                    feeding_addon_names=sorted_names,
                )
            )

    raw_services = aiostreams.get("services", [])
    as_enabled_services = tuple(
        s
        for s in (raw_services if isinstance(raw_services, Sequence) else ())
        if isinstance(s, Mapping) and s.get("enabled", True)
    )
    raw_presets = aiostreams.get("presets", [])
    as_all_presets = tuple(
        p
        for p in (raw_presets if isinstance(raw_presets, Sequence) else ())
        if isinstance(p, Mapping)
    )
    as_enabled_presets = tuple(p for p in as_all_presets if p.get("enabled", True))

    as_groups = aiostreams.get("groups", {})
    groupings = as_groups.get("groupings", []) if isinstance(as_groups, Mapping) else []
    g1_ids: set[str] = set()
    g2_ids: set[str] = set()
    group2_cond = "count(cached(totalStreams)) < 3"
    if isinstance(groupings, Sequence):
        if len(groupings) >= 1 and isinstance(groupings[0], Mapping):
            g1_ids = {str(x) for x in groupings[0].get("addons", [])}
        if len(groupings) >= 2 and isinstance(groupings[1], Mapping):
            g2_ids = {str(x) for x in groupings[1].get("addons", [])}
            group2_cond = str(groupings[1].get("condition") or group2_cond)

    g1_presets: list[Mapping[str, Any]] = []
    g2_presets: list[Mapping[str, Any]] = []
    sub_presets: list[Mapping[str, Any]] = []
    for preset in as_all_presets:
        inst_id = str(preset.get("instanceId") or preset.get("type") or "plugin")
        if inst_id in g1_ids:
            g1_presets.append(preset)
        elif inst_id in g2_ids:
            g2_presets.append(preset)
        else:
            sub_presets.append(preset)

    raw_am_cats = aiometadata.get("catalogs", [])
    am_enabled_catalogs = tuple(
        c
        for c in (raw_am_cats if isinstance(raw_am_cats, Sequence) else ())
        if isinstance(c, Mapping) and c.get("enabled", True)
    )
    am_home_catalogs = tuple(c for c in am_enabled_catalogs if c.get("showInHome", False))

    cats_by_source_mut: dict[str, list[Mapping[str, Any]]] = {}
    for c in am_enabled_catalogs:
        src_key = str(c.get("source") or "other")
        cats_by_source_mut.setdefault(src_key, []).append(c)
    am_cats_by_source = {k: tuple(v) for k, v in cats_by_source_mut.items()}

    am_providers = aiometadata.get("providers", {})
    prov_map = am_providers if isinstance(am_providers, Mapping) else {}
    am_search = aiometadata.get("search", {})
    engine_enabled = (
        am_search.get("engineEnabled", {}) if isinstance(am_search, Mapping) else {}
    )
    eng_map = engine_enabled if isinstance(engine_enabled, Mapping) else {}
    active_engines = tuple(str(k) for k, v in eng_map.items() if v)
    gemini_search_on = bool(eng_map.get("gemini.search", False))

    all_secrets = tuple(
        sorted(
            {
                *_extract_secret_names(aiomanager),
                *_extract_secret_names(aiostreams),
                *_extract_secret_names(aiometadata),
                *(_extract_secret_names(nuvio_collections) if nuvio_collections else ()),
                *(_extract_secret_names(nuvio_badges) if nuvio_badges else ()),
                *(_extract_secret_names(nuvio_meta) if nuvio_meta else ()),
            }
        )
    )

    # Discover active Debrid / Usenet backends dynamically from services & secrets
    debrid_keys: list[str] = []
    for svc in as_enabled_services:
        sid = str(svc.get("id") or "").lower()
        if sid in _DEBRID_SERVERS and sid not in debrid_keys:
            debrid_keys.append(sid)
    for spec in _DEBRID_SERVERS.values():
        if spec.secret_env_key in all_secrets and spec.service_key not in debrid_keys:
            debrid_keys.append(spec.service_key)
    if any(str(p.get("type") or "") == "library" for p in as_all_presets) and not debrid_keys:
        debrid_keys.append("torbox")
    active_debrid_specs = tuple(_DEBRID_SERVERS[k] for k in debrid_keys)

    # Inspect AIOManager addons & AIOStreams presets to discover live URLs & companion addons
    aiostreams_url = ""
    aiometadata_url = ""
    has_bingecat = "SECRET_BINGECAT_TOKEN" in all_secrets or any(
        "bingecat" in c.feeding_addon_ids for c in collections
    )
    has_contraband = False
    contraband_addon_id = "static.contraband"
    contraband_catalogs: list[str] = []

    def _inspect_addon_entry(entry: Mapping[str, Any]) -> None:
        nonlocal aiostreams_url, aiometadata_url, has_bingecat, has_contraband, contraband_addon_id
        raw_mf = entry.get("manifest")
        mf = raw_mf if isinstance(raw_mf, Mapping) else {}
        mid = str(mf.get("id") or entry.get("addonId") or "")
        mname = str(
            (entry.get("metadata", {}) if isinstance(entry.get("metadata"), Mapping) else {}).get(
                "customName"
            )
            or entry.get("name")
            or mf.get("name")
            or ""
        )
        raw_url = str(entry.get("installUrl") or entry.get("transportUrl") or "")
        origin = _safe_origin(raw_url)

        lower_name = mname.lower()
        lower_id = mid.lower()
        if ("aiostreams" in lower_id or lower_name == "aiostreams") and origin and not aiostreams_url:
            aiostreams_url = origin
        elif (
            lower_id == "aio-metadata"
            or lower_id.startswith("addon.aiometadata")
            or lower_name == "aiometadata"
        ) and origin and not aiometadata_url:
            aiometadata_url = origin

        if "aicat" in lower_id or "bingecat" in lower_name:
            has_bingecat = True
        if "contraband" in lower_id or "contraband" in lower_name:
            has_contraband = True
            if mid:
                contraband_addon_id = mid
            cats = mf.get("catalogs")
            if isinstance(cats, Sequence):
                for c in cats:
                    if isinstance(c, Mapping) and c.get("name"):
                        cname = str(c["name"])
                        if cname not in contraband_catalogs:
                            contraband_catalogs.append(cname)

    addons_sec = aiomanager.get("addons")
    if isinstance(addons_sec, Mapping) and isinstance(addons_sec.get("savedAddons"), Sequence):
        for sa in addons_sec["savedAddons"]:
            if isinstance(sa, Mapping):
                _inspect_addon_entry(sa)
    mgr_accs = aiomanager.get("accounts")
    if isinstance(mgr_accs, Sequence):
        for acc in mgr_accs:
            if isinstance(acc, Mapping) and isinstance(acc.get("addons"), Sequence):
                for ad in acc["addons"]:
                    if isinstance(ad, Mapping):
                        _inspect_addon_entry(ad)

    contraband_preset_fqn = ""
    for grp_fqn, plist in (
        ("aiostreams.group1_primary", g1_presets),
        ("aiostreams.group2_fallback", g2_presets),
        ("aiostreams.subtitle_addons", sub_presets),
    ):
        for p in plist:
            p_type = str(p.get("type") or "")
            inst_id = str(p.get("instanceId") or p_type or "plugin")
            opts = p.get("options", {}) if isinstance(p.get("options"), Mapping) else {}
            p_name = str(opts.get("name") or p_type)
            if p_type == "custom" and "contraband" in p_name.lower():
                has_contraband = True
                p_c4_id = _safe_id("preset", f"{inst_id}_{p_name}")
                contraband_preset_fqn = f"{grp_fqn}.{p_c4_id}"

    cat_mods = aiostreams.get("catalogModifications")
    if has_contraband and not contraband_catalogs and isinstance(cat_mods, Sequence):
        for cm in cat_mods:
            if isinstance(cm, Mapping) and str(cm.get("id") or "").startswith("swc"):
                cname = str(cm.get("name") or "")
                if cname and cname not in contraband_catalogs:
                    contraband_catalogs.append(cname)

    contraband_repo_url = (
        _discover_github_repo_url(aiostreams) or _discover_github_repo_url(aiomanager)
        if has_contraband
        else ""
    )

    sub_langs: list[str] = []
    for p in sub_presets:
        opts = p.get("options", {}) if isinstance(p.get("options"), Mapping) else {}
        for lg in opts.get("language", []) if isinstance(opts.get("language"), Sequence) else ():
            s_lg = str(lg)
            if s_lg and s_lg not in sub_langs:
                sub_langs.append(s_lg)
    if not sub_langs:
        for lg in (
            aiostreams.get("requiredLanguages", [])
            if isinstance(aiostreams.get("requiredLanguages"), Sequence)
            else ()
        ):
            s_lg = str(lg)
            if s_lg and s_lg not in sub_langs:
                sub_langs.append(s_lg)

    badge_imports_list: list[str] = []
    badge_platforms_list: list[str] = []
    badge_placement = "BOTTOM"
    badge_show_in_player = False
    if isinstance(nuvio_badges, Mapping):
        badge_placement = str(nuvio_badges.get("badgePlacement") or "BOTTOM").upper()
        badge_show_in_player = bool(nuvio_badges.get("showInPlayer", False))
        raw_b_plats = nuvio_badges.get("platforms", [])
        if isinstance(raw_b_plats, Sequence) and not isinstance(raw_b_plats, (str, bytes)):
            badge_platforms_list = [str(p) for p in raw_b_plats if str(p).strip()]
        raw_b_imps = nuvio_badges.get("imports", [])
        if isinstance(raw_b_imps, Sequence) and not isinstance(raw_b_imps, (str, bytes)):
            for imp in raw_b_imps:
                if isinstance(imp, str) and imp.strip():
                    badge_imports_list.append(imp.strip())
                elif isinstance(imp, Mapping) and imp.get("sourceUrl"):
                    badge_imports_list.append(str(imp["sourceUrl"]).strip())

    return TopologySnapshot(
        aiomanager=aiomanager,
        aiostreams=aiostreams,
        aiometadata=aiometadata,
        collections=tuple(collections),
        total_collection_folders=total_folders,
        as_enabled_services=as_enabled_services,
        active_debrid_specs=active_debrid_specs,
        as_all_presets=as_all_presets,
        as_enabled_presets=as_enabled_presets,
        group1_ids=frozenset(g1_ids),
        group2_ids=frozenset(g2_ids),
        group2_cond=group2_cond,
        g1_presets=tuple(g1_presets),
        g2_presets=tuple(g2_presets),
        sub_presets=tuple(sub_presets),
        am_enabled_catalogs=am_enabled_catalogs,
        am_home_catalogs=am_home_catalogs,
        am_cats_by_source=am_cats_by_source,
        am_movie_provider=str(prov_map.get("movie", "tmdb")),
        am_series_provider=str(prov_map.get("series", "tmdb")),
        am_anime_provider=str(prov_map.get("anime", "tvdb")),
        am_anime_id_provider=str(prov_map.get("anime_id_provider", "kitsu")),
        am_active_engines=active_engines,
        am_gemini_search_on=gemini_search_on,
        am_poster_provider=str(aiometadata.get("posterRatingProvider") or "rpdb"),
        am_custom_poster_url=str(aiometadata.get("customPosterUrlPattern") or ""),
        all_secrets=all_secrets,
        aiostreams_url=aiostreams_url,
        aiometadata_url=aiometadata_url,
        has_bingecat=has_bingecat,
        has_contraband=has_contraband,
        contraband_addon_id=contraband_addon_id,
        contraband_repo_url=contraband_repo_url,
        contraband_catalogs=tuple(contraband_catalogs),
        contraband_preset_fqn=contraband_preset_fqn,
        subtitle_languages=tuple(sub_langs),
        badge_imports=tuple(badge_imports_list),
        badge_platforms=tuple(badge_platforms_list),
        badge_placement=badge_placement,
        badge_show_in_player=badge_show_in_player,
    )


def compile_architecture_bundle(
    *,
    aiomanager: Mapping[str, Any],
    aiostreams: Mapping[str, Any],
    aiometadata: Mapping[str, Any],
    nuvio_collections: Mapping[str, Any] | None = None,
    nuvio_badges: Mapping[str, Any] | None = None,
    nuvio_meta: Mapping[str, Any] | None = None,
) -> ArchitectureBundle:
    """
    Pure function: compiles the declarative JSON configs into:
    1. `architecture/topology.c4` (Full hierarchical C4 model + 10 zoom/dynamic views)
    2. `architecture/likec4.config.json`
    3. `ARCHITECTURE.md` (GitHub-native Mermaid summary + inventory tables)
    """
    snapshot = build_topology_snapshot(
        aiomanager=aiomanager,
        aiostreams=aiostreams,
        aiometadata=aiometadata,
        nuvio_collections=nuvio_collections,
        nuvio_badges=nuvio_badges,
        nuvio_meta=nuvio_meta,
    )
    return ArchitectureBundle(
        likec4_dsl=_build_likec4_dsl(snapshot),
        likec4_config_json=compile_likec4_config(),
        architecture_markdown=_build_architecture_markdown(snapshot),
    )


def _build_likec4_dsl(snap: TopologySnapshot) -> str:
    aiomanager = snap.aiomanager
    aiostreams = snap.aiostreams
    aiometadata = snap.aiometadata
    lines: list[str] = []
    relationships: list[str] = []

    def add_rel(src: str, dst: str, label: str, *, tech: str = "", tag: str = "") -> None:
        safe_lbl = _escape_c4_str(label)
        if not tech and not tag:
            relationships.append(f"  {src} -> {dst} '{safe_lbl}'")
        else:
            relationships.append(f"  {src} -> {dst} '{safe_lbl}' {{")
            if tag:
                relationships.append(f"    #{tag}")
            if tech:
                relationships.append(f"    technology '{_escape_c4_str(tech)}'")
            relationships.append("  }")

    # -------------------------------------------------------------------------
    # 1. Specification
    # -------------------------------------------------------------------------
    lines.extend(
        [
            "// AUTO-GENERATED BY `uv run sw diagram` FROM `configs/*.json` — DO NOT EDIT MANUALLY",
            "",
            "specification {",
            "  element client {",
            "    style {",
            "      shape browser",
            "      color indigo",
            "    }",
            "  }",
            "  element controlPlane {",
            "    style {",
            "      shape component",
            "      color amber",
            "    }",
            "  }",
            "  element aggregator {",
            "    style {",
            "      shape component",
            "      color sky",
            "    }",
            "  }",
            "  element addon {",
            "    style {",
            "      shape component",
            "      color green",
            "    }",
            "  }",
            "  element subsystem {",
            "    style {",
            "      shape rectangle",
            "      color slate",
            "    }",
            "  }",
            "  element plugin {",
            "    style {",
            "      shape rectangle",
            "      color blue",
            "    }",
            "  }",
            "  element externalServer {",
            "    style {",
            "      shape storage",
            "      color secondary",
            "    }",
            "  }",
            "  element secretKey {",
            "    style {",
            "      shape cylinder",
            "      color red",
            "    }",
            "  }",
            "",
            "  tag enabled",
            "  tag disabled",
            "  tag primary",
            "  tag fallback",
            "  tag debrid",
            "  tag ai",
            "  tag metadata",
            "  tag catalog",
            "  tag secret",
            "}",
            "",
            "model {",
            "  // ==========================================================================",
            "  // 1. Streaming Clients & Local GitOps Control Plane",
            "  // ==========================================================================",
            "  controlPlane stream_weaver 'Stream Weaver (sw CLI)' {",
            "    #enabled",
            "    description 'Pure Functional GitOps Control Plane (sw init / pull / push / diff / sanitize / warm / diagram)'",
            "    technology 'Python 3.13+ / uv / AES-256-GCM'",
            "    metadata {",
            "      architecture 'Functional Core, Imperative Shell'",
            "      commands 'sw init | sw pull | sw push | sw diff | sw sanitize | sw warm | sw diagram'",
            "      crypto 'PBKDF2-HMAC-SHA256 + AES-256-GCM'",
            "    }",
            "  }",
            "",
            "  client nuvio 'Nuvio Client' {",
            "    #enabled",
            "    description 'Primary Streaming App with Tabbed Home Collections & Native Sync'",
            "    technology 'Nuvio App (Supabase Sync)'",
            "  }",
            "",
            "  client stremio 'Stremio Client' {",
            "    #enabled",
            "    description 'Stremio Media Center Client synced with Addon Manifests'",
            "    technology 'Stremio Desktop / TV / Web'",
            "    link https://web.stremio.com 'Stremio Web'",
            "  }",
            "",
        ]
    )

    add_rel("stream_weaver", "aiomanager", "Pushes & pulls encrypted Cloud Sync state", tech="PBKDF2 + AES-256-GCM")
    add_rel("stream_weaver", "aiostreams", "Pushes & pulls stream scraper & SEL config", tech="REST /api/v1/user")
    add_rel("stream_weaver", "aiometadata", "Pushes & pulls metadata & catalog config", tech="REST /api/config")
    add_rel("stream_weaver", "nuvio", "Directly syncs compiled Home Collections & Addons", tech="Supabase API")
    add_rel("stream_weaver", "stremio", "Directly reconciles addon collection", tech="api.strem.io")

    # -------------------------------------------------------------------------
    # 2. External Upstream Servers & Cloud APIs (Dynamically Scoped)
    # -------------------------------------------------------------------------
    lines.extend(
        [
            "  // ==========================================================================",
            "  // 2. External Upstream Servers & APIs",
            "  // ==========================================================================",
        ]
    )
    for dspec in snap.active_debrid_specs:
        lines.extend(
            [
                f"  externalServer {dspec.c4_id} '{_escape_c4_str(dspec.title)}' {{",
                "    #debrid",
                f"    description '{_escape_c4_str(dspec.description)}'",
                f"    technology '{_escape_c4_str(dspec.domain)}'",
                f"    link {dspec.portal_url} 'Provider Portal'",
                "  }",
            ]
        )

    gh_desc = (
        "Hosts static Stremio addon manifests & synced SEL/Regex release rules"
        if snap.has_contraband
        else "Hosts synced SEL/Regex release rules"
    )
    gh_link_line = (
        [f"    link {snap.contraband_repo_url} 'Static Addon Repo'"]
        if snap.contraband_repo_url
        else []
    )

    lines.extend(
        [
            "  externalServer tmdb_srv 'TMDB API (The Movie Database)' {",
            "    #metadata",
            "    description 'Primary Movie/Series Metadata, Regional Discover, People & Search'",
            "    technology 'api.themoviedb.org'",
            "    link https://www.themoviedb.org/settings/api 'TMDB API Settings'",
            "  }",
            "  externalServer tvdb_srv 'TheTVDB API v4' {",
            "    #metadata",
            "    description 'Series & Anime Episode Metadata, Collections & Season Mapping'",
            "    technology 'api4.thetvdb.com'",
            "    link https://thetvdb.com/api-information 'TVDB API Portal'",
            "  }",
            "  externalServer gemini_srv 'Google AI Studio (Gemini)' {",
            "    #ai",
            "    description 'LLM Semantic & Natural-Language Search Provider for AIOMetadata'",
            "    technology 'generativelanguage.googleapis.com'",
            "    link https://aistudio.google.com/ 'Google AI Studio'",
            "  }",
            "  externalServer trakt_srv 'Trakt.tv API' {",
            "    #catalog",
            "    description 'Up Next, Personal Watchlists, Trending/Popular Catalogs & Watch Scrobbling'",
            "    technology 'api.trakt.tv'",
            "    link https://trakt.tv 'Trakt.tv'",
            "  }",
            "  externalServer mal_srv 'MyAnimeList (MAL) / Jikan' {",
            "    #catalog",
            "    description 'Anime Airing, Top Rated, Seasonal Schedule & Genre Catalogs'",
            "    technology 'myanimelist.net'",
            "    link https://myanimelist.net 'MyAnimeList'",
            "  }",
            "  externalServer kitsu_srv 'Kitsu Anime API' {",
            "    #metadata",
            "    description 'Canonical Anime ID Resolution & Anime Movie/Series Search'",
            "    technology 'kitsu.io/api'",
            "  }",
            "  externalServer tvmaze_srv 'TVmaze API' {",
            "    #metadata",
            "    description 'TV Show Search & Broadcast Schedule Lookup'",
            "    technology 'api.tvmaze.com'",
            "  }",
            "  externalServer rpdb_srv 'RatingPosterDB (RPDB)' {",
            "    #metadata",
            "    description 'Rating-Badge Overlay Poster Service for Catalogs & Streams'",
            "    technology 'api.ratingposterdb.com'",
            "    link https://ratingposterdb.com 'RPDB'",
            "  }",
            "  externalServer postersplus_srv 'PostersPlus (Slokker)' {",
            "    #metadata",
            "    description 'Multi-Source Frosted Rating Badges (IMDb, RT, Letterboxd, Trakt, MAL, AniList)'",
            "    technology 'postersplus.slokker.cc'",
            "  }",
            "  externalServer github_srv 'GitHub Raw CDN' {",
            "    #catalog",
            f"    description '{_escape_c4_str(gh_desc)}'",
            "    technology 'raw.githubusercontent.com'",
            *gh_link_line,
            "  }",
            "  externalServer tamtaro_srv 'Tamtaro SEL Rule Server' {",
            "    #metadata",
            "    description 'Synced Preferred/Excluded/Included Stream Expressions (PSE/ESE/ISE) & Regex'",
            "    technology 'git.tamtaro.de'",
            "  }",
            "  externalServer opensubtitles_srv 'OpenSubtitles API' {",
            "    #metadata",
            "    description 'Multi-language & AI-Translated Subtitle Provider'",
            "    technology 'opensubtitles.com'",
            "  }",
            "",
        ]
    )

    # -------------------------------------------------------------------------
    # 3. Companion Addons (Dynamically Emitted from Configs)
    # -------------------------------------------------------------------------
    lines.extend(
        [
            "  // ==========================================================================",
            "  // 3. Companion Stremio/Nuvio Addons",
            "  // ==========================================================================",
        ]
    )
    if snap.has_bingecat:
        lines.extend(
            [
                "  addon bingecat 'BingeCat AI Assistant' {",
                "    #enabled #ai #catalog",
                "    description 'Personalized AI Movie & Series Recommendations and Micro-Genre Catalogs'",
                "    technology 'bingecat.com'",
                "    link https://bingecat.com 'BingeCat'",
                "  }",
                "",
            ]
        )
        add_rel("nuvio", "bingecat", "Fetches AI Picks & Because You Watched rows", tech="Stremio Protocol")
        add_rel("stremio", "bingecat", "Fetches AI recommendation catalogs", tech="Stremio Protocol")

    primary_debrid = snap.active_debrid_specs[0] if snap.active_debrid_specs else None
    if snap.has_contraband:
        cb_cats_desc = (
            f" ({', '.join(snap.contraband_catalogs)})" if snap.contraband_catalogs else ""
        )
        cb_link_line = (
            [f"    link {snap.contraband_repo_url} 'GitHub Repository'"]
            if snap.contraband_repo_url
            else []
        )
        lines.extend(
            [
                "  addon contraband 'Contraband Static Addon' {",
                "    #enabled #catalog",
                f"    description 'Curated Static Stremio Addon & Cloud Ingestor{_escape_c4_str(cb_cats_desc)}'",
                f"    technology '{_escape_c4_str(snap.contraband_addon_id)}'",
                *cb_link_line,
                "    metadata {",
                f"      addon_id '{_escape_c4_str(snap.contraband_addon_id)}'",
                "      id_prefixes 'sw_, pa_'",
                "      resources 'catalog, meta, stream'",
                "    }",
                "",
                "    subsystem cb_cli 'Contraband CLI (cb)' {",
                "      #enabled",
                "      description 'Imperative Shell CLI: cb init | cb add | cb sync | cb watch | cb serve | cb publish'",
                "      technology 'Python 3.13+ / uv'",
                "    }",
                "    subsystem debrid_ingestor 'Debrid Queue & Slot Promoter' {",
                "      #enabled #debrid",
                "      description 'Queues magnets, maintains parallel downloads & resolves cloud file indices'",
                "      technology 'DebridProviderProtocol'",
                "    }",
                "    subsystem static_compiler 'Static Manifest, Catalog & Stream Compiler' {",
                "      #enabled #catalog",
                "      description 'Compiles deterministic Stremio JSON files (manifest.json, catalog/, meta/, stream/)'",
                "      technology 'Pure Functional Core'",
                "    }",
            ]
        )
        cat_labels = snap.contraband_catalogs or ("Curated Collections",)
        first_cat_c4 = ""
        for cat_lbl in cat_labels:
            cat_c4 = _safe_id("catalog", cat_lbl)
            if not first_cat_c4:
                first_cat_c4 = cat_c4
            lines.extend(
                [
                    f"    plugin {cat_c4} '{_escape_c4_str(cat_lbl)} Catalog' {{",
                    "      #enabled #catalog",
                    f"      description 'Static Stremio catalog row: {_escape_c4_str(cat_lbl)}'",
                    "      technology 'catalog / meta / stream'",
                    "    }",
                ]
            )
            add_rel("contraband.static_compiler", f"contraband.{cat_c4}", "Emits static catalog/meta/stream JSON")
        lines.extend(["  }", ""])

        add_rel("contraband.cb_cli", "contraband.debrid_ingestor", "Queues magnets & polls active slots (cb add / watch)")
        if primary_debrid is not None:
            add_rel(
                "contraband.debrid_ingestor",
                primary_debrid.c4_id,
                "Ingests magnets & queries cloud file list",
                tech=primary_debrid.secret_env_key,
                tag="debrid",
            )
        add_rel("contraband.cb_cli", "contraband.static_compiler", "Weaves media metadata & episode streams")
        add_rel("contraband.static_compiler", "github_srv", "Publishes static addon tree (cb publish)", tech="Git Push")

    lines.extend(
        [
            "  addon cinemeta 'Cinemeta (Official)' {",
            "    #enabled #metadata",
            "    description 'Canonical IMDb (tt) Metadata Fallback'",
            "    technology 'v3-cinemeta.strem.io'",
            "  }",
            "",
        ]
    )

    add_rel("nuvio", "cinemeta", "Fetches base IMDb (tt) metadata", tech="Stremio Protocol")
    add_rel("stremio", "cinemeta", "Fetches base IMDb (tt) metadata", tech="Stremio Protocol")
    add_rel("nuvio", "aiostreams", "Requests filtered & formatted streams", tech="Stremio Protocol")
    add_rel("stremio", "aiostreams", "Requests filtered & formatted streams", tech="Stremio Protocol")
    add_rel("nuvio", "aiometadata", "Requests catalogs, search & rated posters", tech="Stremio Protocol")
    add_rel("stremio", "aiometadata", "Requests catalogs, search & rated posters", tech="Stremio Protocol")

    # -------------------------------------------------------------------------
    # 4. AIOManager Container & Internal Components (with Nuvio Collections Nesting)
    # -------------------------------------------------------------------------
    mgr_accounts = aiomanager.get("accounts")
    mgr_vault = aiomanager.get("vault")
    mgr_failover = aiomanager.get("failover")

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 4. AIOManager — Addon Registry, Profiles, Vault & Client Reconciler",
            "  // ==========================================================================",
            "  controlPlane aiomanager 'AIOManager' {",
            "    #enabled",
            "    description 'Central Addon Library, Account Profiles, Key Vault, Autopilot Failover & Client Sync'",
            "    technology 'AIOManager v2.0 (Encrypted Cloud Sync)'",
            "",
        ]
    )

    if isinstance(mgr_accounts, Sequence):
        for idx, acc in enumerate(mgr_accounts):
            if not isinstance(acc, Mapping):
                continue
            acc_name = str(acc.get("name") or f"Account {idx + 1}")
            acc_id = _safe_id("acc", acc_name)
            conns = acc.get("connections", [])
            addons = acc.get("addons", [])
            enabled_addons = [
                a for a in addons if isinstance(a, Mapping) and a.get("flags", {}).get("enabled", True)
            ]
            addon_names = [
                str(a.get("metadata", {}).get("customName") or a.get("manifest", {}).get("name") or "Addon")
                for a in enabled_addons
            ]
            lines.extend(
                [
                    f"    subsystem {acc_id} 'Account Profile: {_escape_c4_str(acc_name)}' {{",
                    "      #enabled",
                    f"      description '{len(enabled_addons)} enabled addons synced across {len(conns) if isinstance(conns, Sequence) else 0} client connections'",
                    "      technology 'AIOManager Profile'",
                    *_emit_metadata_block(
                        "      ",
                        {
                            "installed_addons": ", ".join(addon_names),
                            "connections": str(len(conns) if isinstance(conns, Sequence) else 0),
                        },
                    ),
                    "    }",
                ]
            )
            if isinstance(conns, Sequence):
                for conn in conns:
                    if not isinstance(conn, Mapping) or not conn.get("enabled", True):
                        continue
                    plat = str(conn.get("platform") or "client").lower()
                    conn_id = _safe_id("conn", f"{acc_name}_{plat}")
                    caps = ", ".join(str(c) for c in conn.get("capabilities", []))
                    lines.extend(
                        [
                            f"    plugin {conn_id} '{_escape_c4_str(plat.capitalize())} Native Connector' {{",
                            "      #enabled",
                            f"      description 'Syncs capabilities: {_escape_c4_str(caps or 'addons')}'",
                            f"      technology '{_escape_c4_str(str(conn.get('connectionType', 'native')))}'",
                            "    }",
                        ]
                    )
                    add_rel(f"aiomanager.{acc_id}", f"aiomanager.{conn_id}", "Pushes profile state")
                    if plat == "nuvio":
                        add_rel(f"aiomanager.{conn_id}", "nuvio", f"Syncs {caps}", tech="Nuvio Native Sync")
                    elif plat == "stremio":
                        add_rel(f"aiomanager.{conn_id}", "stremio", f"Syncs {caps}", tech="Stremio AuthKey Sync")

            for custom_name in addon_names:
                lower_name = custom_name.lower()
                if "aiometadata" in lower_name:
                    add_rel(f"aiomanager.{acc_id}", "aiometadata", "Manages & monitors addon manifest")
                elif "aiostreams" in lower_name:
                    add_rel(f"aiomanager.{acc_id}", "aiostreams", "Manages & monitors addon manifest")
                elif "bingecat" in lower_name and snap.has_bingecat:
                    add_rel(f"aiomanager.{acc_id}", "bingecat", "Manages & monitors addon manifest")
                elif "cinemeta" in lower_name:
                    add_rel(f"aiomanager.{acc_id}", "cinemeta", "Applies Cinemeta catalog/search overrides")

    if snap.collections:
        col_count = len(snap.collections)
        folder_count = snap.total_collection_folders
        lines.extend(
            [
                "    subsystem nuvio_collections 'Nuvio Home Collections Compiler' {",
                "      #enabled #catalog",
                f"      description '{col_count} Curated Collections ({folder_count} Tabbed Folders) synced to Nuvio'",
                "      technology 'configs/nuvio_collections.json'",
            ]
        )
        for col in snap.collections:
            lines.extend(
                [
                    f"      plugin {col.c4_id} '{_escape_c4_str(col.title)} ({len(col.folder_titles)} folders)' {{",
                    "        #enabled #catalog",
                    f"        description 'Folders: {_escape_c4_str(', '.join(col.folder_titles))}'",
                    f"        technology 'View: {_escape_c4_str(col.view_mode)}'",
                    *_emit_metadata_block(
                        "        ",
                        {
                            "collection_id": col.raw_id,
                            "folder_count": str(len(col.folder_titles)),
                            "folders": ", ".join(col.folder_titles),
                            "upstream_addons": ", ".join(col.feeding_addon_ids),
                        },
                    ),
                    "      }",
                ]
            )
            add_rel(
                f"aiomanager.nuvio_collections.{col.c4_id}",
                "nuvio",
                f"Renders {len(col.folder_titles)} tabbed folders",
            )
            for target_addon in col.feeding_addon_ids:
                if target_addon == "bingecat" and not snap.has_bingecat:
                    continue
                add_rel(
                    f"aiomanager.nuvio_collections.{col.c4_id}",
                    target_addon,
                    f"Pulls catalog rows for {col.title}",
                    tag="catalog",
                )

        lines.append("    }")

    if snap.badge_imports:
        plat_csv = ", ".join(snap.badge_platforms) or "tv, desktop, mobile"
        lines.extend(
            [
                "    subsystem nuvio_badges 'Nuvio Stream Badges Compiler' {",
                "      #enabled",
                f"      description 'Compiles & syncs {len(snap.badge_imports)} badge pack(s) across {plat_csv} (placement: {snap.badge_placement}, player: {snap.badge_show_in_player})'",
                "      technology 'configs/nuvio_badges.json'",
                *_emit_metadata_block(
                    "      ",
                    {
                        "platforms": plat_csv,
                        "badge_placement": snap.badge_placement,
                        "show_in_player": str(snap.badge_show_in_player).lower(),
                        "imports": ", ".join(snap.badge_imports),
                    },
                ),
                "    }",
            ]
        )
        add_rel(
            "aiomanager.nuvio_badges",
            "nuvio",
            f"Syncs Stream & Player Badges ({plat_csv})",
            tech="Supabase Profile Settings RPC",
        )

    if isinstance(mgr_vault, Sequence) and mgr_vault:
        vault_names = [
            str(v.get("customProviderName") or v.get("name") or v.get("provider"))
            for v in mgr_vault
            if isinstance(v, Mapping)
        ]
        lines.extend(
            [
                "    subsystem key_vault 'Encrypted API Key Vault' {",
                "      #enabled #secret",
                f"      description 'Stores {len(vault_names)} tracked provider keys: {_escape_c4_str(', '.join(vault_names))}'",
                "      technology 'AES-256-GCM Vault'",
                "    }",
            ]
        )

    failover_rules = mgr_failover.get("rules", []) if isinstance(mgr_failover, Mapping) else []
    fo_tag = "enabled" if failover_rules else "disabled"
    lines.extend(
        [
            "    subsystem autopilot 'Autopilot Health & Failover Engine' {",
            f"      #{fo_tag}",
            f"      description 'Health checks installed addons ({len(failover_rules)} failover rules active)'",
            "      technology 'AIOManager Autopilot'",
            "    }",
            "  }",
            "",
        ]
    )

    # -------------------------------------------------------------------------
    # 5. AIOStreams Container & Nested Scraper Groups
    # -------------------------------------------------------------------------
    group1_ids = snap.group1_ids
    group2_cond = snap.group2_cond
    g1_presets = snap.g1_presets
    g2_presets = snap.g2_presets
    sub_presets = snap.sub_presets

    pref_res = ", ".join(str(x) for x in aiostreams.get("preferredResolutions", [])[:5])
    req_langs = ", ".join(str(x) for x in aiostreams.get("requiredLanguages", []))
    pref_vis = ", ".join(str(x) for x in aiostreams.get("preferredVisualTags", []))
    dedup_keys = ", ".join(str(x) for x in aiostreams.get("deduplicator", {}).get("keys", []))

    as_host_label = _safe_hostname(snap.aiostreams_url) if snap.aiostreams_url else "Configured Instance"
    as_link_lines = (
        [f"    link {snap.aiostreams_url} 'Live AIOStreams Instance'"]
        if snap.aiostreams_url
        else []
    )
    debrid_summary = (
        ", ".join(d.service_key.capitalize() for d in snap.active_debrid_specs) or "Debrid"
    )

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 5. AIOStreams — Stream Aggregator, Debrid Resolver, SEL Filter & Formatter",
            "  // ==========================================================================",
            "  aggregator aiostreams 'AIOStreams' {",
            "    #enabled",
            f"    description 'Aggregates scrapers in sequential groups, checks {_escape_c4_str(debrid_summary)} cache, filters via SEL/Regex & formats streams'",
            f"    technology 'AIOStreams ({_escape_c4_str(as_host_label)})'",
            *as_link_lines,
            *_emit_metadata_block(
                "    ",
                {
                    "preferred_resolutions": pref_res,
                    "required_languages": req_langs,
                    "preferred_visual_tags": pref_vis,
                    "deduplicator_keys": dedup_keys,
                    "precache_selector": str(aiostreams.get("precacheSelector", "")),
                },
            ),
            "",
            "    subsystem sel_engine 'SEL Filter, Deduplicator & Custom Formatter' {",
            "      #enabled",
            "      description 'Applies synced Stream Expressions, Title/Year/Episode matching & custom stream formatting'",
            "      technology 'Stream Expression Language (SEL)'",
            *_emit_metadata_block(
                "      ",
                {
                    "title_matching_mode": str(aiostreams.get("titleMatching", {}).get("mode", "exact")),
                    "similarity_threshold": str(aiostreams.get("titleMatching", {}).get("similarityThreshold", 0.85)),
                    "poster_service": str(aiostreams.get("posterService", "rpdb")),
                },
            ),
            "    }",
        ]
    )

    add_rel("aiostreams.sel_engine", "tamtaro_srv", "Syncs PSE / ESE / ISE & Excluded Regex rules", tech="HTTPS JSON")
    add_rel("aiostreams.sel_engine", "github_srv", "Syncs ranked Regex & SEL expressions", tech="HTTPS JSON")
    add_rel("aiostreams.sel_engine", "tmdb_srv", "Validates exact title/year/episode matching", tech="SECRET_TMDB_API_KEY")
    add_rel("aiostreams.sel_engine", "tvdb_srv", "Validates series/anime episode matching", tech="SECRET_TVDB_API_KEY")
    if aiostreams.get("posterService") == "rpdb":
        add_rel("aiostreams.sel_engine", "rpdb_srv", "Rewrites catalog posters with rating badges", tech="SECRET_RPDB_API_KEY")

    active_debrid_ids: list[str] = []
    for svc in snap.as_enabled_services:
        svc_raw_id = str(svc.get("id") or "debrid")
        svc_c4_id = _safe_id("svc", svc_raw_id)
        active_debrid_ids.append(svc_c4_id)
        lines.extend(
            [
                f"    subsystem {svc_c4_id} 'Debrid Service: {_escape_c4_str(svc_raw_id.capitalize())}' {{",
                "      #enabled #debrid",
                "      description 'Instant cache verification, magnet/usenet resolution & Precache Next Episode'",
                f"      technology '{_escape_c4_str(svc_raw_id)}'",
                "    }",
            ]
        )
        matched_spec = _DEBRID_SERVERS.get(svc_raw_id.lower())
        if matched_spec is not None:
            add_rel(
                f"aiostreams.{svc_c4_id}",
                matched_spec.c4_id,
                "Checks instant cache & resolves debrid/usenet playback links",
                tech=matched_spec.secret_env_key,
                tag="debrid",
            )

    sub_lang_summary = ", ".join(snap.subtitle_languages[:4]) or "Multi-language"

    def _emit_nested_preset(parent_fqn: str, preset: Mapping[str, Any], group_desc: str) -> None:
        enabled = bool(preset.get("enabled", True))
        inst_id = str(preset.get("instanceId") or preset.get("type") or "plugin")
        p_type = str(preset.get("type") or "addon")
        opts = preset.get("options", {}) if isinstance(preset.get("options"), Mapping) else {}
        p_name = str(opts.get("name") or p_type)
        resources = ", ".join(str(r) for r in opts.get("resources", ["stream"]))
        timeout = str(opts.get("timeout", ""))
        media_types = ", ".join(str(m) for m in opts.get("mediaTypes", []))
        http_provs = ", ".join(str(h) for h in opts.get("httpProviders", []))
        langs = ", ".join(str(lang) for lang in opts.get("language", []))

        p_c4_id = _safe_id("preset", f"{inst_id}_{p_name}")
        full_id = f"{parent_fqn}.{p_c4_id}"
        tag_str = "#enabled" if enabled else "#disabled"

        lines.extend(
            [
                f"      plugin {p_c4_id} '{_escape_c4_str(p_name)} [{_escape_c4_str(inst_id)}]' {{",
                f"        {tag_str}",
                f"        description '{_escape_c4_str(group_desc)} — Resources: {_escape_c4_str(resources)}'",
                f"        technology 'Preset: {_escape_c4_str(p_type)}'",
                *_emit_metadata_block(
                    "        ",
                    {
                        "instance_id": inst_id,
                        "preset_type": p_type,
                        "resources": resources,
                        "timeout_ms": timeout,
                        "media_types": media_types or "all",
                        "http_providers": http_provs,
                        "languages": langs,
                    },
                ),
                "      }",
            ]
        )

        if p_type == "custom" and "contraband" in p_name.lower() and snap.has_contraband:
            add_rel(
                full_id,
                "contraband",
                "Wraps static addon catalog, meta & streams",
                tech="Static Addon Manifest",
            )
        elif p_type == "library" and primary_debrid is not None:
            add_rel(
                full_id,
                primary_debrid.c4_id,
                f"Exposes personal {primary_debrid.service_key.capitalize()} Cloud Library catalog & direct streams",
                tech=primary_debrid.secret_env_key,
            )
        elif "subtitle" in p_type.lower() or "opensubtitles" in p_type.lower():
            add_rel(full_id, "opensubtitles_srv", f"Fetches {sub_lang_summary} subtitles")
        elif inst_id in group1_ids and active_debrid_ids:
            for deb_id in active_debrid_ids:
                add_rel(
                    full_id,
                    f"aiostreams.{deb_id}",
                    "Resolves infoHashes via Debrid cache",
                    tag="debrid",
                )

    g1_names = ", ".join(
        str((p.get("options", {}) if isinstance(p.get("options"), Mapping) else {}).get("name") or p.get("type") or "")
        for p in g1_presets[:5]
    ) or "Primary Scrapers"
    lines.extend(
        [
            "    subsystem group1_primary 'Group 1: Primary Sequential Scrapers (Always Run)' {",
            "      #enabled #primary",
            f"      description 'Executes {len(g1_presets)} primary scrapers in parallel ({_escape_c4_str(g1_names)})'",
            "      technology 'Sequential Group 1 (condition: true)'",
        ]
    )
    for p in g1_presets:
        _emit_nested_preset("aiostreams.group1_primary", p, "Group 1 Primary")
    lines.append("    }")

    lines.extend(
        [
            "    subsystem group2_fallback 'Group 2: Conditional HTTP Fallback Scrapers' {",
            "      #enabled #fallback",
            f"      description 'Triggered only when {_escape_c4_str(group2_cond)} ({len(g2_presets)} HTTP scrapers)'",
            "      technology 'Sequential Group 2 (HTTP Streams)'",
        ]
    )
    for p in g2_presets:
        _emit_nested_preset("aiostreams.group2_fallback", p, "Group 2 Fallback")
    lines.append("    }")

    lines.extend(
        [
            "    subsystem subtitle_addons 'Subtitle Providers' {",
            "      #enabled",
            f"      description '{len(sub_presets)} subtitle addon(s) ({_escape_c4_str(sub_lang_summary)})'",
            "      technology 'OpenSubtitles V3'",
        ]
    )
    for p in sub_presets:
        _emit_nested_preset("aiostreams.subtitle_addons", p, "Subtitles")
    lines.append("    }")

    add_rel("aiostreams.sel_engine", "aiostreams.group1_primary", "1. Queries primary scrapers")
    add_rel(
        "aiostreams.group1_primary",
        "aiostreams.group2_fallback",
        f"2. Fallback if {group2_cond}",
        tag="fallback",
    )

    lines.extend(["  }", ""])

    # -------------------------------------------------------------------------
    # 6. AIOMetadata Container & Internal Engines/Catalogs/Search
    # -------------------------------------------------------------------------
    enabled_catalogs = snap.am_enabled_catalogs
    home_catalogs = snap.am_home_catalogs
    cats_by_source = snap.am_cats_by_source
    movie_prov = snap.am_movie_provider
    series_prov = snap.am_series_provider
    anime_prov = snap.am_anime_provider
    anime_id_prov = snap.am_anime_id_provider
    active_engines = snap.am_active_engines
    gemini_search_on = snap.am_gemini_search_on
    am_poster_provider = snap.am_poster_provider
    am_custom_poster_url = snap.am_custom_poster_url
    gemini_tag = "enabled" if gemini_search_on else "disabled"

    am_host_label = _safe_hostname(snap.aiometadata_url) if snap.aiometadata_url else "Configured Instance"
    am_link_lines = (
        [f"    link {snap.aiometadata_url} 'Live AIOMetadata Instance'"]
        if snap.aiometadata_url
        else []
    )

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 6. AIOMetadata — Multi-Source Metadata, Catalogs, Search & Rating Posters",
            "  // ==========================================================================",
            "  aggregator aiometadata 'AIOMetadata' {",
            "    #enabled #metadata",
            f"    description 'Serves {len(enabled_catalogs)} active catalogs ({len(home_catalogs)} on Home), canonical metadata, search & rating posters'",
            f"    technology 'AIOMetadata ({_escape_c4_str(am_host_label)})'",
            *am_link_lines,
            *_emit_metadata_block(
                "    ",
                {
                    "active_catalogs": str(len(enabled_catalogs)),
                    "home_catalogs": str(len(home_catalogs)),
                    "language": str(aiometadata.get("language", "en-US")),
                    "timezone": str(aiometadata.get("timezone", "UTC")),
                    "trakt_watch_tracking": str(aiometadata.get("traktWatchTracking", True)),
                },
            ),
            "",
            "    subsystem meta_router 'Core Metadata & ID Router' {",
            "      #enabled #metadata",
            f"      description 'Routes Movie ({_escape_c4_str(movie_prov.upper())}), Series ({_escape_c4_str(series_prov.upper())}) & Anime ({_escape_c4_str(anime_prov.upper())} + {_escape_c4_str(anime_id_prov.upper())} IDs)'",
            "      technology 'Provider Router + Redis Cache'",
            *_emit_metadata_block(
                "      ",
                {
                    "movie_provider": movie_prov,
                    "series_provider": series_prov,
                    "anime_provider": anime_prov,
                    "anime_id_provider": anime_id_prov,
                },
            ),
            "    }",
            "",
            "    subsystem search_router 'Multi-Engine Search Dispatcher' {",
            "      #enabled",
            f"      description 'Dispatches queries across {len(active_engines)} enabled search engines'",
            "      technology 'AIOMetadata Search Engine'",
            *_emit_metadata_block(
                "      ",
                {
                    "enabled_engines": ", ".join(active_engines),
                },
            ),
            "    }",
            "",
            "    plugin gemini_search 'Gemini AI Semantic Search' {",
            f"      #{gemini_tag} #ai",
            f"      description 'Natural-language query search via Google AI Studio (Active: {gemini_search_on})'",
            "      technology 'gemini.search (SECRET_GEMINI_API_KEY)'",
            "    }",
            "",
            "    subsystem poster_engine 'Artwork & Rating Poster Engine' {",
            "      #enabled #metadata",
            f"      description 'Generates multi-source rating badge posters (Mode: {_escape_c4_str(am_poster_provider)})'",
            f"      technology '{_escape_c4_str(_safe_hostname(am_custom_poster_url) if am_poster_provider == 'custom' else 'RPDB')}'",
            "    }",
        ]
    )

    add_rel("aiometadata.meta_router", "tmdb_srv", f"Fetches {movie_prov}/{series_prov} metadata & cast", tech="SECRET_TMDB_API_KEY")
    add_rel("aiometadata.meta_router", "tvdb_srv", f"Fetches {anime_prov} episode & season metadata", tech="SECRET_TVDB_API_KEY")
    add_rel("aiometadata.meta_router", "kitsu_srv", f"Maps canonical anime IDs ({anime_id_prov})", tech="Kitsu REST API")

    add_rel("aiometadata.search_router", "tmdb_srv", "Movie & People search (tmdb.search)", tech="SECRET_TMDB_API_KEY")
    add_rel("aiometadata.search_router", "tvdb_srv", "Series & Collection search (tvdb.search)", tech="SECRET_TVDB_API_KEY")
    add_rel("aiometadata.search_router", "kitsu_srv", "Anime Movie & Series search (kitsu.search)")
    add_rel("aiometadata.search_router", "tvmaze_srv", "Broadcast TV search (tvmaze.search)")
    add_rel("aiometadata.search_router", "aiometadata.gemini_search", "Routes AI semantic search queries", tag="ai")
    add_rel("aiometadata.gemini_search", "gemini_srv", "Executes LLM search prompt", tech="SECRET_GEMINI_API_KEY", tag="ai")

    if am_poster_provider == "custom" and "postersplus" in am_custom_poster_url:
        add_rel(
            "aiometadata.poster_engine",
            "postersplus_srv",
            "Renders frosted rating badges (IMDb, RT, Letterboxd, Trakt, MAL)",
            tech="HTTPS URL Template",
        )
    add_rel(
        "aiometadata.poster_engine",
        "rpdb_srv",
        "Fallback / library rating poster overlays",
        tech="SECRET_RPDB_API_KEY",
    )

    source_titles: Mapping[str, tuple[str, str]] = {
        "trakt": ("Trakt.tv Catalogs & Watch Tracking", "trakt_srv"),
        "tmdb": ("TMDB Discover Catalogs", "tmdb_srv"),
        "mal": ("MyAnimeList (MAL) Anime Catalogs", "mal_srv"),
        "streaming": ("Streaming Platform Catalogs", "tmdb_srv"),
    }

    for src_key, cat_objs in sorted(cats_by_source.items()):
        cat_plugin_id = _safe_id("cat_src", src_key)
        title_str, target_srv = source_titles.get(
            src_key,
            (f"{src_key.upper()} Catalogs", "tmdb_srv"),
        )
        home_cnt = sum(1 for c in cat_objs if c.get("showInHome", False))
        cat_names = [
            f"{c.get('name', c.get('id'))} ({c.get('type', '')})"
            for c in cat_objs[:8]
        ]
        sample_labels = ", ".join(str(c.get("name") or c.get("id")) for c in cat_objs[:6])
        lines.extend(
            [
                f"    plugin {cat_plugin_id} '{_escape_c4_str(title_str)} ({len(cat_objs)})' {{",
                "      #enabled #catalog",
                f"      description '{len(cat_objs)} active ({home_cnt} Home): {_escape_c4_str(sample_labels)}'",
                f"      technology 'Source: {_escape_c4_str(src_key)}'",
                *_emit_metadata_block(
                    "      ",
                    {
                        "active_count": str(len(cat_objs)),
                        "home_count": str(home_cnt),
                        "sample_catalogs": ", ".join(cat_names),
                    },
                ),
                "    }",
            ]
        )
        add_rel(f"aiometadata.{cat_plugin_id}", target_srv, f"Fetches {len(cat_objs)} {src_key} catalogs", tag="catalog")
        add_rel(f"aiometadata.{cat_plugin_id}", "aiometadata.poster_engine", "Decorates catalog items with rating posters")

    lines.extend(["  }", ""])

    # -------------------------------------------------------------------------
    # 7. Secret Keys (.env & Vault Credential Blast Radius)
    # -------------------------------------------------------------------------
    all_secrets = snap.all_secrets
    has_vault = isinstance(mgr_vault, Sequence) and bool(mgr_vault)
    active_debrid_c4_ids = {d.c4_id for d in snap.active_debrid_specs}

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 7. Declarative Secrets & API Keys (.env Placeholders)",
            "  // ==========================================================================",
        ]
    )

    secret_targets: dict[str, list[tuple[str, str]]] = {
        "SECRET_TORBOX_API_KEY": [
            ("aiostreams", "Authenticates TorBox Debrid & Cloud Library"),
            *([("contraband", "Authenticates `cb sync` magnet ingestion")] if snap.has_contraband else []),
            *(
                [("torbox_srv", "Bearer API Key")]
                if "torbox_srv" in active_debrid_c4_ids
                else []
            ),
        ],
        "SECRET_RD_API_KEY": [
            ("aiostreams", "Authenticates Real-Debrid stream resolution"),
            *(
                [("realdebrid_srv", "Bearer API Key")]
                if "realdebrid_srv" in active_debrid_c4_ids
                else []
            ),
        ],
        "SECRET_ALLDEBRID_API_KEY": [
            ("aiostreams", "Authenticates AllDebrid stream resolution"),
            *(
                [("alldebrid_srv", "Bearer API Key")]
                if "alldebrid_srv" in active_debrid_c4_ids
                else []
            ),
        ],
        "SECRET_PREMIUMIZE_API_KEY": [
            ("aiostreams", "Authenticates Premiumize stream resolution"),
            *(
                [("premiumize_srv", "API Key")]
                if "premiumize_srv" in active_debrid_c4_ids
                else []
            ),
        ],
        "SECRET_TMDB_API_KEY": [
            ("aiometadata", "Authenticates TMDB metadata, search & catalogs"),
            ("aiostreams", "Authenticates title/year matching"),
            *([("aiomanager.key_vault", "Stored in AIOManager Key Vault")] if has_vault else []),
            ("tmdb_srv", "REST API Key"),
        ],
        "SECRET_TMDB_ACCESS_TOKEN": [
            ("aiostreams", "Authenticates TMDB v4 Bearer read access"),
            ("tmdb_srv", "v4 Read Access Token"),
        ],
        "SECRET_TVDB_API_KEY": [
            ("aiometadata", "Authenticates TVDB series/anime metadata & search"),
            ("aiostreams", "Authenticates TVDB episode matching"),
            *([("aiomanager.key_vault", "Stored in AIOManager Key Vault")] if has_vault else []),
            ("tvdb_srv", "v4 API Key"),
        ],
        "SECRET_GEMINI_API_KEY": [
            ("aiometadata.gemini_search", "Authenticates Gemini AI semantic search"),
            *([("aiomanager.key_vault", "Stored in AIOManager Key Vault")] if has_vault else []),
            ("gemini_srv", "Google AI Studio Key"),
        ],
        "SECRET_RPDB_API_KEY": [
            ("aiometadata.poster_engine", "Authenticates RPDB poster overlays"),
            ("aiostreams.sel_engine", "Authenticates RPDB poster redirect API"),
            *([("aiomanager.key_vault", "Stored in AIOManager Key Vault")] if has_vault else []),
            ("rpdb_srv", "RPDB Tier Key"),
        ],
        "SECRET_TRAKT_TOKEN_ID": [
            ("aiometadata", "Authenticates Trakt Up Next, Watchlist & Scrobbling"),
            ("trakt_srv", "OAuth Token ID"),
        ],
        "SECRET_BINGECAT_TOKEN": [
            ("aiomanager", "Embeds profile token into BingeCat manifest URLs"),
            *([("bingecat", "Profile UUID Token")] if snap.has_bingecat else []),
        ],
        "SECRET_NUVIO_ACCESS_TOKEN": [
            ("aiomanager", "Authenticates Nuvio Supabase addon/collection sync"),
            ("nuvio", "Bearer JWT"),
        ],
        "SECRET_STREMIO_AUTH_KEY": [
            ("aiomanager", "Authenticates Stremio addon collection sync"),
            ("stremio", "Stremio AuthKey"),
        ],
    }

    for sec_name in all_secrets:
        sec_id = _safe_id("sec", sec_name)
        lines.extend(
            [
                f"  secretKey {sec_id} '{_escape_c4_str(sec_name)}' {{",
                "    #secret",
                "    description 'Scrubbed via SecretLens into .env ({{SECRET_*}})'",
                "    technology 'Environment Variable'",
                "  }",
            ]
        )
        for target_node, rel_desc in secret_targets.get(sec_name, []):
            add_rel(sec_id, target_node, rel_desc, tag="secret")

    lines.append("")
    lines.append("  // ==========================================================================")
    lines.append("  // 8. Cross-Component Relationships")
    lines.append("  // ==========================================================================")
    lines.extend(relationships)
    lines.append("}")
    lines.append("")

    # -------------------------------------------------------------------------
    # Views (Dynamically Scoped to Active Addons, Collections & Debrid Servers)
    # -------------------------------------------------------------------------
    debrid_view_includes = [f"      {d.c4_id}," for d in snap.active_debrid_specs]
    bingecat_inc = ["      bingecat,"] if snap.has_bingecat else []
    contraband_inc = ["      contraband,"] if snap.has_contraband else []
    vault_inc = ["      aiomanager.key_vault,"] if has_vault else []

    lines.extend(
        [
            "views {",
            "  // 1. Overarching System Landscape (Level 1-2)",
            "  view index {",
            "    title '1. Overarching System Landscape (End-to-End Stack)'",
            "    description 'High-level map of Clients, Stream Weaver CLI, AIOManager, AIOStreams, AIOMetadata, Companion Addons & Upstream Servers'",
            "    include",
            "      stream_weaver,",
            "      nuvio,",
            "      stremio,",
            "      aiomanager,",
            "      aiostreams,",
            "      aiometadata,",
            *bingecat_inc,
            *contraband_inc,
            "      cinemeta,",
            *debrid_view_includes,
            "      tmdb_srv,",
            "      tvdb_srv,",
            "      gemini_srv,",
            "      trakt_srv,",
            "      mal_srv,",
            "      kitsu_srv,",
            "      rpdb_srv,",
            "      postersplus_srv,",
            "      github_srv,",
            "      tamtaro_srv",
            "    autoLayout LeftRight",
            "  }",
            "",
            "  // 2. AIOManager Drill-Down View",
            "  view aiomanager_view of aiomanager {",
            "    title '2. AIOManager — Accounts, Nuvio Collections, Vault & Client Sync'",
            "    description 'Drill-down into AIOManager showing how profiles, Nuvio collections, and addons connect to Stremio & Nuvio'",
            "    include",
            "      *,",
            "      aiomanager.*,",
            "      stream_weaver,",
            "      nuvio,",
            "      stremio,",
            "      aiostreams,",
            "      aiometadata,",
            *bingecat_inc,
            "      cinemeta",
            "    autoLayout LeftRight",
            "  }",
            "",
        ]
    )

    if snap.collections:
        lines.extend(
            [
                "  // 3. Nuvio Home Collections Drill-Down View",
                "  view nuvio_collections_view of aiomanager.nuvio_collections {",
                "    title '3. Nuvio Home Collections — Curated Categories & Upstream Addon Sources'",
                f"    description 'Drill-down into the {len(snap.collections)} Nuvio Home Collections ({snap.total_collection_folders} Tabbed Folders) and which addons feed each collection'",
                "    include",
                "      *,",
                "      aiomanager.nuvio_collections.*,",
                "      nuvio,",
                *bingecat_inc,
                "      aiometadata,",
                "      aiostreams",
                "    autoLayout LeftRight",
                "  }",
                "",
            ]
        )

    lines.extend(
        [
            "  // 4. AIOStreams Drill-Down View (Nested Scraper Groups)",
            "  view aiostreams_view of aiostreams {",
            "    title '4. AIOStreams — Sequential Scraper Groups, Debrid & SEL Engine'",
            f"    description 'Drill-down into AIOStreams showing nested Group 1 primary scrapers, Group 2 HTTP fallback, Subtitles, {_escape_c4_str(debrid_summary)} cache & rule servers'",
            "    include",
            "      *,",
            "      aiostreams.*,",
            "      aiostreams.group1_primary.*,",
            "      aiostreams.group2_fallback.*,",
            "      aiostreams.subtitle_addons.*,",
            "      nuvio,",
            "      stremio,",
            *contraband_inc,
            *debrid_view_includes,
            "      tmdb_srv,",
            "      tvdb_srv,",
            "      rpdb_srv,",
            "      github_srv,",
            "      tamtaro_srv,",
            "      opensubtitles_srv",
            "    autoLayout LeftRight",
            "  }",
            "",
            "  // 5. AIOMetadata Drill-Down View",
            "  view aiometadata_view of aiometadata {",
            "    title '5. AIOMetadata — Catalogs, Metadata Routers, Gemini Search & Posters'",
            "    description 'Drill-down into AIOMetadata showing how catalogs, search engines, and rating posters connect to TMDB, TVDB, Gemini, Trakt, MAL & PostersPlus'",
            "    include",
            "      *,",
            "      aiometadata.*,",
            "      nuvio,",
            "      stremio,",
            "      tmdb_srv,",
            "      tvdb_srv,",
            "      gemini_srv,",
            "      trakt_srv,",
            "      mal_srv,",
            "      kitsu_srv,",
            "      tvmaze_srv,",
            "      rpdb_srv,",
            "      postersplus_srv",
            "    autoLayout LeftRight",
            "  }",
            "",
        ]
    )

    if snap.has_contraband:
        cb_preset_inc = (
            [f"      {snap.contraband_preset_fqn},"] if snap.contraband_preset_fqn else []
        )
        lines.extend(
            [
                "  // 6. Contraband Static Addon Drill-Down View",
                "  view contraband_view of contraband {",
                "    title '6. Contraband — cb CLI, Debrid Queue & Static Compiler'",
                "    description 'Drill-down into the Contraband static addon compiler and cloud magnet queue pipeline'",
                "    include",
                "      *,",
                "      contraband.*,",
                *debrid_view_includes,
                *cb_preset_inc,
                "      github_srv",
                "    autoLayout LeftRight",
                "  }",
                "",
            ]
        )

    lines.extend(
        [
            "  // 7. Secrets & API Key Blast-Radius View",
            "  view secrets_view {",
            "    title '7. Secrets & API Key Blast-Radius Map (.env -> Services -> Servers)'",
            "    description 'Shows every scrubbed {{SECRET_*}} credential and which internal components and external APIs depend on it'",
            "    include",
            "      element.tag = #secret,",
            "      aiomanager,",
            *vault_inc,
            "      aiostreams,",
            "      aiostreams.sel_engine,",
            "      aiometadata,",
            "      aiometadata.gemini_search,",
            "      aiometadata.poster_engine,",
            *bingecat_inc,
            *contraband_inc,
            "      nuvio,",
            "      stremio,",
            *debrid_view_includes,
            "      tmdb_srv,",
            "      tvdb_srv,",
            "      gemini_srv,",
            "      trakt_srv,",
            "      rpdb_srv",
            "    autoLayout LeftRight",
            "  }",
            "",
        ]
    )

    debrid_flow_step = []
    if active_debrid_ids and primary_debrid is not None:
        debrid_flow_step = [
            f"    aiostreams.{active_debrid_ids[0]} -> {primary_debrid.c4_id} '6. Verifies instant cache & resolves debrid playback links'"
        ]

    lines.extend(
        [
            "  // 8. Dynamic Flow 1: Catalog Browse, Metadata & Sequential Stream Resolution",
            "  dynamic view stream_resolution_flow {",
            "    title '8. Dynamic Flow: Catalog Browse, Metadata & Sequential Stream Resolution'",
            f"    description 'Step-by-step runtime flow when browsing in Nuvio/Stremio and playing a stream via AIOStreams & {_escape_c4_str(debrid_summary)}'",
            "",
            "    nuvio -> aiometadata '1. Requests Home catalog rows'",
            "    aiometadata -> tmdb_srv '2. Fetches catalog items & metadata'",
            "    aiometadata -> aiometadata.poster_engine '3. Attaches rating badge posters'",
            "    nuvio -> aiostreams '4. User clicks Play -> requests streams for IMDb/TMDB ID'",
            "    aiostreams.sel_engine -> aiostreams.group1_primary '5. Triggers Group 1 Primary Scrapers'",
            *debrid_flow_step,
            "    aiostreams.group1_primary -> aiostreams.group2_fallback '7. Conditionally triggers Group 2 HTTP Scrapers when cache threshold is unmet'",
            "    aiostreams.sel_engine -> nuvio '8. Deduplicates, sorts by SEL score/resolution & returns formatted streams'",
            "",
            "    autoLayout TopBottom",
            "  }",
            "",
            "  // 9. Dynamic Flow 2: GitOps Push, Rehydration & Catalog Pre-Warming (sw push / sw warm)",
            "  dynamic view gitops_sync_flow {",
            "    title '9. Dynamic Flow: Stream Weaver GitOps Push, Client Sync & Pre-Warm'",
            "    description 'Step-by-step deployment flow when running `uv run sw push` and `uv run sw warm`'",
            "",
            "    stream_weaver -> aiostreams '1. Injects {{SECRET_*}} keys & pushes configs/aiostreams.json (PUT /api/v1/user)'",
            "    stream_weaver -> aiometadata '2. Injects {{SECRET_*}} keys & pushes configs/aiometadata.json (PUT /api/config)'",
            "    stream_weaver -> aiomanager '3. Rehydrates manifests, compiles Nuvio collections & pushes AES-256-GCM envelope'",
            f"    stream_weaver -> nuvio '4. Directly reconciles Nuvio addons & {len(snap.collections)} tabbed Home Collections'",
            "    stream_weaver -> stremio '5. Directly reconciles Stremio addon collection via AuthKey'",
            f"    stream_weaver -> aiometadata '6. Concurrently pre-warms {len(home_catalogs)}+ Home catalog endpoints (sw warm)'",
            "",
            "    autoLayout TopBottom",
            "  }",
            "",
            "  // 10. Dynamic Flow 3: Multi-Engine & Gemini AI Search",
            "  dynamic view ai_search_flow {",
            "    title '10. Dynamic Flow: Multi-Engine & Gemini AI Semantic Search'",
            "    description 'How search queries in Stremio/Nuvio fan out across TMDB, TVDB, Kitsu, TVmaze & Google AI Studio'",
            "",
            "    nuvio -> aiometadata.search_router '1. Sends user search query'",
            "    aiometadata.search_router -> tmdb_srv '2. Queries Movie & People search (tmdb.search)'",
            "    aiometadata.search_router -> tvdb_srv '3. Queries Series & Collection search (tvdb.search)'",
            "    aiometadata.search_router -> kitsu_srv '4. Queries Anime Movie & Series search (kitsu.search)'",
            "    aiometadata.search_router -> aiometadata.gemini_search '5. Dispatches natural-language query to Gemini search plugin'",
            "    aiometadata.gemini_search -> gemini_srv '6. Resolves semantic recommendations via Google AI Studio'",
            "    aiometadata.poster_engine -> rpdb_srv '7. Decorates search results with rating posters'",
            "",
            "    autoLayout TopBottom",
            "  }",
            "}",
            "",
        ]
    )

    return "\n".join(lines)


def _build_architecture_markdown(snap: TopologySnapshot) -> str:
    """Builds a GitHub-native Markdown architecture overview synchronized with `architecture/topology.c4`."""
    as_presets = snap.as_enabled_presets
    as_services = snap.as_enabled_services
    group1_ids = snap.group1_ids
    group2_ids = snap.group2_ids
    group2_cond = snap.group2_cond
    am_catalogs = snap.am_enabled_catalogs
    am_home_catalogs = snap.am_home_catalogs
    cols_list = snap.collections

    view_names = [
        "`index`",
        "`aiomanager_view`",
        *(["`nuvio_collections_view`"] if cols_list else []),
        "`aiostreams_view`",
        "`aiometadata_view`",
        *(["`contraband_view`"] if snap.has_contraband else []),
        "`secrets_view`",
        "`stream_resolution_flow`",
        "`gitops_sync_flow`",
        "`ai_search_flow`",
    ]
    debrid_label = (
        " / ".join(d.title for d in snap.active_debrid_specs)
        if snap.active_debrid_specs
        else "Debrid Cache API"
    )
    debrid_secret = (
        snap.active_debrid_specs[0].secret_env_key
        if snap.active_debrid_specs
        else "SECRET_DEBRID_API_KEY"
    )

    md_lines: list[str] = [
        "# Stream Weaver — Live System Architecture & Topology",
        "",
        "> **Auto-Generated Architecture-as-Code:** This file and [`architecture/topology.c4`](architecture/topology.c4) are deterministically compiled from [`configs/aiomanager.json`](configs/aiomanager.json), [`configs/aiostreams.json`](configs/aiostreams.json), [`configs/aiometadata.json`](configs/aiometadata.json), and [`configs/nuvio_collections.json`](configs/nuvio_collections.json) via `uv run sw diagram` (and automatically updated on `sw pull`, `sw push`, `sw sanitize`, and `git commit`).",
        "",
        f"## Interactive Drill-Down Explorer (LikeC4 — {len(view_names)} Views)",
        "",
        f"For the interactive, Google-Maps-style zoomable graph with **{len(view_names)} perspectives** ({', '.join(view_names)}):",
        "",
        "```bash",
        "# Regenerate topology from configs/*.json at any time:",
        "uv run sw diagram",
        "",
        "# Launch the interactive LikeC4 web explorer locally:",
        "npx likec4 start architecture",
        "",
        "# Or build a standalone static HTML site (also automated via GitHub Actions -> GitHub Pages):",
        "npx likec4 build architecture -o dist/architecture",
        "```",
        "",
        "*(Tip: You can also install the **LikeC4** extension in VS Code / Cursor / Antigravity and open [`architecture/topology.c4`](architecture/topology.c4) directly in the editor.)*",
        "",
        "---",
        "",
        "## 1. Overarching System Landscape (Level 1–2)",
        "",
        "```mermaid",
        "flowchart LR",
        "    subgraph GitOps[\"🧶 Local GitOps (stream-weaver)\"]",
        "        SW[\"sw CLI\\ninit | pull | push | diff | sanitize | warm | diagram\"]",
        "        ENV[\".env\\n{{SECRET_*}} Vault\"]",
        "        SW --- ENV",
        "    end",
        "",
        "    subgraph Clients[\"📺 Streaming Clients\"]",
        f"        Nuvio[\"Nuvio App\\n({len(cols_list)} Home Collections)\"]",
        "        Stremio[\"Stremio App\"]",
        "    end",
        "",
        "    subgraph Control[\"🎛️ Control Plane\"]",
        "        MGR[\"AIOManager\\nAccounts, Key Vault & Autopilot\"]",
        "    end",
        "",
        "    subgraph Aggregators[\"⚡ Core AIO Aggregators\"]",
        f"        AS[\"AIOStreams\\n({len(as_presets)} Active Plugins)\"]",
        f"        AM[\"AIOMetadata\\n({len(am_catalogs)} Active Catalogs, {len(am_home_catalogs)} Home)\"]",
        *(["        BC[\"BingeCat AI Assistant\"]"] if snap.has_bingecat else []),
        *(
            [
                f"        CB[\"Contraband Static Addon\\n({', '.join(snap.contraband_catalogs) or 'Curated Catalog'})\"]"
            ]
            if snap.has_contraband
            else []
        ),
        "        CM[\"Cinemeta (IMDb Fallback)\"]",
        "    end",
        "",
        "    subgraph Upstream[\"☁️ External Upstream Servers & APIs\"]",
        f"        DEB[(\"{_escape_c4_str(debrid_label)}\")]",
        "        TMDB[(\"TMDB API\\nMeta, Search & Discover\")]",
        "        TVDB[(\"TheTVDB API v4\\nSeries & Anime\")]",
        "        GEM[(\"Google AI Studio\\nGemini Search\")]",
        "        TRAKT[(\"Trakt.tv API\\nWatchlist & Up Next\")]",
        "        MAL[(\"MyAnimeList / Kitsu\\nAnime Catalogs & IDs\")]",
        "        POSTER[(\"Rating Posters\\nRPDB / Custom\")]",
        "        GH[(\"GitHub Raw & Tamtaro\\nManifests & SEL Rules\")]",
        "    end",
        "",
        "    SW ==> MGR & AS & AM",
        "    MGR == \"Native Sync\" ==> Nuvio & Stremio",
        f"    Nuvio & Stremio --> AS & AM{' & BC' if snap.has_bingecat else ''} & CM",
        f"    AS --> {'CB & ' if snap.has_contraband else ''}DEB & TMDB & TVDB & POSTER & GH",
        *(["    CB --> GH & DEB"] if snap.has_contraband else []),
        "    AM --> TMDB & TVDB & GEM & TRAKT & MAL & POSTER",
        "```",
        "",
        "---",
        "",
        "## 2. AIOStreams Internal Pipeline & Upstream Connections",
        "",
        "```mermaid",
        "flowchart LR",
        "    subgraph AIOStreams[\"AIOStreams Internal Pipeline\"]",
        "        SEL[\"SEL Filter, Deduplicator\\n& Custom Formatter\"]",
        "        subgraph G1[\"Group 1: Primary Sequential (condition: true)\"]",
    ]

    contraband_mermaid_node = ""
    for p in as_presets:
        inst_id = str(p.get("instanceId") or "")
        p_type = str(p.get("type") or "")
        opts = p.get("options", {})
        p_name = str(opts.get("name") if isinstance(opts, Mapping) else inst_id)
        if p_type == "custom" and "contraband" in p_name.lower():
            contraband_mermaid_node = f"P_{inst_id}"
        if inst_id in group1_ids:
            md_lines.append(f"            P_{inst_id}[\"{_escape_c4_str(p_name)} ({inst_id})\"]")

    md_lines.extend(
        [
            "        end",
            f"        subgraph G2[\"Group 2: HTTP Fallback ({_escape_c4_str(group2_cond)})\"]",
        ]
    )

    for p in as_presets:
        inst_id = str(p.get("instanceId") or "")
        opts = p.get("options", {})
        p_name = str(opts.get("name") if isinstance(opts, Mapping) else inst_id)
        if inst_id in group2_ids:
            md_lines.append(f"            P_{inst_id}[\"{_escape_c4_str(p_name)} ({inst_id})\"]")

    md_lines.extend(
        [
            "        end",
            "        subgraph Subs[\"Subtitle Addons\"]",
        ]
    )

    for p in as_presets:
        inst_id = str(p.get("instanceId") or "")
        opts = p.get("options", {})
        p_name = str(opts.get("name") if isinstance(opts, Mapping) else inst_id)
        if inst_id not in group1_ids and inst_id not in group2_ids:
            md_lines.append(f"            P_{inst_id}[\"{_escape_c4_str(p_name)} ({inst_id})\"]")

    md_lines.extend(
        [
            "        end",
            "    end",
            "",
            f"    DEB_EXT[(\"{_escape_c4_str(debrid_label)}\\n({debrid_secret})\")]",
            *(["    CB_EXT[(\"Static Addon Manifest\\n(GitHub Raw)\")]"] if contraband_mermaid_node else []),
            "    RULES_EXT[(\"Tamtaro & Ranked\\nSEL / Regex Rules\")]",
            "    META_EXT[(\"TMDB / TVDB / RPDB\")]",
            "",
            "    SEL --> G1",
            f"    G1 -. \"If {_escape_c4_str(group2_cond)}\" .-> G2",
            "    G1 ==> DEB_EXT",
            *([f"    {contraband_mermaid_node} --> CB_EXT"] if contraband_mermaid_node else []),
            "    SEL --> RULES_EXT & META_EXT",
            "```",
        ]
    )

    # Inventory table for AIOStreams presets
    svcs_str = ", ".join(str(s.get("id", "")).capitalize() for s in as_services) or "Debrid"
    md_lines.extend(
        [
            "",
            "### Active AIOStreams Plugins (`configs/aiostreams.json`)",
            "",
            "| ID | Plugin Name | Preset Type | Execution Group | Resources | Upstream Dependency |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for p in as_presets:
        inst_id = str(p.get("instanceId") or "")
        p_type = str(p.get("type") or "")
        opts = p.get("options", {})
        p_name = str(opts.get("name") if isinstance(opts, Mapping) else inst_id)
        resources = (
            ", ".join(str(r) for r in opts.get("resources", ["stream"]))
            if isinstance(opts, Mapping)
            else "stream"
        )
        if inst_id in group1_ids:
            grp = "Group 1 (Primary)"
        elif inst_id in group2_ids:
            grp = f"Group 2 (`{group2_cond}`)"
        else:
            grp = "Subtitles / Utility"

        if p_type == "custom" and "contraband" in p_name.lower():
            upstream = f"Static Manifest + {svcs_str}"
        elif p_type == "library":
            upstream = f"{svcs_str} Cloud Library (`{debrid_secret}`)"
        elif "opensubtitles" in p_type:
            upstream = "OpenSubtitles API"
        elif inst_id in group2_ids:
            upstream = "Direct HTTP Providers"
        else:
            upstream = f"{svcs_str} Cache + Public Indexers"

        md_lines.append(
            f"| `{inst_id}` | **{p_name}** | `{p_type}` | {grp} | `{resources}` | {upstream} |"
        )

    # -------------------------------------------------------------------------
    # 3. AIOMetadata Internal Providers & External APIs
    # -------------------------------------------------------------------------
    cats_by_src = {k: len(v) for k, v in snap.am_cats_by_source.items()}
    home_by_src = {
        k: sum(1 for c in v if c.get("showInHome", False))
        for k, v in snap.am_cats_by_source.items()
    }
    active_search_engines = snap.am_active_engines
    poster_label = (
        f"{_safe_hostname(snap.am_custom_poster_url)} + RPDB"
        if snap.am_poster_provider == "custom" and snap.am_custom_poster_url
        else snap.am_poster_provider.upper()
    )

    md_lines.extend(
        [
            "",
            "---",
            "",
            "## 3. AIOMetadata Internal Engines & External Servers",
            "",
            "```mermaid",
            "flowchart LR",
            "    subgraph AIOMetadata[\"AIOMetadata Internal Engines\"]",
            f"        MR[\"Metadata Router\\nMovie/Series: {snap.am_movie_provider.upper()} | Anime: {snap.am_anime_provider.upper()} + {snap.am_anime_id_provider.upper()}\"]",
            f"        SR[\"Search Engine\\n({len(active_search_engines)} Active Engines{' + Gemini AI' if snap.am_gemini_search_on else ''})\"]",
            f"        CAT_TRAKT[\"Trakt Catalogs\\n({cats_by_src.get('trakt', 0)} active, {home_by_src.get('trakt', 0)} Home)\"]",
            f"        CAT_TMDB[\"TMDB Discover\\n({cats_by_src.get('tmdb', 0)} active, {home_by_src.get('tmdb', 0)} Home)\"]",
            f"        CAT_MAL[\"MAL Anime Catalogs\\n({cats_by_src.get('mal', 0)} active, {home_by_src.get('mal', 0)} Home)\"]",
            f"        CAT_STR[\"Streaming Platforms\\n({cats_by_src.get('streaming', 0)} active, {home_by_src.get('streaming', 0)} Home)\"]",
            f"        ART[\"Poster Engine\\n{_escape_c4_str(poster_label)}\"]",
            "    end",
            "",
            "    TMDB_API[(\"TMDB API\\n(SECRET_TMDB_API_KEY)\")]",
            "    TVDB_API[(\"TheTVDB API\\n(SECRET_TVDB_API_KEY)\")]",
            "    KITSU_API[(\"Kitsu & TVmaze APIs\")]",
            "    GEMINI_API[(\"Google AI Studio\\n(SECRET_GEMINI_API_KEY)\")]",
            "    TRAKT_API[(\"Trakt.tv API\\n(SECRET_TRAKT_TOKEN_ID)\")]",
            "    MAL_API[(\"MyAnimeList API\")]",
            "    POSTER_API[(\"Rating Poster APIs\\n(SECRET_RPDB_API_KEY)\")]",
            "",
            "    MR --> TMDB_API & TVDB_API & KITSU_API",
            "    SR --> TMDB_API & TVDB_API & KITSU_API & GEMINI_API",
            "    CAT_TRAKT --> TRAKT_API",
            "    CAT_TMDB & CAT_STR --> TMDB_API",
            "    CAT_MAL --> MAL_API",
            "    CAT_TRAKT & CAT_TMDB & CAT_MAL & CAT_STR --> ART",
            "    ART --> POSTER_API",
            "```",
            "",
            "---",
            "",
            "## 4. Nuvio Curated Home Collections (`configs/nuvio_collections.json`)",
            "",
            "| Collection ID | Title | Tabbed Folders | Upstream Addons |",
            "| :--- | :--- | :--- | :--- |",
        ]
    )

    for col in cols_list:
        md_lines.append(
            f"| `{col.raw_id}` | **{col.title}** ({len(col.folder_titles)}) | {', '.join(col.folder_titles)} | {', '.join(col.feeding_addon_names)} |"
        )

    if snap.badge_imports:
        plat_csv = ", ".join(f"`{p}`" for p in snap.badge_platforms) or "`tv`, `desktop`, `mobile`"
        md_lines.extend(
            [
                "",
                "---",
                "",
                "## 5. Nuvio Stream & Player Badges (`configs/nuvio_badges.json`)",
                "",
                "| Platforms | Placement | Paused Player Logos | Active Badge Pack URLs |",
                "| :--- | :--- | :--- | :--- |",
                f"| {plat_csv} | `{snap.badge_placement}` | `{'enabled' if snap.badge_show_in_player else 'disabled'}` | {', '.join(f'`{u}`' for u in snap.badge_imports)} |",
            ]
        )

    md_lines.append("")
    return "\n".join(md_lines)
