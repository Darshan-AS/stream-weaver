# Stream Weaver — Live System Architecture & Topology

> **Auto-Generated Architecture-as-Code:** This file and [`architecture/topology.c4`](architecture/topology.c4) are deterministically compiled from [`configs/aiomanager.json`](configs/aiomanager.json), [`configs/aiostreams.json`](configs/aiostreams.json), [`configs/aiometadata.json`](configs/aiometadata.json), and [`configs/nuvio_collections.json`](configs/nuvio_collections.json) via `uv run sw diagram` (and automatically updated on `sw pull`, `sw push`, `sw sanitize`, and `git commit`).

## Interactive Drill-Down Explorer (LikeC4 — 10 Views)

For the interactive, Google-Maps-style zoomable graph with **10 perspectives** (`index`, `aiomanager_view`, `nuvio_collections_view`, `aiostreams_view`, `aiometadata_view`, `contraband_view`, `secrets_view`, `stream_resolution_flow`, `gitops_sync_flow`, `ai_search_flow`):

```bash
# Regenerate topology from configs/*.json at any time:
uv run sw diagram

# Launch the interactive LikeC4 web explorer locally:
npx likec4 start architecture

# Or build a standalone static HTML site (also automated via GitHub Actions -> GitHub Pages):
npx likec4 build architecture -o dist/architecture
```

*(Tip: You can also install the **LikeC4** extension in VS Code / Cursor / Antigravity and open [`architecture/topology.c4`](architecture/topology.c4) directly in the editor.)*

---

## 1. Overarching System Landscape (Level 1–2)

```mermaid
flowchart LR
    subgraph GitOps["🧶 Local GitOps (stream-weaver)"]
        SW["sw CLI\ninit | pull | push | diff | sanitize | warm | diagram"]
        ENV[".env\n{{SECRET_*}} Vault"]
        SW --- ENV
    end

    subgraph Clients["📺 Streaming Clients"]
        Nuvio["Nuvio App\n(5 Home Collections)"]
        Stremio["Stremio App"]
    end

    subgraph Control["🎛️ Control Plane"]
        MGR["AIOManager\nAccounts, Key Vault & Autopilot"]
    end

    subgraph Aggregators["⚡ Core AIO Aggregators"]
        AS["AIOStreams\n(15 Active Plugins)"]
        AM["AIOMetadata\n(64 Active Catalogs, 48 Home)"]
        BC["BingeCat AI Assistant"]
        CB["Contraband Static Addon\n(Peterson Academy)"]
        CM["Cinemeta (IMDb Fallback)"]
    end

    subgraph Upstream["☁️ External Upstream Servers & APIs"]
        DEB[("TorBox Cloud & Debrid API")]
        TMDB[("TMDB API\nMeta, Search & Discover")]
        TVDB[("TheTVDB API v4\nSeries & Anime")]
        GEM[("Google AI Studio\nGemini Search")]
        TRAKT[("Trakt.tv API\nWatchlist & Up Next")]
        MAL[("MyAnimeList / Kitsu\nAnime Catalogs & IDs")]
        POSTER[("Rating Posters\nRPDB / Custom")]
        GH[("GitHub Raw & Tamtaro\nManifests & SEL Rules")]
    end

    SW ==> MGR & AS & AM
    MGR == "Native Sync" ==> Nuvio & Stremio
    Nuvio & Stremio --> AS & AM & BC & CM
    AS --> CB & DEB & TMDB & TVDB & POSTER & GH
    CB --> GH & DEB
    AM --> TMDB & TVDB & GEM & TRAKT & MAL & POSTER
```

---

## 2. AIOStreams Internal Pipeline & Upstream Connections

```mermaid
flowchart LR
    subgraph AIOStreams["AIOStreams Internal Pipeline"]
        SEL["SEL Filter, Deduplicator\n& Custom Formatter"]
        subgraph G1["Group 1: Primary Sequential (condition: true)"]
            P_swc["Contraband (swc)"]
            P_lib["Library (lib)"]
            P_tio["Torrentio (tio)"]
            P_met["Meteor (met)"]
            P_com["Comet (com)"]
            P_stz["StremThru Torz (stz)"]
            P_mdf["MediaFusion (mdf)"]
            P_kna["Knaben (kna)"]
            P_sdx["SeaDex (sdx)"]
            P_atn["AnimeTosho (atn)"]
        end
        subgraph G2["Group 2: HTTP Fallback (count(cached(totalStreams)) < 3)"]
            P_soo["Sootio (soo)"]
            P_hdh["HD Hub (hdh)"]
            P_wst["WebStreamr (wst)"]
        end
        subgraph Subs["Subtitle Addons"]
            P_osb["OpenSubtitles V3 Pro (osb)"]
            P_osv["OpenSubtitles v3 (Fallback) (osv)"]
        end
    end

    DEB_EXT[("TorBox Cloud & Debrid API\n(SECRET_TORBOX_API_KEY)")]
    CB_EXT[("Static Addon Manifest\n(GitHub Raw)")]
    RULES_EXT[("Tamtaro & Ranked\nSEL / Regex Rules")]
    META_EXT[("TMDB / TVDB / RPDB")]

    SEL --> G1
    G1 -. "If count(cached(totalStreams)) < 3" .-> G2
    G1 ==> DEB_EXT
    P_swc --> CB_EXT
    SEL --> RULES_EXT & META_EXT
```

### Active AIOStreams Plugins (`configs/aiostreams.json`)

| ID | Plugin Name | Preset Type | Execution Group | Resources | Upstream Dependency |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `swc` | **Contraband** | `custom` | Group 1 (Primary) | `catalog, meta, stream` | Static Manifest + Torbox |
| `lib` | **Library** | `library` | Group 1 (Primary) | `stream, catalog, meta` | Torbox Cloud Library (`SECRET_TORBOX_API_KEY`) |
| `tio` | **Torrentio** | `torrentio` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `met` | **Meteor** | `meteor` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `com` | **Comet** | `comet` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `stz` | **StremThru Torz** | `stremthruTorz` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `mdf` | **MediaFusion** | `mediafusion` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `kna` | **Knaben** | `knaben` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `sdx` | **SeaDex** | `seadex` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `atn` | **AnimeTosho** | `animetosho` | Group 1 (Primary) | `stream` | Torbox Cache + Public Indexers |
| `soo` | **Sootio** | `sootio` | Group 2 (`count(cached(totalStreams)) < 3`) | `stream` | Direct HTTP Providers |
| `hdh` | **HD Hub** | `hdhub` | Group 2 (`count(cached(totalStreams)) < 3`) | `stream` | Direct HTTP Providers |
| `wst` | **WebStreamr** | `webstreamr` | Group 2 (`count(cached(totalStreams)) < 3`) | `stream` | Direct HTTP Providers |
| `osb` | **OpenSubtitles V3 Pro** | `opensubtitles-v3-plus` | Subtitles / Utility | `subtitles` | OpenSubtitles API |
| `osv` | **OpenSubtitles v3 (Fallback)** | `opensubtitles` | Subtitles / Utility | `subtitles` | OpenSubtitles API |

---

## 3. AIOMetadata Internal Engines & External Servers

```mermaid
flowchart LR
    subgraph AIOMetadata["AIOMetadata Internal Engines"]
        MR["Metadata Router\nMovie/Series: TMDB | Anime: TVDB + KITSU"]
        SR["Search Engine\n(8 Active Engines)"]
        CAT_TRAKT["Trakt Catalogs\n(11 active, 5 Home)"]
        CAT_TMDB["TMDB Discover\n(22 active, 16 Home)"]
        CAT_MAL["MAL Anime Catalogs\n(7 active, 3 Home)"]
        CAT_STR["Streaming Platforms\n(24 active, 24 Home)"]
        ART["Poster Engine\npostersplus.slokker.cc + RPDB"]
    end

    TMDB_API[("TMDB API\n(SECRET_TMDB_API_KEY)")]
    TVDB_API[("TheTVDB API\n(SECRET_TVDB_API_KEY)")]
    KITSU_API[("Kitsu & TVmaze APIs")]
    GEMINI_API[("Google AI Studio\n(SECRET_GEMINI_API_KEY)")]
    TRAKT_API[("Trakt.tv API\n(SECRET_TRAKT_TOKEN_ID)")]
    MAL_API[("MyAnimeList API")]
    POSTER_API[("Rating Poster APIs\n(SECRET_RPDB_API_KEY)")]

    MR --> TMDB_API & TVDB_API & KITSU_API
    SR --> TMDB_API & TVDB_API & KITSU_API & GEMINI_API
    CAT_TRAKT --> TRAKT_API
    CAT_TMDB & CAT_STR --> TMDB_API
    CAT_MAL --> MAL_API
    CAT_TRAKT & CAT_TMDB & CAT_MAL & CAT_STR --> ART
    ART --> POSTER_API
```

---

## 4. Nuvio Curated Home Collections (`configs/nuvio_collections.json`)

| Collection ID | Title | Tabbed Folders | Upstream Addons |
| :--- | :--- | :--- | :--- |
| `col-for-you-discovery` | **For You & Discovery** (5) | AI Picks For You, Because You Watched, Trending & Popular, Top Rated All-Time, Watchlist & Cloud Library | AIOMetadata, AIOStreams, BingeCat |
| `col-streaming-services` | **Streaming Services** (8) | Netflix, Prime Video, Apple TV+, HBO Max, Disney+, Hulu, Paramount+, Peacock | AIOMetadata |
| `col-indian-cinema` | **Indian Cinema & Regional OTT** (5) | Kannada Cinema, Hindi Cinema & Series, JioHotstar, Sony LIV, ZEE5 | AIOMetadata |
| `col-anime-japanese` | **Anime & Japanese** (3) | Airing & Popular Anime, Top Rated Anime, Japanese Cinema & Drama | AIOMetadata |
| `col-courses-docs` | **Courses & Documentaries** (3) | Peterson Academy, Curiosity Stream, Top Documentaries & Docuseries | AIOMetadata, AIOStreams |

---

## 5. Nuvio Stream & Player Badges (`configs/nuvio_badges.json`)

| Platforms | Placement | Paused Player Logos | Active Badge Pack URLs |
| :--- | :--- | :--- | :--- |
| `tv`, `desktop`, `mobile` | `BOTTOM` | `enabled` | `https://raw.githubusercontent.com/leonevz/Elite-Badges/main/badges.json` |
