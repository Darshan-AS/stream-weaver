# Stream Weaver — Tasks & Roadmap

## Completed

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
