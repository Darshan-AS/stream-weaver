#!/usr/bin/env python3
"""
One-off: back-fill Trakt history from Nuvio Sync's watched items (preserving original `watched_at`).

Usage (from the stream-weaver root, with .env populated):

    .venv/bin/python scripts/trakt_backfill.py            # dry run: prints what WOULD be sent
    .venv/bin/python scripts/trakt_backfill.py --apply    # authorise via Trakt device code, then push

Requires in .env:
    TRAKT_CLIENT_ID / TRAKT_CLIENT_SECRET  -> your own Trakt API app (https://trakt.tv/oauth/applications)
    NUVIO_SUPABASE_ANON_KEY + AIOMANAGER_SYNC_PASSWORD -> already used by `sw` for Nuvio sync

Only items with IMDb ids are sent (Trakt cannot represent custom/Contraband ids). Items Trakt
already lists as watched are skipped so re-running never inflates play counts.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stream_weaver.cli.app import _resolve_nuvio_connection  # noqa: E402
from stream_weaver.core.result import Ok  # noqa: E402
from stream_weaver.effects.http import HttpxClient  # noqa: E402
from stream_weaver.effects.storage import load_env_file  # noqa: E402
from stream_weaver.providers.services.clients import NuvioClientSync  # noqa: E402

TRAKT_API = "https://api.trakt.tv"
NUVIO_RPC = "https://api.nuvio.tv/rest/v1/rpc"


# ── pure ─────────────────────────────────────────────────────────────────────


def epoch_ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def imdb_id(content_id: str) -> str | None:
    cid = content_id.strip()
    return cid if cid.startswith("tt") and cid[2:].isdigit() else None


def build_history_payload(
    nuvio_items: Iterable[Mapping[str, Any]],
    *,
    already_watched_movies: set[str],
    already_watched_episodes: set[tuple[str, int, int]],
    include_whole_shows: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    """
    Converts Nuvio watched rows into a Trakt `/sync/history` body; returns (payload, stats).

    Series rows without season/episode are Nuvio "whole show watched" markers; with
    `include_whole_shows` they become show-level entries (Trakt marks every aired episode).
    """
    movies: list[dict[str, Any]] = []
    shows: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    whole_shows: list[dict[str, Any]] = []
    stats = {
        "movies": 0,
        "episodes": 0,
        "whole_shows": 0,
        "skipped_no_imdb": 0,
        "skipped_already_watched": 0,
        "skipped_whole_shows": 0,
    }

    for row in nuvio_items:
        iid = imdb_id(str(row.get("content_id") or ""))
        if iid is None:
            stats["skipped_no_imdb"] += 1
            continue
        raw_ts = row.get("watched_at")
        watched_at = (
            epoch_ms_to_iso(int(raw_ts))
            if isinstance(raw_ts, (int, float)) and raw_ts > 0
            else None
        )
        ctype = str(row.get("content_type") or "").lower()
        season, episode = row.get("season"), row.get("episode")

        if ctype == "movie":
            if iid in already_watched_movies:
                stats["skipped_already_watched"] += 1
                continue
            entry: dict[str, Any] = {"ids": {"imdb": iid}}
            if watched_at:
                entry["watched_at"] = watched_at
            movies.append(entry)
            stats["movies"] += 1
        elif isinstance(season, int) and isinstance(episode, int):
            if (iid, season, episode) in already_watched_episodes:
                stats["skipped_already_watched"] += 1
                continue
            ep: dict[str, Any] = {"number": episode}
            if watched_at:
                ep["watched_at"] = watched_at
            shows[iid][season].append(ep)
            stats["episodes"] += 1
        elif ctype == "series" and season is None and episode is None:
            if not include_whole_shows:
                stats["skipped_whole_shows"] += 1
                continue
            show_entry: dict[str, Any] = {"ids": {"imdb": iid}}
            if watched_at:
                show_entry["watched_at"] = watched_at
            whole_shows.append(show_entry)
            stats["whole_shows"] += 1
        else:
            stats["skipped_whole_shows"] += 1

    payload: dict[str, Any] = {}
    if movies:
        payload["movies"] = movies
    show_entries: list[dict[str, Any]] = [
        {
            "ids": {"imdb": iid},
            "seasons": [
                {"number": s, "episodes": sorted(eps, key=lambda e: e["number"])}
                for s, eps in sorted(seasons.items())
            ],
        }
        for iid, seasons in sorted(shows.items())
    ]
    show_entries.extend(whole_shows)
    if show_entries:
        payload["shows"] = show_entries
    return payload, stats


# ── effects ──────────────────────────────────────────────────────────────────


def pull_nuvio_watched(env: Mapping[str, str]) -> list[dict[str, Any]]:
    res = _resolve_nuvio_connection(ROOT / "configs", env)
    if not isinstance(res, Ok) or res.value is None:
        sys.exit("❌ No Nuvio connection resolvable from configs/aiomanager.json + .env")
    conn, password, salt_b64, _ = res.value
    with HttpxClient() as http:
        syncer = NuvioClientSync(http)
        auth = syncer._authenticate_and_resolve_profile(conn, password, salt_b64, env)  # noqa: SLF001
        if not isinstance(auth, Ok):
            sys.exit(f"❌ Nuvio auth failed: {auth.error}")
        headers, profile_idx, _ = auth.value
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            r = http.request_json(
                "POST",
                f"{NUVIO_RPC}/sync_pull_watched_items",
                headers=headers,
                json_body={"p_profile_id": profile_idx, "p_page": page, "p_page_size": 500},
            )
            if not isinstance(r, Ok) or r.value.status_code != 200:
                sys.exit("❌ Nuvio sync_pull_watched_items failed")
            batch = [x for x in (r.value.data or []) if isinstance(x, dict)]
            rows.extend(batch)
            if len(batch) < 500:
                return rows
            page += 1


def trakt_headers(client_id: str, token: str | None = None) -> dict[str, str]:
    h = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": client_id}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def trakt_device_auth(client: httpx.Client, client_id: str, client_secret: str) -> str:
    code = client.post(f"{TRAKT_API}/oauth/device/code", json={"client_id": client_id}).json()
    print(f"\n🔑 Open {code['verification_url']} and enter code:  {code['user_code']}\n")
    deadline = time.time() + int(code.get("expires_in", 600))
    interval = int(code.get("interval", 5))
    while time.time() < deadline:
        time.sleep(interval)
        r = client.post(
            f"{TRAKT_API}/oauth/device/token",
            json={
                "code": code["device_code"],
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if r.status_code == 200:
            print("✅ Trakt authorised")
            return str(r.json()["access_token"])
        if r.status_code not in (400, 429):
            sys.exit(f"❌ Trakt device auth failed: HTTP {r.status_code} {r.text[:200]}")
    sys.exit("❌ Trakt device code expired")


def trakt_already_watched(
    client: httpx.Client, headers: Mapping[str, str]
) -> tuple[set[str], set[tuple[str, int, int]]]:
    movies = {
        str(m["movie"]["ids"].get("imdb"))
        for m in client.get(f"{TRAKT_API}/sync/watched/movies", headers=headers).json()
        if m.get("movie", {}).get("ids", {}).get("imdb")
    }
    episodes: set[tuple[str, int, int]] = set()
    for show in client.get(f"{TRAKT_API}/sync/watched/shows", headers=headers).json():
        iid = show.get("show", {}).get("ids", {}).get("imdb")
        if not iid:
            continue
        for season in show.get("seasons", []):
            for ep in season.get("episodes", []):
                episodes.add((str(iid), int(season["number"]), int(ep["number"])))
    return movies, episodes


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply", action="store_true", help="Actually push to Trakt (default: dry run)"
    )
    ap.add_argument(
        "--no-whole-shows",
        action="store_true",
        help="Skip Nuvio 'entire show watched' markers instead of marking all aired episodes on Trakt",
    )
    args = ap.parse_args()

    env = load_env_file(ROOT / ".env")
    rows = pull_nuvio_watched(env)
    print(f"📥 Nuvio Sync watched items: {len(rows)}")

    client_id = env.get("TRAKT_CLIENT_ID", "")
    client_secret = env.get("TRAKT_CLIENT_SECRET", "")

    watched_movies: set[str] = set()
    watched_eps: set[tuple[str, int, int]] = set()
    token: str | None = None
    with httpx.Client(timeout=60) as client:
        if args.apply:
            if not client_id or not client_secret:
                sys.exit("❌ --apply needs TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET in .env")
            token = trakt_device_auth(client, client_id, client_secret)
            watched_movies, watched_eps = trakt_already_watched(
                client, trakt_headers(client_id, token)
            )
            print(
                f"📚 Trakt already has {len(watched_movies)} movies / {len(watched_eps)} episodes watched"
            )

        payload, stats = build_history_payload(
            rows,
            already_watched_movies=watched_movies,
            already_watched_episodes=watched_eps,
            include_whole_shows=not args.no_whole_shows,
        )
        print("📊", stats)
        if not payload:
            print("✅ Nothing to send.")
            return

        if not args.apply:
            print("\n--- DRY RUN: would POST /sync/history with ---")
            for m in payload.get("movies", [])[:5]:
                print("  movie  ", m)
            for s in payload.get("shows", [])[:3]:
                seasons = s.get("seasons")
                detail = (
                    [(x["number"], len(x["episodes"])) for x in seasons]
                    if seasons
                    else "ENTIRE SHOW"
                )
                print("  show   ", s["ids"], "seasons:", detail)
            print(
                f"  … {stats['movies']} movies, {stats['episodes']} episodes, "
                f"{stats['whole_shows']} entire shows. Re-run with --apply."
            )
            return

        r = client.post(
            f"{TRAKT_API}/sync/history", headers=trakt_headers(client_id, token), json=payload
        )
        if r.status_code != 201:
            sys.exit(f"❌ Trakt rejected the batch: HTTP {r.status_code} {r.text[:400]}")
        body = r.json()
        print("✅ Trakt added:", body.get("added"))
        nf = body.get("not_found", {})
        missing = {k: v for k, v in nf.items() if v}
        if missing:
            print("⚠️  Not matched on Trakt:", missing)


if __name__ == "__main__":
    main()
