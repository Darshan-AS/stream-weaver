# AGENTS.md — Agent Guide for Stream Weaver

This file helps AI coding agents (Claude Code, Codex, Cursor, Antigravity, Gemini CLI) perform two jobs cleanly:
1. **Set up and customize a user's personal Stremio + Nuvio streaming stack** via the `sw` CLI.
2. **Contribute code or recipes** to the `stream-weaver` codebase safely.

---

## 1. Setting Up a User's Streaming Stack (Day-0 & Day-2 Playbook)

When a user asks you to set up, customize, debug, or update their Stremio/Nuvio/AIOStreams/AIOMetadata/AIOManager stack, follow this deterministic workflow. Always prefer structured `--json` flags over parsing human Rich tables.

### Step-by-Step Onboarding Workflow
1. **Verify `sw` is installed** (install if missing):
   ```bash
   sw --help || uv tool install git+https://github.com/Darshan-AS/stream-weaver.git
   ```
2. **Check or scaffold the user's portable workspace (OUTSIDE this tool repo):**
   ```bash
   sw workspace show --json
   # If no workspace exists yet, scaffold a standalone starter folder and set it as default:
   sw init ~/my-streaming-stack --starter --use
   ```
   Never store personal configs or `.env` files inside the `stream-weaver` repository clone unless the user explicitly maintains a multi-tenant monorepo.
3. **Run a brief 4-topic interview before editing files:**
   - **Debrid / Usenet:** TorBox (`torbox`), Real-Debrid (`realdebrid`), AllDebrid (`alldebrid`), Premiumize (`premiumize`), and/or Easynews.
   - **Hosting:** Public ElfHosted (`https://aiostreams.elfhosted.com`, `https://aiometadata.elfhosted.com`), custom URLs, or self-hosted Docker (`docker/docker-compose.yml`).
   - **Content Taste:** Which starter collections/shelves to keep or reorder, regional languages (e.g., Hindi, Kannada, Tamil, Telugu, Anime), dual-language subtitles (`jpn:eng`, etc.), max resolution (`2160p`/`1080p`/`720p`), and cached-only vs. uncached policy.
   - **Household Profiles & Clients:** Single user (`main`) or extra profiles (e.g., partner, parents, kids — optionally with dedicated AIOMetadata instances), and whether they use **Nuvio**, **Stremio**, or both.
4. **Apply AIOStreams Debrid, packs, and stream preferences declaratively:**
   ```bash
   sw packs list --json
   sw packs set --debrid <provider> --enable core_debrid,core_subtitles --max-resolution 2160p --cached-only
   ```
   Or add custom Stremio `manifest.json` addons / fine-grained filters via Studio (`sw studio`) or `aiostreams.json`.
5. **Compose Home collections & multi-profile settings:**
   - Browse all available bundled recipes and parameters:
     ```bash
     sw library list --json
     sw library show <kind> <id> --with key=val
     ```
   - Edit `<workspace>/profiles/<name>/profile.json` to pick `collections`, `badges`, `meta`, `settings`, and `aiometadata.extraCatalogs`.
   - For additional household members:
     ```bash
     sw instance new aiometadata <profile> --from <workspace>/aiometadata.json   # optional isolation
     sw profile new <profile> --aiometadata <profile> --nuvio-login <login> --nuvio-profile "<Display>" --extras-from main
     ```
6. **Direct the user to enter their secrets in `<workspace>/.env`:**
   - **Minimum 3 essentials:** 1 Debrid/Usenet API key (`SECRET_TORBOX_API_KEY` or `SECRET_RD_API_KEY`, etc.), `SECRET_TMDB_API_KEY` + `SECRET_TMDB_ACCESS_TOKEN`, and `SECRET_TVDB_API_KEY` — plus chosen `AIOSTREAMS_PASSWORD` / `AIOMETADATA_PASSWORD` if provisioning fresh instances.
   - Do **not** read back or echo `.env` secret values in chat.
7. **Validate offline & run Health Doctor:**
   ```bash
   sw validate --json
   sw doctor --offline --json   # offline key/schema checks first
   sw doctor --json             # live key/API reachability checks (Debrid, TMDB, TVDB, servers)
   ```
8. **Provision remote instances (when UUIDs are blank) & authenticate clients:**
   ```bash
   sw push aiostreams --create
   sw push aiometadata --create
   sw login nuvio --login main        # interactive client auth
   # Optional: sw login stremio --login main
   ```
9. **Preview diff & obtain explicit confirmation before pushing:**
   ```bash
   sw diff --stat --json
   ```
   Summarize what will change remotely, get explicit user approval, and run:
   ```bash
   sw push
   ```
10. **Verify topology & interactive UI:**
    `sw push` automatically regenerates `<workspace>/ARCHITECTURE.md` and `<workspace>/architecture/topology.c4`. Users can launch `sw studio` anytime for visual previews, live poster shelves, and UI-driven edits.

---

## 2. Hard Safety Rules for Agents

1. **Never print, log, or commit secrets:** Never inspect and repeat `.env` values in terminal summaries or prose, never embed API keys or tokens in JSON files (use `{{SECRET_*}}` placeholders), and always confirm `.env` is listed in `<workspace>/.gitignore` before `git add`.
2. **Never push destructive or live changes unapproved:** Always run `sw validate --json` and `sw diff --stat --json` and present a concise change summary to the user before running `sw push`, `sw push --create`, or `git push`.
3. **Never write user state into the tool repo:** User workspaces belong in standalone folders outside `stream-weaver` (e.g., `~/my-streaming-stack`).
4. **Safe inspection commands (with structured JSON):**
   - `sw workspace show --json`
   - `sw validate --json`
   - `sw packs list --json`
   - `sw profile list --json`
   - `sw library list --json`
   - `sw doctor --json` (or `--offline --json`)
   - `sw diff --json` (or `--stat --json`)

---

## 3. Workspace File Map (`<workspace>/`)

| File / Directory | Purpose | Safe for Git? |
| :--- | :--- | :--- |
| `.env` | Real API keys, UUIDs, passwords, login tokens | **NO** (gitignored) |
| `.env.example` | Documented empty template of all supported env keys | Yes |
| `tenant.json` | Instances, login aliases, profiles list, library roots | Yes |
| `aiostreams.json` | AIOStreams Debrid providers, scrapers, SEL filters, formatter | Yes (`{{SECRET_*}}` scrubbed) |
| `aiometadata[.<inst>].json` | AIOMetadata providers, artwork rules, compiled catalogs | Yes |
| `aiomanager.json` | Optional AIOManager Cloud Sync & managed account entries | Yes |
| `profiles/<name>/profile.json` | Per-person Home `collections`, `badges`, `meta`, `settings`, extra catalogs | Yes |
| `library/` | Optional workspace-private recipe overrides/additions | Yes |
| `ARCHITECTURE.md` & `architecture/` | Auto-generated Mermaid overview + LikeC4 topology | Yes |

---

## 4. Contributing to `stream-weaver`

- **Architecture (`src/stream_weaver/`):**
  - `core/` — Pure functional core (`@dataclass(frozen=True, slots=True)`, `Result[T, E]`, **zero I/O, zero path literals to repo data dirs**).
  - `engine/` — Shared orchestration used identically by CLI and Studio.
  - `providers/` — Protocol implementations for AIOStreams, AIOMetadata, AIOManager, Nuvio, Stremio.
  - `effects/`, `cli/`, `studio/` — Imperative shell (HTTP, `.env`/JSON storage, Typer CLI, Studio v2 server + zero-build ES modules).
- **Boundary invariants (`tests/unit/test_boundaries.py`):**
  - `src/` must never hardcode `tenants/`, `library/`, or `examples/` path literals or personal identifiers/UUIDs.
  - `library/` and `examples/` must stay identity-free.
  - `uv.lock` must reference only public PyPI (`uv lock --no-config --default-index https://pypi.org/simple`).
- **Verification suite (run after every edit):**
  ```bash
  uv run --no-config ruff check src tests
  uv run --no-config mypy src
  uv run --no-config pytest
  uv run --no-config sw validate --dir examples/home --json
  ```
