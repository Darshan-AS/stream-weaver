#!/usr/bin/env python3
"""
One-off: back-fill Trakt history from Nuvio Sync's watched items (preserving original `watched_at`).

Usage (from the stream-weaver root, with .env populated):

    .venv/bin/python scripts/trakt_backfill.py            # dry run: prints what WOULD be sent
    .venv/bin/python scripts/trakt_backfill.py --apply    # authorise via Trakt device code, then push

Requires in .env:
    TRAKT_CLIENT_ID        -> Trakt Development app (https://developer.trakt.tv/apps; public client)
    TRAKT_CLIENT_SECRET    -> optional; only for legacy confidential Trakt apps
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


def resolve_watched_at(row: Mapping[str, Any]) -> str | None:
    raw_ts = row.get("watched_at")
    if isinstance(raw_ts, (int, float)) and raw_ts > 0:
        return epoch_ms_to_iso(int(raw_ts))
    created = str(row.get("created_at") or "").strip()
    if created:
        try:
            dt = datetime.fromisoformat(created).astimezone(UTC)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            return None
    return None


def imdb_id(content_id: str) -> str | None:
    cid = content_id.strip()
    return cid if cid.startswith("tt") and cid[2:].isdigit() else None


def build_history_payload(
    nuvio_items: Iterable[Mapping[str, Any]],
    *,
    already_watched_movies: set[str],
    already_watched_episodes: set[tuple[str, int, int]],
    already_watched_trakt_movies: set[int] | None = None,
    include_whole_shows: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    """
    Converts Nuvio watched rows into a Trakt `/sync/history` body; returns (payload, stats).

    Series rows without season/episode are Nuvio "whole show watched" markers; with
    `include_whole_shows` they become show-level entries (or expanded episodes when enriched).
    """
    trakt_movies_seen = already_watched_trakt_movies or set()
    movies: list[dict[str, Any]] = []
    shows: dict[str, dict[int, dict[int, dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
    show_ids_override: dict[str, dict[str, Any]] = {}
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
        override_ids = row.get("_trakt_ids")
        iid = imdb_id(str(row.get("content_id") or ""))
        if iid is None and not isinstance(override_ids, dict):
            stats["skipped_no_imdb"] += 1
            continue
        watched_at = resolve_watched_at(row)
        ctype = str(row.get("content_type") or "").lower()
        season, episode = row.get("season"), row.get("episode")
        key = iid or f"trakt:{override_ids['trakt']}"
        if isinstance(override_ids, dict):
            show_ids_override[key] = override_ids

        if ctype == "movie":
            trakt_mid = override_ids.get("trakt") if isinstance(override_ids, dict) else None
            if (iid and iid in already_watched_movies) or (
                isinstance(trakt_mid, int) and trakt_mid in trakt_movies_seen
            ):
                stats["skipped_already_watched"] += 1
                continue
            entry: dict[str, Any] = {
                "ids": override_ids if isinstance(override_ids, dict) else {"imdb": iid}
            }
            if watched_at:
                entry["watched_at"] = watched_at
            movies.append(entry)
            stats["movies"] += 1
        elif isinstance(season, int) and isinstance(episode, int):
            if (key, season, episode) in already_watched_episodes or (
                iid is not None and (iid, season, episode) in already_watched_episodes
            ):
                stats["skipped_already_watched"] += 1
                continue
            ep: dict[str, Any] = {"number": episode}
            if watched_at:
                ep["watched_at"] = watched_at
            shows[key][season][episode] = ep
            stats["episodes"] += 1
        elif ctype == "series" and season is None and episode is None:
            if not include_whole_shows:
                stats["skipped_whole_shows"] += 1
                continue
            show_entry: dict[str, Any] = {
                "ids": override_ids if isinstance(override_ids, dict) else {"imdb": iid},
                "watched_at": watched_at or "released",
            }
            whole_shows.append(show_entry)
            stats["whole_shows"] += 1
        else:
            stats["skipped_whole_shows"] += 1

    payload: dict[str, Any] = {}
    if movies:
        payload["movies"] = movies
    show_entries: list[dict[str, Any]] = [
        {
            "ids": show_ids_override.get(k, {"imdb": k}),
            "seasons": [
                {"number": s, "episodes": [eps[n] for n in sorted(eps)]}
                for s, eps in sorted(seasons.items())
            ],
        }
        for k, seasons in sorted(shows.items())
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
    h = {
        "Content-Type": "application/json",
        "User-Agent": "stream-weaver/1.0",
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def trakt_device_auth(client: httpx.Client, client_id: str, client_secret: str = "") -> str:
    r_code = client.post(
        f"{TRAKT_API}/oauth/device/code",
        headers=trakt_headers(client_id),
        json={"client_id": client_id},
    )
    if r_code.status_code != 200:
        sys.exit(f"❌ Trakt device code request failed: HTTP {r_code.status_code}")
    code = r_code.json()
    print(
        f"\n🔑 Open {code['verification_url']} and enter code:  {code['user_code']}\n",
        flush=True,
    )
    deadline = time.time() + int(code.get("expires_in", 600))
    interval = int(code.get("interval", 5))
    token_body: dict[str, str] = {"code": code["device_code"], "client_id": client_id}
    if client_secret:
        token_body["client_secret"] = client_secret
    while time.time() < deadline:
        time.sleep(interval)
        r = client.post(
            f"{TRAKT_API}/oauth/device/token",
            headers=trakt_headers(client_id),
            json=token_body,
        )
        if r.status_code == 200:
            print("✅ Trakt authorised", flush=True)
            return str(r.json()["access_token"])
        if r.status_code not in (400, 429):
            sys.exit(f"❌ Trakt device auth failed: HTTP {r.status_code}")
    sys.exit("❌ Trakt device code expired")


def trakt_already_watched(
    client: httpx.Client, headers: Mapping[str, str]
) -> tuple[set[str], set[int], set[tuple[str, int, int]], list[str]]:
    movies: set[str] = set()
    trakt_movies: set[int] = set()
    for m in client.get(f"{TRAKT_API}/sync/watched/movies", headers=headers).json():
        ids = m.get("movie", {}).get("ids", {})
        if ids.get("imdb"):
            movies.add(str(ids["imdb"]))
        if isinstance(ids.get("trakt"), int):
            trakt_movies.add(int(ids["trakt"]))

    episodes: set[tuple[str, int, int]] = set()
    show_summaries: list[str] = []
    for show in client.get(f"{TRAKT_API}/sync/watched/shows", headers=headers).json():
        s_obj = show.get("show", {})
        ids = s_obj.get("ids", {})
        iid = ids.get("imdb")
        tid = ids.get("trakt")
        ep_total = 0
        for season in show.get("seasons", []):
            for ep in season.get("episodes", []):
                ep_total += 1
                s_num, e_num = int(season["number"]), int(ep["number"])
                if iid:
                    episodes.add((str(iid), s_num, e_num))
                if isinstance(tid, int):
                    episodes.add((f"trakt:{tid}", s_num, e_num))
        show_summaries.append(f"{s_obj.get('title', '?')} ({ep_total} eps)")
    return movies, trakt_movies, episodes, show_summaries


def _resolve_trakt_show(
    client: httpx.Client, headers: Mapping[str, str], iid: str | None, title: str
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    if iid:
        r = client.get(
            f"{TRAKT_API}/search/imdb/{iid}",
            params={"type": "show", "extended": "full"},
            headers=headers,
        )
        if r.status_code == 200:
            candidates = [x["show"] for x in r.json() if isinstance(x, dict) and "show" in x]
    if not candidates and title:
        r = client.get(
            f"{TRAKT_API}/search/show", params={"query": title, "extended": "full"}, headers=headers
        )
        if r.status_code == 200:
            norm = title.strip().lower()
            candidates = [
                x["show"]
                for x in r.json()
                if isinstance(x, dict)
                and "show" in x
                and str(x["show"].get("title") or "").strip().lower() == norm
            ]
    if not candidates:
        return None
    norm = title.strip().lower()
    candidates.sort(
        key=lambda s: (
            1 if norm and str(s.get("title") or "").strip().lower() == norm else 0,
            int(s.get("aired_episodes") or 0),
        ),
        reverse=True,
    )
    return candidates[0]


def enrich_rows_via_trakt(
    client: httpx.Client,
    headers: Mapping[str, str],
    rows: list[dict[str, Any]],
    watched_movies: set[str],
    include_whole_shows: bool,
) -> list[dict[str, Any]]:
    now_iso = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    seasons_cache: dict[int, list[dict[str, Any]]] = {}

    def get_seasons(trakt_id: int) -> list[dict[str, Any]]:
        if trakt_id not in seasons_cache:
            r = client.get(
                f"{TRAKT_API}/shows/{trakt_id}/seasons",
                params={"extended": "episodes,full"},
                headers=headers,
            )
            seasons_cache[trakt_id] = r.json() if r.status_code == 200 else []
        return seasons_cache[trakt_id]

    enriched: list[dict[str, Any]] = []
    for row in rows:
        cid = str(row.get("content_id") or "").strip()
        iid = imdb_id(cid)
        ctype = str(row.get("content_type") or "").lower()
        title = str(row.get("title") or "").strip()
        season, episode = row.get("season"), row.get("episode")

        if ctype == "movie":
            if iid and iid not in watched_movies:
                r = client.get(
                    f"{TRAKT_API}/search/imdb/{iid}", params={"type": "movie"}, headers=headers
                )
                if r.status_code == 200 and not r.json() and title:
                    sr = client.get(
                        f"{TRAKT_API}/search/movie", params={"query": title}, headers=headers
                    )
                    if sr.status_code == 200:
                        norm = title.lower()
                        matches = [
                            x["movie"]
                            for x in sr.json()
                            if str(x.get("movie", {}).get("title") or "").strip().lower() == norm
                        ]
                        if matches:
                            ids = matches[0]["ids"]
                            print(
                                f"🔎 Movie '{title}' ({iid}) -> Trakt ID {ids.get('trakt')}",
                                flush=True,
                            )
                            enriched.append({**row, "_trakt_ids": {"trakt": ids["trakt"]}})
                            continue
            enriched.append(row)
            continue

        if ctype == "series" and cid.startswith("kitsu:") and title:
            show = _resolve_trakt_show(client, headers, None, title)
            if show:
                ids = show["ids"]
                print(
                    f"🔎 Kitsu '{title}' ({cid}) -> Trakt '{show.get('title')}' ({ids.get('imdb') or ids.get('trakt')})",
                    flush=True,
                )
                enriched.append(
                    {
                        **row,
                        "content_id": ids.get("imdb") or cid,
                        "_trakt_ids": {"trakt": ids["trakt"]},
                    }
                )
                continue

        if ctype == "series" and season is None and episode is None and include_whole_shows:
            show = _resolve_trakt_show(client, headers, iid, title)
            if show and isinstance(show.get("ids", {}).get("trakt"), int):
                tid = int(show["ids"]["trakt"])
                seasons_data = get_seasons(tid)
                ep_rows: list[dict[str, Any]] = []
                for s_obj in seasons_data:
                    s_num = int(s_obj.get("number", 0))
                    if s_num <= 0:
                        continue
                    for ep_obj in s_obj.get("episodes", []):
                        aired = str(ep_obj.get("first_aired") or "")
                        if aired and aired <= now_iso:
                            ep_rows.append(
                                {
                                    **row,
                                    "content_id": show["ids"].get("imdb") or cid,
                                    "_trakt_ids": {"trakt": tid},
                                    "season": s_num,
                                    "episode": int(ep_obj["number"]),
                                }
                            )
                if ep_rows:
                    print(
                        f"📺 Whole show '{show.get('title')}' -> {len(ep_rows)} aired regular episodes",
                        flush=True,
                    )
                    enriched.extend(ep_rows)
                    continue

        if (
            ctype == "series"
            and isinstance(season, int)
            and isinstance(episode, int)
            and season > 5
        ):
            show = _resolve_trakt_show(client, headers, iid, title)
            if show and isinstance(show.get("ids", {}).get("trakt"), int):
                tid = int(show["ids"]["trakt"])
                reg_seasons = [s for s in get_seasons(tid) if int(s.get("number", 0)) > 0]
                valid_nums = {int(s["number"]) for s in reg_seasons}
                if season not in valid_nums:
                    mapped: tuple[int, int] | None = None
                    if season > 100 and len(reg_seasons) == 1:
                        s1_eps = {int(e["number"]) for e in reg_seasons[0].get("episodes", [])}
                        if episode in s1_eps:
                            mapped = (int(reg_seasons[0]["number"]), episode)
                    if mapped is None and season == 46360 and 3 in valid_nums:
                        mapped = (3, episode)
                    if mapped is None and season > 100:
                        for s_obj in reg_seasons:
                            for e_obj in s_obj.get("episodes", []):
                                if e_obj.get("number_abs") == episode:
                                    mapped = (int(s_obj["number"]), int(e_obj["number"]))
                                    break
                    if mapped is not None:
                        print(
                            f"🔎 Remapped '{show.get('title')}' S{season}E{episode} -> S{mapped[0]}E{mapped[1]}",
                            flush=True,
                        )
                        enriched.append(
                            {
                                **row,
                                "content_id": show["ids"].get("imdb") or cid,
                                "_trakt_ids": {"trakt": tid},
                                "season": mapped[0],
                                "episode": mapped[1],
                            }
                        )
                        continue
                    print(
                        f"⚠️  Unmappable season for '{show.get('title')}' S{season}E{episode} (Trakt seasons: {sorted(valid_nums)})",
                        flush=True,
                    )

        enriched.append(row)
    return enriched


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
    watched_trakt_movies: set[int] = set()
    watched_eps: set[tuple[str, int, int]] = set()
    token: str | None = None
    with httpx.Client(timeout=60) as client:
        if args.apply:
            if not client_id:
                sys.exit("❌ --apply needs TRAKT_CLIENT_ID in .env")
            token = trakt_device_auth(client, client_id, client_secret)
            hdrs = trakt_headers(client_id, token)
            watched_movies, watched_trakt_movies, watched_eps, show_list = trakt_already_watched(
                client, hdrs
            )
            print(
                f"📚 Trakt currently has {len(watched_trakt_movies)} movies across {len(show_list)} watched shows",
                flush=True,
            )
            rows = enrich_rows_via_trakt(
                client, hdrs, rows, watched_movies, not args.no_whole_shows
            )

        payload, stats = build_history_payload(
            rows,
            already_watched_movies=watched_movies,
            already_watched_episodes=watched_eps,
            already_watched_trakt_movies=watched_trakt_movies,
            include_whole_shows=not args.no_whole_shows,
        )
        print("📊", stats, flush=True)
        if not payload:
            print("✅ Nothing new to send — Trakt is completely caught up!", flush=True)
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
        print("✅ Trakt added:", body.get("added"), flush=True)
        nf = body.get("not_found", {})
        missing = {k: v for k, v in nf.items() if v}
        if missing:
            print("⚠️  Not matched on Trakt:", missing, flush=True)
        _, final_movies, _, final_shows = trakt_already_watched(
            client, trakt_headers(client_id, token)
        )
        print(
            f"🎉 Final Trakt totals: {len(final_movies)} watched movies, {len(final_shows)} watched shows -> {final_shows}",
            flush=True,
        )


if __name__ == "__main__":
    main()
