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
            for v in node.values():
                _walk(v)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            for item in node:
                _walk(item)
        elif isinstance(node, str):
            for match in SECRET_PLACEHOLDER_RE.finditer(node):
                found.add(match.group(1))

    _walk(value)
    return tuple(sorted(found))


def _safe_hostname(url: str) -> str:
    if not url:
        return "configured-endpoint"
    scrubbed = SECRET_PLACEHOLDER_RE.sub("secret", url)
    try:
        parsed = urlparse(scrubbed)
        return parsed.netloc or "configured-endpoint"
    except ValueError:
        return "configured-endpoint"


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


def compile_architecture_bundle(
    *,
    aiomanager: Mapping[str, Any],
    aiostreams: Mapping[str, Any],
    aiometadata: Mapping[str, Any],
    nuvio_collections: Mapping[str, Any] | None = None,
) -> ArchitectureBundle:
    """
    Pure function: compiles the declarative JSON configs into:
    1. `architecture/topology.c4` (Full hierarchical C4 model + 10 zoom/dynamic views)
    2. `architecture/likec4.config.json`
    3. `ARCHITECTURE.md` (GitHub-native Mermaid summary + inventory tables)
    """
    dsl = _build_likec4_dsl(
        aiomanager=aiomanager,
        aiostreams=aiostreams,
        aiometadata=aiometadata,
        nuvio_collections=nuvio_collections,
    )
    md = _build_architecture_markdown(
        aiomanager=aiomanager,
        aiostreams=aiostreams,
        aiometadata=aiometadata,
        nuvio_collections=nuvio_collections,
    )
    return ArchitectureBundle(
        likec4_dsl=dsl,
        likec4_config_json=compile_likec4_config(),
        architecture_markdown=md,
    )


def _build_likec4_dsl(
    *,
    aiomanager: Mapping[str, Any],
    aiostreams: Mapping[str, Any],
    aiometadata: Mapping[str, Any],
    nuvio_collections: Mapping[str, Any] | None,
) -> str:
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
            "    description 'Pure Functional GitOps Control Plane (sw pull / push / sanitize / warm / diagram)'",
            "    technology 'Python 3.14+ / uv / AES-256-GCM'",
            "    link https://github.com/Darshan-AS/stream-weaver 'GitHub Repository'",
            "    metadata {",
            "      architecture 'Functional Core, Imperative Shell'",
            "      commands 'sw pull | sw push | sw sanitize | sw warm | sw diagram'",
            "      crypto 'PBKDF2-HMAC-SHA256 + AES-256-GCM'",
            "    }",
            "  }",
            "",
            "  client nuvio 'Nuvio Client' {",
            "    #enabled",
            "    description 'Primary Streaming App with Tabbed Home Collections, AI Picks & Native Sync'",
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
    # 2. External Upstream Servers & Cloud APIs
    # -------------------------------------------------------------------------
    lines.extend(
        [
            "  // ==========================================================================",
            "  // 2. External Upstream Servers & APIs",
            "  // ==========================================================================",
            "  externalServer torbox_srv 'TorBox Cloud & Debrid API' {",
            "    #debrid",
            "    description 'Cached Debrid Streams, Usenet/Torrent Offloading & Personal Cloud Library'",
            "    technology 'api.torbox.app'",
            "    link https://torbox.app/settings 'TorBox Dashboard'",
            "  }",
            "  externalServer tmdb_srv 'TMDB API (The Movie Database)' {",
            "    #metadata",
            "    description 'Primary Movie/Series Metadata, Regional Discover (HI/KN/JA), People & Search'",
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
            "    description 'Hosts static Contraband Addon manifests & Vidhin05 SEL/Regex release rules'",
            "    technology 'raw.githubusercontent.com'",
            "    link https://github.com/Darshan-AS/aio_contraband 'aio_contraband Repo'",
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
    # 3. Companion Addons (BingeCat, Contraband with Internal Pipeline, Cinemeta)
    # -------------------------------------------------------------------------
    lines.extend(
        [
            "  // ==========================================================================",
            "  // 3. Companion Stremio/Nuvio Addons (including Contraband Internal Pipeline)",
            "  // ==========================================================================",
            "  addon bingecat 'BingeCat AI Assistant' {",
            "    #enabled #ai #catalog",
            "    description 'Personalized AI Movie & Series Recommendations and Micro-Genre Catalogs'",
            "    technology 'bingecat.com'",
            "    link https://bingecat.com 'BingeCat'",
            "  }",
            "",
            "  addon contraband 'Contraband (aio_contraband)' {",
            "    #enabled #catalog",
            "    description 'Curated Static Stremio Addon & TorBox Ingestor for Niche Courses & Lectures (Peterson Academy)'",
            "    technology 'dev.darshanas.contraband'",
            "    link https://github.com/Darshan-AS/aio_contraband 'GitHub Repository'",
            "    metadata {",
            "      addon_id 'dev.darshanas.contraband'",
            "      id_prefixes 'sw_, pa_, aiostreams::library.torbox.torrent.'",
            "      resources 'catalog, meta, stream'",
            "    }",
            "",
            "    subsystem cb_cli 'Contraband CLI (cb)' {",
            "      #enabled",
            "      description 'Imperative Shell CLI: cb add | cb sync | cb watch | cb list | cb publish'",
            "      technology 'Python 3.14+ / uv'",
            "    }",
            "    subsystem torbox_ingestor 'TorBox Magnet Queue & Slot Promoter' {",
            "      #enabled #debrid",
            "      description 'Queues course magnets, maintains 3 parallel downloads & resolves file IDs'",
            "      technology 'TorBox REST Adapter'",
            "    }",
            "    subsystem static_compiler 'Static Manifest, Catalog & Stream Compiler' {",
            "      #enabled #catalog",
            "      description 'Compiles deterministic Stremio JSON files (manifest.json, catalog/, meta/, stream/)'",
            "      technology 'Pure Functional Core'",
            "    }",
            "    plugin peterson_catalog 'Peterson Academy Catalog' {",
            "      #enabled #catalog",
            "      description 'Curated university-style lectures across 8 humanities & science genres'",
            "      technology 'series / peterson_academy'",
            "    }",
            "  }",
            "",
            "  addon cinemeta 'Cinemeta (Official)' {",
            "    #enabled #metadata",
            "    description 'Canonical IMDb (tt) Metadata Fallback (Standard Catalogs & Search Disabled)'",
            "    technology 'v3-cinemeta.strem.io'",
            "  }",
            "",
        ]
    )

    add_rel("contraband.cb_cli", "contraband.torbox_ingestor", "Queues magnets & polls active slots (cb add / watch)")
    add_rel("contraband.torbox_ingestor", "torbox_srv", "Ingests magnets & queries cloud file list", tech="SECRET_TORBOX_API_KEY", tag="debrid")
    add_rel("contraband.cb_cli", "contraband.static_compiler", "Weaves course metadata & episode streams")
    add_rel("contraband.static_compiler", "contraband.peterson_catalog", "Emits static catalog/meta/stream JSON")
    add_rel("contraband.static_compiler", "github_srv", "Publishes static addon tree to main branch (cb publish)", tech="Git Push")
    add_rel("nuvio", "bingecat", "Fetches AI Picks & Because You Watched rows", tech="Stremio Protocol")
    add_rel("stremio", "bingecat", "Fetches AI recommendation catalogs", tech="Stremio Protocol")
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
    cols_list = (
        nuvio_collections.get("collections", [])
        if isinstance(nuvio_collections, Mapping)
        else []
    )

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 4. AIOManager — Addon Registry, Profiles, Vault & Client Reconciler",
            "  // ==========================================================================",
            "  controlPlane aiomanager 'AIOManager' {",
            "    #enabled",
            "    description 'Central Addon Library, Account Profiles, Key Vault, Autopilot Failover & Client Sync'",
            "    technology 'AIOManager v2.0 (aiomanagerfortheweebs.midnightignite.me)'",
            "    link https://aiomanagerfortheweebs.midnightignite.me 'Live AIOManager Instance'",
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
                elif "bingecat" in lower_name:
                    add_rel(f"aiomanager.{acc_id}", "bingecat", "Manages & monitors addon manifest")
                elif "cinemeta" in lower_name:
                    add_rel(f"aiomanager.{acc_id}", "cinemeta", "Applies Cinemeta catalog/search overrides")

    if isinstance(cols_list, Sequence) and cols_list:
        col_count = len(cols_list)
        folder_count = sum(
            len(c.get("folders", [])) for c in cols_list if isinstance(c, Mapping)
        )
        lines.extend(
            [
                "    subsystem nuvio_collections 'Nuvio Home Collections Compiler' {",
                "      #enabled #catalog",
                f"      description '{col_count} Curated Collections ({folder_count} Tabbed Folders) synced to Nuvio'",
                "      technology 'configs/nuvio_collections.json'",
            ]
        )
        for col in cols_list:
            if not isinstance(col, Mapping):
                continue
            raw_col_id = str(col.get("id") or col.get("title") or "col")
            col_c4_id = _safe_id("", raw_col_id)
            col_title = str(col.get("title") or raw_col_id)
            folders = col.get("folders", [])
            folder_titles = [
                str(f.get("title") or f.get("id"))
                for f in (folders if isinstance(folders, Sequence) else ())
                if isinstance(f, Mapping)
            ]
            # Discover which addons feed this collection
            feeding_addons: set[str] = set()
            for f in (folders if isinstance(folders, Sequence) else ()):
                if not isinstance(f, Mapping):
                    continue
                if f.get("dynamicBingecatLists"):
                    feeding_addons.add("bingecat")
                for src in f.get("sources", []):
                    if not isinstance(src, Mapping):
                        continue
                    aid = str(src.get("addonId") or "").lower()
                    if "aicat" in aid or "bingecat" in aid:
                        feeding_addons.add("bingecat")
                    elif "aio-metadata" in aid or "aiometadata" in aid:
                        feeding_addons.add("aiometadata")
                    elif "aiostreams" in aid:
                        feeding_addons.add("aiostreams")

            lines.extend(
                [
                    f"      plugin {col_c4_id} '{_escape_c4_str(col_title)} ({len(folder_titles)} folders)' {{",
                    "        #enabled #catalog",
                    f"        description 'Folders: {_escape_c4_str(', '.join(folder_titles))}'",
                    f"        technology 'View: {_escape_c4_str(str(col.get('viewMode', 'TABBED_GRID')))}'",
                    *_emit_metadata_block(
                        "        ",
                        {
                            "collection_id": raw_col_id,
                            "folder_count": str(len(folder_titles)),
                            "folders": ", ".join(folder_titles),
                            "upstream_addons": ", ".join(sorted(feeding_addons)),
                        },
                    ),
                    "      }",
                ]
            )
            add_rel(f"aiomanager.nuvio_collections.{col_c4_id}", "nuvio", f"Renders {len(folder_titles)} tabbed folders")
            for target_addon in sorted(feeding_addons):
                add_rel(
                    f"aiomanager.nuvio_collections.{col_c4_id}",
                    target_addon,
                    f"Pulls catalog rows for {col_title}",
                    tag="catalog",
                )

        lines.append("    }")

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
    as_services = aiostreams.get("services", [])
    as_presets = aiostreams.get("presets", [])
    as_groups = aiostreams.get("groups", {})
    groupings = as_groups.get("groupings", []) if isinstance(as_groups, Mapping) else []

    group1_ids: set[str] = set()
    group2_ids: set[str] = set()
    group2_cond = "count(cached(totalStreams)) < 3"
    if isinstance(groupings, Sequence):
        if len(groupings) >= 1 and isinstance(groupings[0], Mapping):
            group1_ids = {str(x) for x in groupings[0].get("addons", [])}
        if len(groupings) >= 2 and isinstance(groupings[1], Mapping):
            group2_ids = {str(x) for x in groupings[1].get("addons", [])}
            group2_cond = str(groupings[1].get("condition") or group2_cond)

    pref_res = ", ".join(str(x) for x in aiostreams.get("preferredResolutions", [])[:5])
    req_langs = ", ".join(str(x) for x in aiostreams.get("requiredLanguages", []))
    pref_vis = ", ".join(str(x) for x in aiostreams.get("preferredVisualTags", []))
    dedup_keys = ", ".join(str(x) for x in aiostreams.get("deduplicator", {}).get("keys", []))

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 5. AIOStreams — Stream Aggregator, Debrid Resolver, SEL Filter & Formatter",
            "  // ==========================================================================",
            "  aggregator aiostreams 'AIOStreams' {",
            "    #enabled",
            "    description 'Aggregates scrapers in sequential groups, checks TorBox cache, filters via SEL/Regex & formats streams'",
            "    technology 'AIOStreams v2.36 (aiostreamsfortheweebsstable.midnightignite.me)'",
            "    link https://aiostreamsfortheweebsstable.midnightignite.me 'Live AIOStreams Instance'",
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
            "      description 'Applies synced Tamtaro/Vidhin05 Stream Expressions, Title/Year/Episode matching & custom prism formatting'",
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
    add_rel("aiostreams.sel_engine", "github_srv", "Syncs Vidhin05 English ranked Regex & SEL expressions", tech="HTTPS JSON")
    add_rel("aiostreams.sel_engine", "tmdb_srv", "Validates exact title/year/episode matching", tech="SECRET_TMDB_API_KEY")
    add_rel("aiostreams.sel_engine", "tvdb_srv", "Validates series/anime episode matching", tech="SECRET_TVDB_API_KEY")
    if aiostreams.get("posterService") == "rpdb":
        add_rel("aiostreams.sel_engine", "rpdb_srv", "Rewrites catalog posters with rating badges", tech="SECRET_RPDB_API_KEY")

    active_debrid_ids: list[str] = []
    if isinstance(as_services, Sequence):
        for svc in as_services:
            if not isinstance(svc, Mapping) or not svc.get("enabled", True):
                continue
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
            if svc_raw_id.lower() == "torbox":
                add_rel(
                    f"aiostreams.{svc_c4_id}",
                    "torbox_srv",
                    "Checks instant cache & resolves debrid/usenet playback links",
                    tech="SECRET_TORBOX_API_KEY",
                    tag="debrid",
                )

    # Partition presets into their nested containers: group1_primary, group2_fallback, subtitle_addons
    g1_presets: list[Mapping[str, Any]] = []
    g2_presets: list[Mapping[str, Any]] = []
    sub_presets: list[Mapping[str, Any]] = []

    if isinstance(as_presets, Sequence):
        for preset in as_presets:
            if not isinstance(preset, Mapping):
                continue
            inst_id = str(preset.get("instanceId") or preset.get("type") or "plugin")
            if inst_id in group1_ids:
                g1_presets.append(preset)
            elif inst_id in group2_ids:
                g2_presets.append(preset)
            else:
                sub_presets.append(preset)

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

        if p_type == "custom" and "contraband" in p_name.lower():
            add_rel(
                full_id,
                "contraband",
                "Wraps Peterson Academy catalog, meta & streams",
                tech="GitHub Raw Manifest",
            )
        elif p_type == "library":
            add_rel(
                full_id,
                "torbox_srv",
                "Exposes personal TorBox Cloud Library catalog & direct streams",
                tech="SECRET_TORBOX_API_KEY",
            )
        elif "subtitle" in p_type.lower() or "opensubtitles" in p_type.lower():
            add_rel(full_id, "opensubtitles_srv", "Fetches EN/KN subtitles")
        elif inst_id in group1_ids and active_debrid_ids:
            for deb_id in active_debrid_ids:
                add_rel(
                    full_id,
                    f"aiostreams.{deb_id}",
                    "Resolves infoHashes via Debrid cache",
                    tag="debrid",
                )

    lines.extend(
        [
            "    subsystem group1_primary 'Group 1: Primary Sequential Scrapers (Always Run)' {",
            "      #enabled #primary",
            f"      description 'Executes {len(g1_presets)} primary scrapers in parallel (Contraband, Library, Torrent/Debrid & Anime)'",
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
            f"      description '{len(sub_presets)} multi-language & AI-translated subtitle addons (EN, KN)'",
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
    am_providers = aiometadata.get("providers", {})
    am_search = aiometadata.get("search", {})
    am_catalogs = aiometadata.get("catalogs", [])
    am_poster_provider = str(aiometadata.get("posterRatingProvider") or "rpdb")
    am_custom_poster_url = str(aiometadata.get("customPosterUrlPattern") or "")

    enabled_catalogs = [
        c for c in am_catalogs if isinstance(c, Mapping) and c.get("enabled", True)
    ]
    home_catalogs = [c for c in enabled_catalogs if c.get("showInHome", False)]

    cats_by_source: dict[str, list[Mapping[str, Any]]] = {}
    for c in enabled_catalogs:
        src = str(c.get("source") or "other")
        cats_by_source.setdefault(src, []).append(c)

    movie_prov = str(am_providers.get("movie", "tmdb")) if isinstance(am_providers, Mapping) else "tmdb"
    series_prov = str(am_providers.get("series", "tmdb")) if isinstance(am_providers, Mapping) else "tmdb"
    anime_prov = str(am_providers.get("anime", "tvdb")) if isinstance(am_providers, Mapping) else "tvdb"
    anime_id_prov = (
        str(am_providers.get("anime_id_provider", "kitsu"))
        if isinstance(am_providers, Mapping)
        else "kitsu"
    )

    engine_enabled = (
        am_search.get("engineEnabled", {}) if isinstance(am_search, Mapping) else {}
    )
    active_engines = [
        str(k) for k, v in (engine_enabled.items() if isinstance(engine_enabled, Mapping) else ()) if v
    ]
    gemini_search_on = bool(
        isinstance(engine_enabled, Mapping) and engine_enabled.get("gemini.search", False)
    )
    gemini_tag = "enabled" if gemini_search_on else "disabled"

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 6. AIOMetadata — Multi-Source Metadata, Catalogs, Search & Rating Posters",
            "  // ==========================================================================",
            "  aggregator aiometadata 'AIOMetadata' {",
            "    #enabled #metadata",
            f"    description 'Serves {len(enabled_catalogs)} active catalogs ({len(home_catalogs)} on Home), canonical metadata, search & rating posters'",
            "    technology 'AIOMetadata v3.6 (aiometadatafortheweebs.midnightignite.me)'",
            "    link https://aiometadatafortheweebs.midnightignite.me 'Live AIOMetadata Instance'",
            *_emit_metadata_block(
                "    ",
                {
                    "active_catalogs": str(len(enabled_catalogs)),
                    "home_catalogs": str(len(home_catalogs)),
                    "language": str(aiometadata.get("language", "en-US")),
                    "timezone": str(aiometadata.get("timezone", "Asia/Kolkata")),
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

    source_meta: Mapping[str, tuple[str, str, str]] = {
        "trakt": (
            "Trakt.tv Catalogs & Watch Tracking",
            "trakt_srv",
            "Up Next, Watchlist, Trending, Popular, Calendar & Scrobbling (SECRET_TRAKT_TOKEN_ID)",
        ),
        "tmdb": (
            "TMDB & Regional Discover Catalogs",
            "tmdb_srv",
            "Popular, Top Rated, Hindi, Kannada, Japanese & Documentary Discover (SECRET_TMDB_API_KEY)",
        ),
        "mal": (
            "MyAnimeList (MAL) Anime Catalogs",
            "mal_srv",
            "Airing Now, Popular, Top Rated, Schedule, Genres & Top Movies/Series",
        ),
        "streaming": (
            "Streaming Platform Catalogs (14 Services)",
            "tmdb_srv",
            "Netflix, Prime, JioHotstar, HBO Max, Apple TV+, Disney+, Sony LIV, Zee5, Curiosity, Hulu",
        ),
    }

    for src_key, cat_objs in sorted(cats_by_source.items()):
        cat_plugin_id = _safe_id("cat_src", src_key)
        title_str, target_srv, desc_detail = source_meta.get(
            src_key,
            (f"{src_key.upper()} Catalogs", "tmdb_srv", f"{len(cat_objs)} active catalogs"),
        )
        home_cnt = sum(1 for c in cat_objs if c.get("showInHome", False))
        cat_names = [
            f"{c.get('name', c.get('id'))} ({c.get('type', '')})"
            for c in cat_objs[:8]
        ]
        lines.extend(
            [
                f"    plugin {cat_plugin_id} '{_escape_c4_str(title_str)} ({len(cat_objs)})' {{",
                "      #enabled #catalog",
                f"      description '{len(cat_objs)} active ({home_cnt} Home): {_escape_c4_str(desc_detail)}'",
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
    all_secrets = sorted(
        {
            *_extract_secret_names(aiomanager),
            *_extract_secret_names(aiostreams),
            *_extract_secret_names(aiometadata),
            *(_extract_secret_names(nuvio_collections) if nuvio_collections else ()),
        }
    )

    lines.extend(
        [
            "  // ==========================================================================",
            "  // 7. Declarative Secrets & API Keys (.env Placeholders)",
            "  // ==========================================================================",
        ]
    )

    secret_targets: Mapping[str, list[tuple[str, str]]] = {
        "SECRET_TORBOX_API_KEY": [
            ("aiostreams", "Authenticates TorBox Debrid & Cloud Library"),
            ("contraband", "Authenticates `cb sync` magnet ingestion"),
            ("torbox_srv", "Bearer API Key"),
        ],
        "SECRET_TMDB_API_KEY": [
            ("aiometadata", "Authenticates TMDB metadata, search & regional catalogs"),
            ("aiostreams", "Authenticates title/year matching"),
            ("aiomanager.key_vault", "Stored in AIOManager Key Vault"),
            ("tmdb_srv", "REST API Key"),
        ],
        "SECRET_TMDB_ACCESS_TOKEN": [
            ("aiostreams", "Authenticates TMDB v4 Bearer read access"),
            ("tmdb_srv", "v4 Read Access Token"),
        ],
        "SECRET_TVDB_API_KEY": [
            ("aiometadata", "Authenticates TVDB series/anime metadata & search"),
            ("aiostreams", "Authenticates TVDB episode matching"),
            ("aiomanager.key_vault", "Stored in AIOManager Key Vault"),
            ("tvdb_srv", "v4 API Key"),
        ],
        "SECRET_GEMINI_API_KEY": [
            ("aiometadata.gemini_search", "Authenticates Gemini AI semantic search"),
            ("aiomanager.key_vault", "Stored in AIOManager Key Vault"),
            ("gemini_srv", "Google AI Studio Key"),
        ],
        "SECRET_RPDB_API_KEY": [
            ("aiometadata.poster_engine", "Authenticates RPDB poster overlays"),
            ("aiostreams.sel_engine", "Authenticates RPDB poster redirect API"),
            ("aiomanager.key_vault", "Stored in AIOManager Key Vault"),
            ("rpdb_srv", "RPDB Tier Key"),
        ],
        "SECRET_TRAKT_TOKEN_ID": [
            ("aiometadata", "Authenticates Trakt Up Next, Watchlist & Scrobbling"),
            ("trakt_srv", "OAuth Token ID"),
        ],
        "SECRET_BINGECAT_TOKEN": [
            ("aiomanager", "Embeds profile token into BingeCat manifest URLs"),
            ("bingecat", "Profile UUID Token"),
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
    # Views (C4 Hierarchy + Nuvio Collections + Contraband + Blast-Radius + 3 Dynamic Flows)
    # -------------------------------------------------------------------------
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
            "      bingecat,",
            "      contraband,",
            "      cinemeta,",
            "      torbox_srv,",
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
            "      bingecat,",
            "      cinemeta",
            "    autoLayout LeftRight",
            "  }",
            "",
            "  // 3. Nuvio Home Collections Drill-Down View",
            "  view nuvio_collections_view of aiomanager.nuvio_collections {",
            "    title '3. Nuvio Home Collections — Curated Categories & Upstream Addon Sources'",
            "    description 'Drill-down into the 5 Nuvio Home Collections (24 Tabbed Folders) and which addons feed each collection'",
            "    include",
            "      *,",
            "      aiomanager.nuvio_collections.*,",
            "      nuvio,",
            "      bingecat,",
            "      aiometadata,",
            "      aiostreams",
            "    autoLayout LeftRight",
            "  }",
            "",
            "  // 4. AIOStreams Drill-Down View (Nested Scraper Groups)",
            "  view aiostreams_view of aiostreams {",
            "    title '4. AIOStreams — Sequential Scraper Groups, Debrid & SEL Engine'",
            "    description 'Drill-down into AIOStreams showing nested Group 1 primary scrapers, Group 2 HTTP fallback, Subtitles, TorBox cache & rule servers'",
            "    include",
            "      *,",
            "      aiostreams.*,",
            "      aiostreams.group1_primary.*,",
            "      aiostreams.group2_fallback.*,",
            "      aiostreams.subtitle_addons.*,",
            "      nuvio,",
            "      stremio,",
            "      contraband,",
            "      torbox_srv,",
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
            "  // 6. Contraband (aio_contraband) Drill-Down View",
            "  view contraband_view of contraband {",
            "    title '6. Contraband (aio_contraband) — cb CLI, TorBox Queue & Static Compiler'",
            "    description 'Drill-down into the Contraband static addon compiler and TorBox magnet queue pipeline'",
            "    include",
            "      *,",
            "      contraband.*,",
            "      torbox_srv,",
            "      github_srv,",
            "      aiostreams.group1_primary.preset_swc_contraband",
            "    autoLayout LeftRight",
            "  }",
            "",
            "  // 7. Secrets & API Key Blast-Radius View",
            "  view secrets_view {",
            "    title '7. Secrets & API Key Blast-Radius Map (.env -> Services -> Servers)'",
            "    description 'Shows every scrubbed {{SECRET_*}} credential and which internal components and external APIs depend on it'",
            "    include",
            "      element.tag = #secret,",
            "      aiomanager,",
            "      aiomanager.key_vault,",
            "      aiostreams,",
            "      aiostreams.sel_engine,",
            "      aiometadata,",
            "      aiometadata.gemini_search,",
            "      aiometadata.poster_engine,",
            "      bingecat,",
            "      contraband,",
            "      nuvio,",
            "      stremio,",
            "      torbox_srv,",
            "      tmdb_srv,",
            "      tvdb_srv,",
            "      gemini_srv,",
            "      trakt_srv,",
            "      rpdb_srv",
            "    autoLayout LeftRight",
            "  }",
            "",
            "  // 8. Dynamic Flow 1: Catalog Browse, Metadata & Sequential Stream Resolution",
            "  dynamic view stream_resolution_flow {",
            "    title '8. Dynamic Flow: Catalog Browse, Metadata & Sequential Stream Resolution'",
            "    description 'Step-by-step runtime flow when browsing in Nuvio/Stremio and playing a stream via AIOStreams & TorBox'",
            "",
            "    nuvio -> aiometadata '1. Requests Home catalog rows (Trakt, TMDB Regional, MAL, Streaming)'",
            "    aiometadata -> tmdb_srv '2. Fetches catalog items & metadata'",
            "    aiometadata -> postersplus_srv '3. Attaches frosted multi-source rating posters'",
            "    nuvio -> aiostreams '4. User clicks Play -> requests streams for IMDb/TMDB ID'",
            "    aiostreams.sel_engine -> aiostreams.group1_primary '5. Triggers Group 1 Primary Scrapers (Contraband, Torrentio, Comet, StremThru, MediaFusion, SeaDex)'",
            "    aiostreams.svc_torbox -> torbox_srv '6. Verifies instant cache & resolves debrid playback links'",
            "    aiostreams.group1_primary -> aiostreams.group2_fallback '7. Conditionally triggers Group 2 HTTP Scrapers (HD Hub, Sootio, WebStreamr) only if cached < 3'",
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
            "    stream_weaver -> nuvio '4. Directly reconciles Nuvio addons & 5 tabbed Home Collections'",
            "    stream_weaver -> stremio '5. Directly reconciles Stremio addon collection via AuthKey'",
            "    stream_weaver -> aiometadata '6. Concurrently pre-warms all 48+ Home catalog endpoints (sw warm)'",
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
            "    aiometadata.poster_engine -> postersplus_srv '7. Decorates search results with frosted rating posters'",
            "",
            "    autoLayout TopBottom",
            "  }",
            "}",
            "",
        ]
    )

    return "\n".join(lines)


def _build_architecture_markdown(
    *,
    aiomanager: Mapping[str, Any],
    aiostreams: Mapping[str, Any],
    aiometadata: Mapping[str, Any],
    nuvio_collections: Mapping[str, Any] | None,
) -> str:
    """Builds a GitHub-native Markdown architecture overview synchronized with `architecture/topology.c4`."""
    as_presets = [
        p for p in aiostreams.get("presets", []) if isinstance(p, Mapping) and p.get("enabled", True)
    ]
    as_services = [
        s for s in aiostreams.get("services", []) if isinstance(s, Mapping) and s.get("enabled", True)
    ]
    as_groups = aiostreams.get("groups", {})
    groupings = as_groups.get("groupings", []) if isinstance(as_groups, Mapping) else []
    group1_ids = (
        {str(x) for x in groupings[0].get("addons", [])}
        if isinstance(groupings, Sequence) and len(groupings) >= 1 and isinstance(groupings[0], Mapping)
        else set()
    )
    group2_ids = (
        {str(x) for x in groupings[1].get("addons", [])}
        if isinstance(groupings, Sequence) and len(groupings) >= 2 and isinstance(groupings[1], Mapping)
        else set()
    )
    group2_cond = (
        str(groupings[1].get("condition", "count(cached(totalStreams)) < 3"))
        if isinstance(groupings, Sequence) and len(groupings) >= 2 and isinstance(groupings[1], Mapping)
        else "count(cached(totalStreams)) < 3"
    )

    am_catalogs = [
        c for c in aiometadata.get("catalogs", []) if isinstance(c, Mapping) and c.get("enabled", True)
    ]
    am_home_catalogs = [c for c in am_catalogs if c.get("showInHome", False)]
    am_search = aiometadata.get("search", {})
    engine_enabled = (
        am_search.get("engineEnabled", {}) if isinstance(am_search, Mapping) else {}
    )
    cols_list = (
        nuvio_collections.get("collections", [])
        if isinstance(nuvio_collections, Mapping)
        else []
    )

    md_lines: list[str] = [
        "# Stream Weaver — Live System Architecture & Topology",
        "",
        "> **Auto-Generated Architecture-as-Code:** This file and [`architecture/topology.c4`](architecture/topology.c4) are deterministically compiled from [`configs/aiomanager.json`](configs/aiomanager.json), [`configs/aiostreams.json`](configs/aiostreams.json), [`configs/aiometadata.json`](configs/aiometadata.json), and [`configs/nuvio_collections.json`](configs/nuvio_collections.json) via `uv run sw diagram` (and automatically updated on `sw pull`, `sw push`, `sw sanitize`, and `git commit`).",
        "",
        "## Interactive Drill-Down Explorer (LikeC4 — 10 Views)",
        "",
        "For the interactive, Google-Maps-style zoomable graph with **10 perspectives** (`index`, `aiomanager_view`, `nuvio_collections_view`, `aiostreams_view`, `aiometadata_view`, `contraband_view`, `secrets_view`, `stream_resolution_flow`, `gitops_sync_flow`, and `ai_search_flow`):",
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
        "        SW[\"sw CLI\\npull | push | sanitize | warm | diagram\"]",
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
        "        BC[\"BingeCat AI Assistant\"]",
        "        CB[\"Contraband (aio_contraband)\\nPeterson Academy\"]",
        "        CM[\"Cinemeta (IMDb Fallback)\"]",
        "    end",
        "",
        "    subgraph Upstream[\"☁️ External Upstream Servers & APIs\"]",
        "        TB[(\"TorBox API\\nDebrid & Cloud\")]",
        "        TMDB[(\"TMDB API\\nMeta, Search & Discover\")]",
        "        TVDB[(\"TheTVDB API v4\\nSeries & Anime\")]",
        "        GEM[(\"Google AI Studio\\nGemini Search\")]",
        "        TRAKT[(\"Trakt.tv API\\nWatchlist & Up Next\")]",
        "        MAL[(\"MyAnimeList / Kitsu\\nAnime Catalogs & IDs\")]",
        "        POSTER[(\"PostersPlus & RPDB\\nRating Posters\")]",
        "        GH[(\"GitHub Raw & Tamtaro\\nManifests & SEL Rules\")]",
        "    end",
        "",
        "    SW ==> MGR & AS & AM",
        "    MGR == \"Native Sync\" ==> Nuvio & Stremio",
        "    Nuvio & Stremio --> AS & AM & BC & CM",
        "    AS --> CB & TB & TMDB & TVDB & POSTER & GH",
        "    CB --> GH & TB",
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

    for p in as_presets:
        inst_id = str(p.get("instanceId") or "")
        opts = p.get("options", {})
        p_name = str(opts.get("name") if isinstance(opts, Mapping) else inst_id)
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
            "    TB_EXT[(\"TorBox Debrid API\\n(SECRET_TORBOX_API_KEY)\")]",
            "    CB_EXT[(\"Contraband GitHub Raw\")]",
            "    RULES_EXT[(\"Tamtaro & Vidhin05\\nSEL / Regex Rules\")]",
            "    META_EXT[(\"TMDB / TVDB / RPDB\")]",
            "",
            "    SEL --> G1",
            "    G1 -. \"If cached < 3\" .-> G2",
            "    G1 ==> TB_EXT",
            "    P_swc --> CB_EXT",
            "    SEL --> RULES_EXT & META_EXT",
            "```",
        ]
    )

    # Inventory table for AIOStreams presets
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
            upstream = "GitHub Raw (`aio_contraband`) + TorBox"
        elif p_type == "library":
            upstream = "TorBox Cloud Library (`SECRET_TORBOX_API_KEY`)"
        elif "opensubtitles" in p_type:
            upstream = "OpenSubtitles API"
        elif inst_id in group2_ids:
            upstream = "Direct HTTP Providers"
        else:
            svcs_str = ", ".join(str(s.get("id", "")).capitalize() for s in as_services) or "Debrid"
            upstream = f"{svcs_str} Cache + Public Indexers"

        md_lines.append(
            f"| `{inst_id}` | **{p_name}** | `{p_type}` | {grp} | `{resources}` | {upstream} |"
        )

    # -------------------------------------------------------------------------
    # 3. AIOMetadata Internal Providers & External APIs
    # -------------------------------------------------------------------------
    cats_by_src: dict[str, int] = {}
    home_by_src: dict[str, int] = {}
    for c in am_catalogs:
        s = str(c.get("source") or "other")
        cats_by_src[s] = cats_by_src.get(s, 0) + 1
        if c.get("showInHome", False):
            home_by_src[s] = home_by_src.get(s, 0) + 1

    active_search_engines = [
        k for k, v in (engine_enabled.items() if isinstance(engine_enabled, Mapping) else ()) if v
    ]

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
            "        MR[\"Metadata Router\\nMovie/Series: TMDB | Anime: TVDB + Kitsu\"]",
            f"        SR[\"Search Engine\\n({len(active_search_engines)} Active Engines + Gemini AI)\"]",
            f"        CAT_TRAKT[\"Trakt Catalogs\\n({cats_by_src.get('trakt', 0)} active, {home_by_src.get('trakt', 0)} Home)\"]",
            f"        CAT_TMDB[\"TMDB & Regional Discover\\n({cats_by_src.get('tmdb', 0)} active, {home_by_src.get('tmdb', 0)} Home)\"]",
            f"        CAT_MAL[\"MAL Anime Catalogs\\n({cats_by_src.get('mal', 0)} active, {home_by_src.get('mal', 0)} Home)\"]",
            f"        CAT_STR[\"Streaming Platforms\\n({cats_by_src.get('streaming', 0)} active, {home_by_src.get('streaming', 0)} Home)\"]",
            "        ART[\"Poster Engine\\nPostersPlus + RPDB\"]",
            "    end",
            "",
            "    TMDB_API[(\"TMDB API\\n(SECRET_TMDB_API_KEY)\")]",
            "    TVDB_API[(\"TheTVDB API\\n(SECRET_TVDB_API_KEY)\")]",
            "    KITSU_API[(\"Kitsu & TVmaze APIs\")]",
            "    GEMINI_API[(\"Google AI Studio\\n(SECRET_GEMINI_API_KEY)\")]",
            "    TRAKT_API[(\"Trakt.tv API\\n(SECRET_TRAKT_TOKEN_ID)\")]",
            "    MAL_API[(\"MyAnimeList API\")]",
            "    POSTER_API[(\"PostersPlus & RPDB\\n(SECRET_RPDB_API_KEY)\")]",
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
        if not isinstance(col, Mapping):
            continue
        cid = str(col.get("id") or "")
        ctitle = str(col.get("title") or cid)
        folders = col.get("folders", [])
        f_names = [
            str(f.get("title") or f.get("id"))
            for f in (folders if isinstance(folders, Sequence) else ())
            if isinstance(f, Mapping)
        ]
        feeding: set[str] = set()
        for f in (folders if isinstance(folders, Sequence) else ()):
            if not isinstance(f, Mapping):
                continue
            if f.get("dynamicBingecatLists"):
                feeding.add("BingeCat")
            for src in f.get("sources", []):
                if not isinstance(src, Mapping):
                    continue
                aid = str(src.get("addonId") or "").lower()
                if "aicat" in aid or "bingecat" in aid:
                    feeding.add("BingeCat")
                elif "aio-metadata" in aid or "aiometadata" in aid:
                    feeding.add("AIOMetadata")
                elif "aiostreams" in aid:
                    feeding.add("AIOStreams")
        md_lines.append(
            f"| `{cid}` | **{ctitle}** ({len(f_names)}) | {', '.join(f_names)} | {', '.join(sorted(feeding))} |"
        )

    md_lines.append("")
    return "\n".join(md_lines)
