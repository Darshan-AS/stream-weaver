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
- [x] Fix missing folder cover images on **Nuvio Mac Desktop** (replace broken `.webp` hashes and plain-black v1 covers in `configs/nuvio_collections.json` with verified `ImKaptain/nuvio-assets` hashes and `v2` cover artwork)

---

## Pending / Backlog

### Nuvio Sync & CLI
- [ ] **Support `sync_pull_collections` in `sw pull`**: Fetch remote Nuvio collections via `POST https://api.nuvio.tv/rest/v1/rpc/sync_pull_collections` during `sw pull` (or `sw diff`) so edits made directly in the Nuvio app UI can be inspected or synced back into `configs/nuvio_collections.json`
- [ ] **Optional dynamic catalog drift check**: Add a lightweight check/command to detect when BingeCat rotates `aicat_list_for_you_*` catalog IDs in the live AIOStreams manifest

### BingeCat Configuration
- [ ] **Enable BingeCat AI-Assisted Search catalogs on `bingecat.com`**: If not yet enabled on the hosted BingeCat token, toggle **AI-Assisted Search** on [bingecat.com](https://bingecat.com) and re-save (or update `SECRET_BINGECAT_TOKEN` in `.env` if the token changes) so `aicat_search_movie` and `aicat_search_series` appear in the live manifest

### Cross-Client Compatibility (Optional)
- [ ] **Stremio vs. Nuvio catalog toggle (if needed)**: Since Nuvio Collections deduplicate folder catalogs off the Nuvio Home screen while Stremio renders all `showInHome: true` catalogs as flat rows, optionally support a profile or flag if Stremio is ever used alongside Nuvio
