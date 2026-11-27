---
name: stream-weaver-setup
description: Interactive onboarding, customization, multi-profile management, and troubleshooting for a user's Stremio + Nuvio + AIOStreams + AIOMetadata + AIOManager streaming stack using the Stream Weaver (`sw`) CLI. Activate when the user wants to set up, migrate, customize, add profiles, change Debrid/subtitle packs, diagnose, or sync their streaming stack.
---

# Stream Weaver Interactive Setup & Operations Skill

Guide the user smoothly from zero config to a live, GitOps-managed Stremio/Nuvio streaming stack — without requiring them to memorize JSON schemas or CLI flags.

## Guardrails

1. **Keep user data out of the `stream-weaver` source repo:** Scaffold or operate on an external standalone directory (default suggestion: `~/my-streaming-stack`) registered via `sw workspace use <dir>`.
2. **Never expose secrets:** Never print raw values from `<workspace>/.env` in responses, never embed API keys directly into JSON files (always `{{SECRET_*}}`), and verify `.env` is ignored by `.gitignore` before any git commit.
3. **Inspect before pushing:** Always run `sw validate --json` and `sw diff --stat --json` and get explicit user approval before running `sw push` or `sw push --create`.
4. **Always prefer `--json` commands** (`sw workspace show --json`, `sw validate --json`, `sw packs list --json`, `sw profile list --json`, `sw library list --json`, `sw doctor --json`, `sw diff --json`) rather than scraping Rich tables.

---

## Step 1 — Check CLI & Existing Workspace

```bash
sw --help >/dev/null 2>&1 || uv tool install git+https://github.com/Darshan-AS/stream-weaver.git
sw workspace show --json
```

- If `activeWorkspace` is set, confirm with the user whether they want to work on that workspace.
- If `activeWorkspace` is `null`, propose scaffolding a fresh portable starter workspace and remembering it globally:
  ```bash
  sw init ~/my-streaming-stack --starter --use
  ```

## Step 2 — Run a Concise 4-Question Intake

Ask only what is needed to tailor their stack:

1. **Debrid / Usenet provider(s):** TorBox (`torbox`), Real-Debrid (`realdebrid`), AllDebrid (`alldebrid`), Premiumize (`premiumize`), and/or Easynews?
2. **Instance hosting:** Public hosted (e.g., ElfHosted `aiostreams.elfhosted.com` / `aiometadata.elfhosted.com`), custom hosted URLs, or self-hosted Docker (`docker/docker-compose.yml`)? Do they already have existing `UUID`s or should `sw` provision new ones?
3. **Taste & Playback:**
   - Which starter Home shelves to keep/remove/order (e.g. *For You & Discovery*, *Streaming Services*, *Anime & Japanese*, *Courses & Documentaries*, *Genres & Moods*, *Franchises*, *Indian/Regional Cinema*)?
   - Any regional languages (Kannada, Hindi, Tamil, Telugu, Korean, Japanese, Spanish) or Strelingo dual-subtitle pairs (`jpn:eng`, etc.)?
   - Resolution cap (`2160p`, `1080p`, `720p`) and cached-only vs. uncached torrents?
4. **Profiles & Players:** Single user (`main`) or additional household profiles (partner, parents, kids — optionally with a separate AIOMetadata instance)? Using **Nuvio**, **Stremio**, or both?

## Step 3 — Configure AIOStreams Packs & Household Profiles

### Debrid Provider, Resolution Cap & Subtitles
```bash
sw packs list --json
sw packs set \
  --debrid <torbox|realdebrid|alldebrid|premiumize> \
  --enable core_debrid,core_subtitles \
  --max-resolution <2160p|1080p|720p> \
  --cached-only
```
Add `--dual-subs "jpn:eng"` (plus `,dual_subtitles` in `--enable`) if they requested Strelingo dual-subtitles (note: requires `SECRET_SUBSOURCE_API_KEY`).

### Home Collections & Library Composition
Inspect bundled recipes and parameter schemas cleanly:
```bash
sw library list --json
sw library show folder regional-movies -w language=ja -w slug=japanese -w label=Japanese
```
Edit `<workspace>/profiles/<name>/profile.json`:
- Adjust `collections` (keep only the collections the user wants; reorder them cleanly).
- Remove `"stremio"` under `clients` if the user only uses Nuvio (or `"nuvio"` if they only use Stremio).
- Check `sw profile list --json` afterward.

For each additional family profile:
```bash
# Optional: give them their own AIOMetadata instance if their catalog tastes differ
sw instance new aiometadata <profile> --from <workspace>/aiometadata.json
sw profile new <profile> --aiometadata <profile> --nuvio-login main --nuvio-profile "<Display>" --extras-from main
```

## Step 4 — Secrets Setup & Pre-Push Verification

Inspect `<workspace>/.env.example` (never dump `<workspace>/.env` values!) and give the user a short checklist of the exact keys to paste into `<workspace>/.env`:

- **Required (Minimum 3 keys):**
  1. Debrid key matching their chosen provider (`SECRET_TORBOX_API_KEY` / `SECRET_RD_API_KEY` / `SECRET_ALLDEBRID_API_KEY` / `SECRET_PREMIUMIZE_API_KEY`)
  2. TMDB: `SECRET_TMDB_API_KEY` & `SECRET_TMDB_ACCESS_TOKEN` (free from themoviedb.org/settings/api)
  3. TVDB: `SECRET_TVDB_API_KEY` (free from thetvdb.com)
  4. If provisioning new instances: set strong passwords for `AIOSTREAMS_PASSWORD` and `AIOMETADATA_PASSWORD` (leave `*_UUID` blank).
- **Optional:** `SECRET_RPDB_API_KEY` (`t0-free-rpdb` works), `SECRET_MDBLIST_API_KEY`, `SECRET_BINGECAT_TOKEN`, `SECRET_TRAKT_TOKEN_ID`, `SECRET_SUBSOURCE_API_KEY`, `SECRET_ITSOUT_TOKEN`, or `AIOMANAGER_URL` / `AIOMANAGER_UUID` / `AIOMANAGER_SYNC_PASSWORD`.

Once the user says their keys are in `.env`, run offline + live checks:
```bash
sw validate --json
sw doctor --offline --json
sw doctor --json
```
Fix any reported warnings or missing keys before continuing.

## Step 5 — Provision, Authenticate Clients, Diff, and Push

1. **Provision blank instances** (writes UUIDs back to `.env` automatically):
   ```bash
   sw push aiostreams --create
   sw push aiometadata --create
   # Repeat with `--profile <name> --create` for any secondary AIOMetadata instance
   ```
2. **Have the user sign in to clients:**
   - Remind them to open Nuvio at least once on each device (`tv`, `desktop`, `mobile`) first so profile settings blobs initialize.
   - Run `sw login nuvio --login main` (and `sw login stremio --login main` if enabled).
3. **Preview and confirm before live push:**
   ```bash
   sw diff --stat --json
   ```
   Review the target changes with the user and get confirmation, then push:
   ```bash
   sw push
   ```
4. **Verify health & finish:**
   ```bash
   sw doctor --json
   ```
   Let the user know they can open Studio anytime (`sw studio`), inspect drift (`sw diff`), pull edits made on mobile/web UIs (`sw pull`), or view their generated topology (`<workspace>/ARCHITECTURE.md` / `npx likec4 start <workspace>/architecture`).
