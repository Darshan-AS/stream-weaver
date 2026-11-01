# Stream Weaver — Tasks & Roadmap

## v2 — Multi-Tenant, Multi-Profile Control Plane ✅ (Phases 0–4 live; 5 scaffolded; 6 done)

> Design: the approved *Stream Weaver v2* plan (Library → Profile → Tenant). Entries below are the delivered state; historic entries further down refer to the pre-v2 `configs/` layout, which now lives at `tenants/don/`.

### Phase 0 · Restructure ✅
- [x] `core/tenant.py`, `tenant.json` v1, `resolve_tenant_root` with no repo-root fallback; `tenants/don/`; `sw init [--from]`; `sw validate [--all-tenants]`; `examples/home` fixture; boundary lint; CI + CODEOWNERS + per-tenant pre-commit

### Phase 1 · Identity ✅
- [x] 1a `core/instances.py` — named instances (`AIOMETADATA_DAD_*`), adapters take an `InstanceBinding`, `sw instance list|new`, `--instance`, `--create` writes UUID/encrypted-password back
- [x] 1b/1c `core/logins.py` + `NuvioSessionPool` — one auth per login, refresh-safe, syncs serialised per login; `sw login nuvio|stremio|adopt|list`
- [x] 1d identity-aware secret lenses — `{{AIOMETADATA_<I>_UUID}}`, `{{AIOSTREAMS_URL}}`, `SECRET_<PLATFORM>_<ACCOUNT>_*`
- [x] 1e `core/profiles.py` + `core/aiomanager_accounts.py` — `profile.json`, managed `accounts[]` merged over server state, `sw profile list|import|new`; don imported as a no-op

### Phase 2 · Library & composition ✅
- [x] `core/template.py` (frozen surface), `core/library.py` (layered roots), `core/composition.py` (symbolic sources, ref-or-inline compile, eject-on-pull, derived catalogs), `core/addon_index.py`, `core/compile.py`, `core/migrate.py`
- [x] `sw migrate compose` lifted don 1:1 (golden-gated): 40 shared + 4 private recipes; `sw library list|show`; `cli/nuvio.py` replaces the four copied Nuvio blocks
- [x] Parameterised `regional-*` catalogs/folders/collection (one-off kannada/hindi catalogs removed, byte-identical)

### Phase 3 · Multi-profile fan-out ✅
- [x] `--profile` scoping on push/pull/diff; `sw profile nuvio-list`; Profiles table in ARCHITECTURE.md

### Phase 4 · Dad ✅
- [x] `aiometadata/dad` provisioned; `profiles/dad` (Kannada → Hindi with 80s/90s/2000s + classic series, Discover, full Streaming strip; Cinemeta only; Nuvio profile #2); pushed — all 11 targets in sync

### Phase 5 · GF scaffold ◐
- [x] Own-login path proven (`tests/unit/test_own_login_profile.py`)
- [ ] When she is ready: `sw login nuvio --login gf` (or share login `don` + a Nuvio profile), `sw instance new aiometadata gf --from tenants/don/aiometadata.json`, `sw push aiometadata --profile gf --create`, `sw profile new gf …`, then author her collections (anime / Japanese recipes already exist: `col-anime-japanese`)

### Phase 6 · Docs ✅
- [x] README (layout, push/pull semantics, people/recipe commands), tasks.md

### Deferred (by decision)
- [ ] Live-TV / serial sources for Dad (likely a second static addon → `addons/` fold + Pages deploy)
- [ ] "Not yet digital" badge on unreleased titles
- [ ] AIOStreams recipes (plugin sets / SEL / formatter) — revisit if Brother's drift or per-profile ranking hurts
- [ ] Per-instance AIOManager vault entries as a managed section; PyPI / public subtree split
- [ ] `col-for-you-discovery` is tenant-private only because ItsOut! is wired as a `custom` preset; split the ItsOut folder out if Brother wants the BingeCat rows

---

## Completed (pre-v2)

### Recommendations & Search
- [x] Replace **Watchly** (`com.bimal.watchly`) with **BingeCat** (`com.aicat`) across `configs/aiostreams.json`, `configs/aiomanager.json`, and `src/stream_weaver/core/coercions.py`
- [x] Disable **AIOMetadata Gemini AI Search** (`search.ai_enabled: false`, `gemini.search: false`) in `configs/aiometadata.json` in favor of BingeCat AI-Assisted Search

### Artwork & Posters
- [x] Keep **BetterPosters** (`user-posters` in RPDB custom API key slot) for native RPDB badge overlays on fan-art posters

### Nuvio Collections & Home Screen Organization
- [x] Add `configs/nuvio_collections.json` defining 5 Nuvio Collections (**For You & Discovery**, **Streaming Services**, **Indian Cinema & Regional OTT**, **Anime & Japanese**, **Courses & Documentaries**)
- [x] Implement `compile_nuvio_collections` and `NuvioClient.sync_push_collections` (`POST /rest/v1/rpc/sync_push_collections`) in `src/stream_weaver/providers/services/clients.py`
- [x] Wire Nuvio Collections compilation and push into `sw diff` and `sw push` (`src/stream_weaver/cli/app.py`, `src/stream_weaver/core/coercions.py`)
- [x] Move the 3 rotating BingeCat daily micro-genre catalogs (`aicat_list_for_you_1..3`) out of Nuvio Collection folders onto the loose Home screen so they refresh automatically every 24h without running `sw push`
- [x] Fix missing folder cover images on **Nuvio Mac Desktop** (standardize all 24 `coverImageUrl`s and 5 `backdropImageUrl`s in `configs/nuvio_collections.json` to genuine `.webp` assets on `https://imkaptain.github.io/nuvio-assets/assets/images/<hash>.webp`, replacing `raw.githubusercontent.com` `.png` files that had mismatched JPEG magic bytes)

### Nuvio Stream Badges (`nuvio_badges`)
- [x] Add `configs/nuvio_badges.json` declaring **Elite-Badges** (`leonevz/Elite-Badges`) across `tv`, `desktop`, and `mobile` with stream-list and video-player (`showInPlayer`) logo badges
- [x] Implement pure compiler/decompiler `src/stream_weaver/core/nuvio_badges.py`, `NuvioClientSync.push_badges` / `pull_badges`, and `sw push` / `sw pull` / `sw diff` `nuvio_badges` support
- [x] Strip redundant text badges (`4K`, `REMUX`, `HEVC`, `HDR`/`DV`, `Atmos`, `7.1`) from `configs/aiostreams.json` custom formatter (keeping `AV1` fallback and filename)

### Nuvio Details-Page Enrichment (`nuvio_meta`)
- [x] Add `configs/nuvio_meta.json` declaring MDBList ratings providers (IMDb/TMDB/RT/Audience/Metacritic/Trakt/Letterboxd/MAL), TMDB enrichment modules, Trakt comments, and Meta Screen layout
- [x] Implement pure compiler `src/stream_weaver/core/nuvio_meta.py` (per-platform `mdblist_settings` / `tmdb_settings` / `trakt_*` / `meta_screen_settings_payload` fragments, read-modify-write blob overlay, provider-credential merge, decompiler for `sw diff`)
- [x] Implement `NuvioClientSync.push_meta` / `pull_meta` (profile settings blobs for `tv`/`desktop`/`mobile` + `sync_seed/push/pull_provider_credentials` RPCs for the MDBList/TMDB keys) and wire `nuvioMeta` through `sync_account`
- [x] Wire `nuvio_meta` into `sw push` / `sw pull` / `sw diff` / `sw sanitize` (implicit via `all`/`aiomanager`/`nuvio`, explicit via `nuvio_meta`) with `SECRET_MDBLIST_API_KEY` / `SECRET_TMDB_API_KEY` lenses
- [ ] **Set `SECRET_MDBLIST_API_KEY` in `.env`** (free key from https://mdblist.com/preferences) and run `uv run sw push nuvio_meta`; until then `sw push all` skips the meta sync with a warning

### Nuvio App Settings (`nuvio_settings`)
- [x] Add `configs/nuvio_settings.json` managing every portable Nuvio preference (playback incl. **Reuse Last Link**, stream auto-play, next-episode/binge groups, skip-intro/auto-skip/AnimeSkip, audio & subtitle preferences/styling, theme, TV Home layout, notifications, TV plugin grouping) with opinionated defaults
- [x] Implement table-driven compiler `src/stream_weaver/core/nuvio_settings.py` (one `Setting` registry row per preference → per-platform `player_settings` / `theme_settings` / `layout_settings` / `animeskip_settings` / `plugin_settings` / `notifications_settings` fragments; codecs for enum/colour/unit/capability translation; read-modify-write merge with key deletion; expressive-platform consensus decompiler for `sw diff`)
- [x] Implement `NuvioClientSync.push_settings` / `pull_settings` (+ `animeskip` / `introdb` provider credentials) and wire `nuvioSettings` through `sync_account`, `sw push` / `sw pull` / `sw diff` / `sw sanitize` (implicit via `all`/`aiomanager`/`nuvio`, explicit via `nuvio_settings`) with `SECRET_ANIMESKIP_CLIENT_ID` / `SECRET_INTRODB_API_KEY` lenses
- [ ] Revisit a `debrid` block only if Nuvio's native TorBox resolver is ever preferred over AIOStreams (deliberately out of scope; keys differ per platform and hold credentials)

### Community Addons & Subtitles Adoption (`aiostreams` + `nuvio_collections`)
- [x] Add **EinthusanTV Kannada** (`etv`) to AIOStreams Group 2 HTTP fallback (`resources: ["stream"]`, `mediaTypes: ["movie"]`, `titleMatching` enabled)
- [x] Add **Stinger Pro** (`ssp`), **ItsOut!** (`ito`), and **BasedOn** (`bso`) to AIOStreams Group 1 (`pinPosition: "bottom"`, `resultPassthrough: true`, `formatPassthrough: true`)
- [x] Add **SubSource** (`ssc`, English/Kannada/Hindi/Japanese) and 3 **Strelingo** dual-subtitle pairs (`JA → EN`, `EN → KN`, `HI → KN`) wired with `SECRET_SUBSOURCE_API_KEY`
- [x] Update Nuvio Collection folder `fld-watchlist-library` → **Cloud Library & Release Alerts** (TorBox Cloud Library + `itsout.released` Movies & Series, pruning dead `trakt.watchlist.*` entries)
- [ ] **Set `SECRET_ITSOUT_TOKEN` & `SECRET_SUBSOURCE_API_KEY` in `.env`** and run `uv run sw push`

---

## Pending / Backlog

### Deferred Addon / Plugin Candidates (Revisit Only If Needed)
- [ ] **PenguPlay (`penguplay` preset)**: Revisit as a Group 2 HTTP fallback only if existing Group 2 providers (`hdh`, `soo`, `wst`, `etv`) regularly miss titles (requires personal sign-in at `pengu.uk`)
- [ ] **Nuvio-native plugin sync (`sync_push_plugins`)**: Revisit adding `configs/nuvio_plugins.json` + `sync_push_plugins` RPC support only if regional titles (`MovieBox` / `Castle`) keep missing from TorBox + EinthusanTV + Sootio + WebStreamr

### Nuvio Sync & CLI
- [ ] **Support `sync_pull_collections` in `sw pull`**: Fetch remote Nuvio collections via `POST https://api.nuvio.tv/rest/v1/rpc/sync_pull_collections` during `sw pull` (or `sw diff`) so edits made directly in the Nuvio app UI can be inspected or synced back into `configs/nuvio_collections.json`
- [ ] **Optional dynamic catalog drift check**: Add a lightweight check/command to detect when BingeCat rotates `aicat_list_for_you_*` catalog IDs in the live AIOStreams manifest

### BingeCat Configuration
- [ ] **Enable BingeCat AI-Assisted Search catalogs on `bingecat.com`**: If not yet enabled on the hosted BingeCat token, toggle **AI-Assisted Search** on [bingecat.com](https://bingecat.com) and re-save (or update `SECRET_BINGECAT_TOKEN` in `.env` if the token changes) so `aicat_search_movie` and `aicat_search_series` appear in the live manifest

### Cross-Client Compatibility (Optional)
- [ ] **Stremio vs. Nuvio catalog toggle (if needed)**: Since Nuvio Collections deduplicate folder catalogs off the Nuvio Home screen while Stremio renders all `showInHome: true` catalogs as flat rows, optionally support a profile or flag if Stremio is ever used alongside Nuvio
