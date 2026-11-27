# Stream Weaver

[![CI & Architecture](https://github.com/Darshan-AS/stream-weaver/actions/workflows/architecture.yml/badge.svg)](https://github.com/Darshan-AS/stream-weaver/actions/workflows/architecture.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-3776AB.svg)](pyproject.toml)
[![Live Architecture Demo](https://img.shields.io/badge/Live_Topology-LikeC4_Demo-0EA5E9.svg)](https://darshanas.dev/stream-weaver/)

**One-stop GitOps control plane & dark-cinema web studio (`sw`) for [Stremio](https://www.stremio.com/) and [Nuvio](https://nuvio.app/).**  
Declare your scrapers, Debrid providers, metadata catalogs, household profiles, Nuvio Home shelves, stream badges, and playback settings once—preview everything visually in **Studio v2**, keep secrets locally scrubbed in `.env`, and sync across every server and device in one command.

---

## 60-Second Quick Start

Choose whichever workflow fits you best:

### Option 1 — Visual Studio (Recommended)
Install the `sw` CLI globally, scaffold your own standalone starter folder outside this repo, and open Studio:

```bash
uv tool install git+https://github.com/Darshan-AS/stream-weaver.git
sw init ~/my-streaming-stack --starter --use
sw studio
```

Studio v2 opens at `http://localhost:8787` with a **4-step guided onboarding wizard**: choose hosted instances (ElfHosted 1-click prefill) or self-hosted URLs, paste your keys with **Live Health Verification**, pick collections & regional cinema, and click **Provision & Push**.

> `sw workspace use` remembers `~/my-streaming-stack` in `~/.config/stream-weaver/config.json`, so plain `sw studio`, `sw push`, `sw pull`, and `sw doctor` work from any directory.

### Option 2 — Set Up with an AI Coding Agent
Use Claude Code, Codex, Cursor, Antigravity, or Gemini CLI with [`AGENTS.md`](AGENTS.md) and our portable skill [`skills/stream-weaver-setup/SKILL.md`](skills/stream-weaver-setup/SKILL.md). Paste this prompt into your agent:

```text
Follow https://github.com/Darshan-AS/stream-weaver/blob/main/skills/stream-weaver-setup/SKILL.md
and install `sw`, scaffold a starter workspace with `sw init ~/my-streaming-stack --starter --use`,
interview me for my Debrid provider and streaming preferences, configure packs & profiles using
`sw --json` commands, verify with `sw validate --json` and `sw doctor --json`, and preview
`sw diff --stat --json` before pushing.
```

### Option 3 — Docker (Zero-Install Stateless Studio or Full Self-Hosted Stack)
```bash
# Build the lightweight Studio image:
docker build -t stream-weaver .

# A. Stateless Browser Mode (zero server disk persistence; keys stay in browser localStorage + 1-click GitOps zip export):
docker run --rm -p 8787:8787 stream-weaver

# B. Local GitOps Mode (mount your workspace folder to /workspace):
docker run --rm -p 8787:8787 \
  -v "$HOME/my-streaming-stack:/workspace" \
  stream-weaver studio --dir /workspace --host 0.0.0.0 --port 8787 --no-open
```

Want to self-host the entire stack (`aiostreams` + `aiometadata` + `redis` + `aiomanager` + `studio`)? See [`docker/docker-compose.yml`](docker/docker-compose.yml):
```bash
cp docker/.env.selfhost.example docker/.env.selfhost
# Set AIOSTREAMS_SECRET_KEY & AIOMANAGER_ENCRYPTION_KEY (`openssl rand -hex 32`)
docker compose -f docker/docker-compose.yml up -d --build
```

---

## What Keys You Actually Need

You only need **3 essentials** to get a working streaming stack—everything else is optional enhancement.

| Tier | Variable(s) | Purpose | Where to Get It |
| :--- | :--- | :--- | :--- |
| **Required** | **1 Debrid / Usenet key** (`SECRET_TORBOX_API_KEY`, `SECRET_RD_API_KEY`, `SECRET_ALLDEBRID_API_KEY`, `SECRET_PREMIUMIZE_API_KEY`, or `SECRET_EASYNEWS_*`) | Instant cached playback in AIOStreams | Your Debrid provider settings |
| **Required** | `SECRET_TMDB_API_KEY` & `SECRET_TMDB_ACCESS_TOKEN` | Movies/series metadata, trailers, hero art | Free at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) |
| **Required** | `SECRET_TVDB_API_KEY` | Series & anime metadata | Free at [thetvdb.com/api-information](https://thetvdb.com/api-information) |
| Optional | `SECRET_RPDB_API_KEY` | Poster ratings (`t0-free-rpdb` works free) | [ratingposterdb.com](https://ratingposterdb.com) |
| Optional | `SECRET_MDBLIST_API_KEY` | 8-source ratings strip in Nuvio (`IMDb`, `RT`, `Metacritic`, `Trakt`, `Letterboxd`, `MAL`) | Free at [mdblist.com/preferences](https://mdblist.com/preferences) |
| Optional | `SECRET_SUBSOURCE_API_KEY` | SubSource & 19-language Strelingo dual-subtitles | Free at [subsource.net](https://subsource.net) |
| Optional | `SECRET_BINGECAT_TOKEN` | BingeCat AI recommendations & rotating shelves | [bingecat.com](https://bingecat.com) |
| Optional | `SECRET_TRAKT_TOKEN_ID` / `SECRET_ITSOUT_TOKEN` | Trakt catalogs / digital release alerts | AIOMetadata Trakt auth / [itsout.elfhosted.com](https://itsout.elfhosted.com/configure) |
| Optional | `SECRET_GEMINI_API_KEY` / `SECRET_OPENROUTER_API_KEY` | AI-assisted metadata search inside AIOMetadata | Google AI Studio / OpenRouter |

> **Do I need AIOManager or [`aio_contraband`](https://github.com/Darshan-AS/aio_contraband)?**
> * **AIOManager is optional:** whenever `AIOMANAGER_UUID` is blank, `sw push` syncs addons, Home collections, badges, metadata enrichment, and app settings directly to Nuvio/Stremio.
> * **`aio_contraband` is optional:** the starter template ships 7 curated Home collections out of the box without Contraband. See [Optional: Contraband Custom Addon](#optional-enable-contraband-custom-addon) to wire it in later.

---

## What's Inside Stream Weaver Studio v2 (`sw studio`)

Stream Weaver Studio runs identically in **Local GitOps Mode** and **Zero-Persistence Stateless Mode**:

* **🧭 4-Step Guided Day-0 Wizard:** Auto-opens on unprovisioned workspaces, prefills public hosted URLs, runs **Live Key Verification**, lets you pick collections + regional cinema, and streams provisioning logs (`NDJSON`).
* **👨‍👩‍👧‍👦 Household Multi-Profile Switcher:** Add family profiles via **+ New Profile** (*Standard*, *Minimal*, *Kids & Family*, or *Clone Existing*), optionally with a dedicated `aiometadata.<profile>.json` instance per person.
* **🏠 Interactive Nuvio Home & Live Poster Simulator:** Reorder collections & tabs (`↑`/`↓`), preview live poster strips with rating badges, or build DIY shelves from MDBList, TMDB Discover, or any addon catalog.
* **⚡ AIOStreams & AIOMetadata Power Studio:** Stack multiple Debrid + Usenet providers in priority order, inspect & wire any custom Stremio `manifest.json` URL, preview stream card formatters (*Prism*, *Compact TV*, *Minimal*), tune size/seeders/HDR/codec/language/Strelingo filters, and toggle AIOMetadata routers, AI Search, and poster engines.
* **🎨 Nuvio Client Simulators & Raw JSON Escape Hatch:** Preview **Elite-Badges** (`TOP`/`BOTTOM`) + **16:9 TV Subtitle Scale**, run **Health Doctor** & **Drift Diff**, or edit scrubbed JSON directly in the **Raw JSON Modal** (with automatic `{{SECRET_*}}` harvesting & scrubbing).

---

## Day-to-Day CLI Cheatsheet

Inspection commands support `--json` so scripts and AI agents can inspect workspaces without scraping terminal tables:

| Task | Command | Notes |
| :--- | :--- | :--- |
| Open Studio Web UI | `sw studio` (`--stateless` for zero disk writes) | Visual editor + onboarding wizard |
| Switch default workspace | `sw workspace show --json` · `sw use <path>` · `sw workspace clear` | Saved in `~/.config/stream-weaver/config.json` |
| Health check keys & APIs | `sw doctor --json` (or `--offline --json`) | Validates `.env`, Debrid, TMDB, TVDB, server UUIDs |
| Inspect drift vs. live servers | `sw diff --stat --json` (or `sw diff --exit-code`) | Read-only comparison across services + Nuvio |
| Switch Debrid / addon packs | `sw packs list --json` · `sw packs set --debrid realdebrid ...` | Declaratively updates `aiostreams.json` |
| Provision & push everything | `sw push aiostreams --create` · `sw push aiometadata --create` · `sw push` | Auto-writes new UUIDs into `.env` |
| Sync only Nuvio client state | `sw push nuvio` (`nuvio_collections`, `nuvio_badges`, `nuvio_meta`, `nuvio_settings`) | Pushes straight to Nuvio without AIOManager |
| Pull web/device edits back | `sw pull` | Auto-harvests secrets into `.env`; ejects Nuvio edits |
| Manage household profiles | `sw profile list --json` · `sw profile new ...` · `sw profile import ...` | Per-person catalogs, logins & isolated instances |
| Browse bundled recipes | `sw library list --json` · `sw library show folder <id> -w key=val` | View templates and parameter bindings |
| Offline CI/workspace check | `sw validate --json` | Validates schemas, recipes & diagram sync |
| Regenerate / view diagrams | `sw diagram` · `npx likec4 start <workspace>/architecture` | Compiles `ARCHITECTURE.md` + LikeC4 model |

---

## How It Works: Tool Repo vs. Your Private Config Repo

`stream-weaver` cleanly separates the **tool** from **your personal configuration**, so your private stack belongs in its own standalone folder/private Git repo (for example `~/my-streaming-stack`), while the bundled recipe library (`library/`) and starter fixture (`examples/home/`) travel inside the installed `stream-weaver` package:

```text
stream-weaver/                       # Public tool + bundled recipes & starter template
├── src/stream_weaver/               # Core, engine, providers, Studio v2, CLI
├── library/                         # Bundled identity-free recipes (collections, folders, catalogs, badges, meta, settings)
├── examples/home/                   # Identity-free demo workspace & LikeC4 fixture
├── skills/stream-weaver-setup/      # Portable AI agent onboarding skill
└── AGENTS.md                        # Cross-agent playbook & contributor rules

~/my-streaming-stack/                # Your standalone private config repo (`sw init --starter --use`)
├── tenant.json                      # Instances, login aliases, profile list, library roots
├── .env                             # Local secrets & UUIDs (automatically gitignored)
├── aiostreams.json                  # AIOStreams providers, filters & formatter ({{SECRET_*}} scrubbed)
├── aiometadata.json                 # AIOMetadata default instance (plus optional aiometadata.<profile>.json)
├── aiomanager.json                  # Optional AIOManager Cloud Sync document
├── profiles/<name>/profile.json     # Per-person taste: Home collections, badges, meta, settings
├── library/                         # Optional workspace-private recipes
└── architecture/ · ARCHITECTURE.md  # Auto-generated LikeC4 & Mermaid topology
```

- **Compile on Push:** Every profile renders recipes + resolves symbolic addon IDs from cached manifests, derives per-instance AIOMetadata catalog lists, updates managed AIOManager accounts, injects `.env` secrets into `{{SECRET_*}}` in memory, and syncs live instances + Nuvio/Stremio.
- **Eject & Scrub on Pull:** Remote UI or on-device edits matching a library recipe remain clean `{ref}` pointers; customized items eject inline into `profile.json` losslessly while any new API tokens are harvested into `.env` and scrubbed from JSON.

---

## Deep Dives

<details>
<summary><strong>1. Nuvio Stream Badges, Details Enrichment & App Settings</strong></summary>

- **Stream Badges (`library/badges/*.json`):** Declaratively syncs regex-matched technology logo packs (such as [Elite-Badges](https://github.com/leonevz/Elite-Badges)) into `stream_badge_settings` across `tv`, `desktop`, and `mobile` (stream-list placement `BOTTOM`/`TOP`, player seek-bar badges, addon logos, file-size chips).
- **Details-Page Enrichment (`library/meta/*.json`):** Configures MDBList multi-source ratings (`SECRET_MDBLIST_API_KEY`), TMDB trailers/credits/logos (`SECRET_TMDB_API_KEY`), Trakt comments, and Meta Screen layout per platform with read-modify-write merges.
- **App Settings (`library/settings/*.json`):** Table-driven registry ([`nuvio_settings.py`](src/stream_weaver/core/nuvio_settings.py)) synchronizing playback (`reuseLastLink`, auto-play, next-episode binge groups, IntroDB/AnimeSkip), audio/subtitle styling, themes, TV Home layout, and notifications while preserving device-local decoder/buffer settings.

</details>

<details>
<summary><strong>2. Self-Updating Architecture Topology (`LikeC4` & `ARCHITECTURE.md`)</strong></summary>

Every `sw pull`, `sw push`, `sw sanitize`, and `sw diagram` compiles a workspace's JSON config into an interactive **LikeC4** model (`architecture/topology.c4`) and Mermaid diagrams (`ARCHITECTURE.md`). Browse the public demo deployment at [darshanas.dev/stream-weaver](https://darshanas.dev/stream-weaver/) or explore your own stack locally:

```bash
sw diagram
npx likec4 start ~/my-streaming-stack/architecture
```

</details>

<details>
<summary><strong>3. Optional: Enable Contraband Custom Addon</strong></summary>

If you pair Stream Weaver with [`aio_contraband`](https://github.com/Darshan-AS/aio_contraband) (`cb` CLI for curated niche courses, lectures, and unlisted specials backed by Debrid/static catalogs):
1. In **Studio → AIOStreams Power → Custom Addon URL Manager**, add `https://raw.githubusercontent.com/Darshan-AS/aio_contraband/main/manifest.json` (or wire it into `aiostreams.json`).
2. Add a local folder recipe in `<workspace>/library/folders/` referencing its catalog, add it to your profile's `collections`, and run `sw push`.

</details>

<details>
<summary><strong>4. Software Architecture & Contributor Verification Suite</strong></summary>

`stream-weaver` uses a **Functional Core / Imperative Shell** layout in [`src/stream_weaver/`](src/stream_weaver/):
- `core/` — Pure frozen dataclasses (`Result[T, E] = Ok[T] | Err[E]`), JSON-path `SecretLens` scrubbing, PBKDF2 + AES-256-GCM crypto for AIOManager Cloud Sync (zero Node.js dependency), AIOStreams/AIOMetadata/AIOManager power compilers, LikeC4/Mermaid generator, and Doctor rules.
- `engine/` — Shared multi-profile orchestration (`workspace.py`) powering both CLI and Studio v2.
- `providers/` — Structural `typing.Protocol` adapters for AIOStreams, AIOMetadata, AIOManager, Nuvio, and Stremio.
- `effects/`, `cli/`, `studio/` — I/O shell (`httpx`, `.env`/JSON storage, Typer CLI, and zero-build ES-module web Studio).

Run the local verification gate:
```bash
uv run --no-config ruff check src tests
uv run --no-config mypy src
uv run --no-config pytest
uv run --no-config sw validate --dir examples/home --json
```

</details>
