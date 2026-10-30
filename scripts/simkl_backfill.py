#!/usr/bin/env python3
"""
One-off: back-fill Simkl history from Nuvio Sync's watched items (preserving original `watched_at`).

Usage (from the stream-weaver root, with .env populated):

    .venv/bin/python scripts/simkl_backfill.py            # dry run: prints what WOULD be sent
    .venv/bin/python scripts/simkl_backfill.py --apply    # authorise via Simkl PIN, then push

Requires in .env:
    SIMKL_CLIENT_ID -> temporary Simkl API app (https://simkl.com/settings/developer/)
    NUVIO_SUPABASE_ANON_KEY + AIOMANAGER_SYNC_PASSWORD -> already used by `sw` for Nuvio sync

Supports IMDb movies/shows, whole-show completion markers (`status: "completed"`), `kitsu:*`
anime rows, and Nuvio's Kitsu-ID-in-season anime rows (e.g. `season: 1555` -> `ids.kitsu: 1555`).
Items already marked watched/completed on Simkl are skipped so re-running is idempotent.
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

SIMKL_API = "https://api.simkl.com"
NUVIO_RPC = "https://api.nuvio.tv/rest/v1/rpc"


# ── pure ─────────────────────────────────────────────────────────────────────


def epoch_ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_watched_at(row: Mapping[str, Any]) -> str | None:
    raw_ts = row.get("watched_at")
    if isinstance(raw_ts, (int, float)) and raw_ts >= 10_000_000_000:
        return epoch_ms_to_iso(int(raw_ts))
    created = str(row.get("created_at") or "").strip()
    if created:
        try:
            dt = datetime.fromisoformat(created).astimezone(UTC)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    return None


def imdb_id(content_id: str) -> str | None:
    cid = content_id.strip()
    return cid if cid.startswith("tt") and cid[2:].isdigit() else None


def kitsu_id_from_row(row: Mapping[str, Any]) -> int | None:
    cid = str(row.get("content_id") or "").strip()
    if cid.startswith("kitsu:"):
        tail = cid.split(":", 1)[1].strip()
        return int(tail) if tail.isdigit() else None
    season = row.get("season")
    iid = imdb_id(cid)
    # Nuvio quirk: playing `kitsu:<kitsuId>:<ep>` from an IMDb details screen stores kitsuId in `season`
    if isinstance(season, int) and (season > 100 or (iid == "tt0409591" and season == 11)):
        return season
    return None


def build_simkl_history_payload(
    nuvio_items: Iterable[Mapping[str, Any]],
    *,
    already_watched_movies: set[str],
    already_watched_simkl_movies: set[int] | None = None,
    already_completed_shows: set[str] | None = None,
    already_watched_episodes: set[tuple[str, int, int]] | None = None,
    already_watched_kitsu_eps: set[tuple[int, int]] | None = None,
    anime_imdb_ids: set[str] | None = None,
    include_whole_shows: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    simkl_movies_seen = already_watched_simkl_movies or set()
    completed_shows = already_completed_shows or set()
    watched_eps = already_watched_episodes or set()
    watched_kitsu = already_watched_kitsu_eps or set()
    anime_iids = anime_imdb_ids or set()

    movies: list[dict[str, Any]] = []
    season_shows: dict[str, dict[str, Any]] = {}
    kitsu_shows: dict[int, dict[str, Any]] = {}
    whole_shows: dict[str, dict[str, Any]] = {}

    stats = {
        "movies": 0,
        "episodes": 0,
        "kitsu_episodes": 0,
        "whole_shows": 0,
        "skipped_unresolvable": 0,
        "skipped_already_watched": 0,
        "skipped_whole_shows": 0,
    }

    for row in nuvio_items:
        cid = str(row.get("content_id") or "").strip()
        iid = imdb_id(cid)
        kid = kitsu_id_from_row(row)
        override_ids = row.get("_simkl_ids")
        ctype = str(row.get("content_type") or "").lower()
        title = str(row.get("title") or "").strip() or None
        watched_at = resolve_watched_at(row)
        season, episode = row.get("season"), row.get("episode")

        if ctype == "movie":
            if iid is None and not isinstance(override_ids, dict):
                stats["skipped_unresolvable"] += 1
                continue
            sid = override_ids.get("simkl") if isinstance(override_ids, dict) else None
            if (iid and iid in already_watched_movies) or (
                isinstance(sid, int) and sid in simkl_movies_seen
            ):
                stats["skipped_already_watched"] += 1
                continue
            entry: dict[str, Any] = {
                "ids": override_ids if isinstance(override_ids, dict) else {"imdb": iid}
            }
            if title:
                entry["title"] = title
            if watched_at:
                entry["watched_at"] = watched_at
            movies.append(entry)
            stats["movies"] += 1
            continue

        if ctype == "series" and season is None and episode is None:
            if iid is None and not isinstance(override_ids, dict):
                stats["skipped_unresolvable"] += 1
                continue
            if not include_whole_shows:
                stats["skipped_whole_shows"] += 1
                continue
            key = iid or f"simkl:{override_ids['simkl']}"
            if key in completed_shows:
                stats["skipped_already_watched"] += 1
                continue
            w_entry: dict[str, Any] = {
                "ids": override_ids if isinstance(override_ids, dict) else {"imdb": iid},
                "status": "completed",
            }
            if title:
                w_entry["title"] = title
            if watched_at:
                w_entry["watched_at"] = watched_at
            whole_shows[key] = w_entry
            stats["whole_shows"] += 1
            continue

        if ctype == "series" and kid is not None and isinstance(episode, int):
            if (kid, episode) in watched_kitsu:
                stats["skipped_already_watched"] += 1
                continue
            bucket = kitsu_shows.setdefault(
                kid,
                {"ids": {"kitsu": kid}, **({"title": title} if title else {}), "episodes": {}},
            )
            ep_obj: dict[str, Any] = {"number": episode}
            if watched_at:
                ep_obj["watched_at"] = watched_at
            bucket["episodes"][episode] = ep_obj
            stats["kitsu_episodes"] += 1
            continue

        if (
            ctype == "series"
            and iid is not None
            and isinstance(season, int)
            and isinstance(episode, int)
        ):
            if iid in completed_shows or (iid, season, episode) in watched_eps:
                stats["skipped_already_watched"] += 1
                continue
            s_bucket = season_shows.setdefault(
                iid,
                {
                    "ids": override_ids if isinstance(override_ids, dict) else {"imdb": iid},
                    **({"title": title} if title else {}),
                    "seasons": defaultdict(dict),
                },
            )
            ep_entry: dict[str, Any] = {"number": episode}
            if watched_at:
                ep_entry["watched_at"] = watched_at
            s_bucket["seasons"][season][episode] = ep_entry
            stats["episodes"] += 1
            continue

        stats["skipped_unresolvable"] += 1

    # Whole-show completion supersedes individual episodes for the same IMDb show in the same batch
    shows_list: list[dict[str, Any]] = list(whole_shows.values())
    for iid, data in sorted(season_shows.items()):
        if iid in whole_shows:
            continue
        seasons_out = [
            {"number": s_num, "episodes": [eps[n] for n in sorted(eps)]}
            for s_num, eps in sorted(data["seasons"].items())
        ]
        show_item: dict[str, Any] = {"ids": data["ids"], "seasons": seasons_out}
        if data.get("title"):
            show_item["title"] = data["title"]
        if iid in anime_iids:
            show_item["use_tvdb_anime_seasons"] = True
        shows_list.append(show_item)

    for kid, data in sorted(kitsu_shows.items()):
        eps_out = [data["episodes"][n] for n in sorted(data["episodes"])]
        k_item: dict[str, Any] = {"ids": {"kitsu": kid}, "episodes": eps_out}
        if data.get("title"):
            k_item["title"] = data["title"]
        shows_list.append(k_item)

    payload: dict[str, Any] = {}
    if movies:
        payload["movies"] = movies
    if shows_list:
        payload["shows"] = shows_list
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


def simkl_params(client_id: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    base = {"client_id": client_id, "app-name": "stream-weaver", "app-version": "1.0"}
    if extra:
        base.update(extra)
    return base


def simkl_headers(token: str | None = None, json_body: bool = False) -> dict[str, str]:
    h = {"Accept": "application/json", "User-Agent": "stream-weaver/1.0"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def simkl_pin_auth(client: httpx.Client, client_id: str) -> str:
    r = client.get(
        f"{SIMKL_API}/oauth/pin",
        params=simkl_params(client_id),
        headers=simkl_headers(),
    )
    if r.status_code != 200:
        sys.exit(f"❌ Simkl PIN request failed: HTTP {r.status_code}")
    data = r.json()
    user_code = str(data.get("user_code") or "").strip()
    verify_url = str(
        data.get("verification_url") or data.get("verification_uri") or "https://simkl.com/pin"
    ).strip()
    if data.get("result") != "OK" or not user_code:
        sys.exit("❌ Unexpected Simkl PIN response")
    print(f"\n🔑 Open {verify_url} and enter code:  {user_code}\n", flush=True)
    deadline = time.time() + int(data.get("expires_in") or 900)
    interval = max(2, int(data.get("interval") or 5))
    while time.time() < deadline:
        time.sleep(interval)
        pr = client.get(
            f"{SIMKL_API}/oauth/pin/{user_code}",
            params=simkl_params(client_id),
            headers=simkl_headers(),
        )
        if pr.status_code != 200:
            sys.exit(f"❌ Simkl PIN poll failed: HTTP {pr.status_code}")
        pdata = pr.json()
        if pdata.get("result") == "OK" and pdata.get("access_token"):
            print("✅ Simkl authorised", flush=True)
            return str(pdata["access_token"])
        if pdata.get("result") != "KO":
            sys.exit("❌ Simkl PIN invalidated or unexpected state")
    sys.exit("❌ Simkl PIN code expired")


def simkl_already_watched(
    client: httpx.Client, client_id: str, token: str
) -> tuple[
    set[str],
    set[int],
    set[str],
    set[tuple[str, int, int]],
    set[tuple[int, int]],
    dict[str, int],
]:
    r = client.get(
        f"{SIMKL_API}/sync/all-items/",
        params=simkl_params(
            client_id,
            {
                "extended": "full_anime_seasons",
                "episode_watched_at": "yes",
                "include_all_episodes": "yes",
            },
        ),
        headers=simkl_headers(token),
    )
    if r.status_code != 200:
        sys.exit(f"❌ Simkl /sync/all-items failed: HTTP {r.status_code}")
    body = r.json() if r.text.strip() and r.json() else {}
    if not isinstance(body, dict):
        body = {}

    watched_movies: set[str] = set()
    watched_simkl_movies: set[int] = set()
    completed_shows: set[str] = set()
    watched_eps: set[tuple[str, int, int]] = set()
    watched_kitsu: set[tuple[int, int]] = set()

    movies_list = body.get("movies") or []
    shows_list = body.get("shows") or []
    anime_list = body.get("anime") or []

    for m in movies_list:
        if m.get("status") != "completed":
            continue
        ids = (m.get("movie") or {}).get("ids") or {}
        if ids.get("imdb"):
            watched_movies.add(str(ids["imdb"]))
        if isinstance(ids.get("simkl"), int):
            watched_simkl_movies.add(int(ids["simkl"]))

    for bucket in (shows_list, anime_list):
        for entry in bucket:
            media = entry.get("show") or entry.get("movie") or {}
            ids = media.get("ids") or {}
            iid = str(ids["imdb"]) if ids.get("imdb") else None
            kid = int(ids["kitsu"]) if str(ids.get("kitsu") or "").isdigit() else None
            if entry.get("status") == "completed" and entry.get("anime_type") == "movie":
                if iid:
                    watched_movies.add(iid)
                if isinstance(ids.get("simkl"), int):
                    watched_simkl_movies.add(int(ids["simkl"]))
                continue
            if (
                entry.get("status") == "completed"
                and iid
                and entry.get("not_aired_episodes_count", 0) == 0
            ):
                completed_shows.add(iid)
            for s_obj in entry.get("seasons") or []:
                s_num = s_obj.get("number")
                for ep in s_obj.get("episodes") or []:
                    ep_num = ep.get("number")
                    if iid and isinstance(s_num, int) and isinstance(ep_num, int):
                        watched_eps.add((iid, s_num, ep_num))
                    if kid is not None and isinstance(ep_num, int):
                        watched_kitsu.add((kid, ep_num))

    summary = {
        "movies": len(watched_simkl_movies),
        "shows": len(shows_list),
        "anime": len(anime_list),
    }
    return (
        watched_movies,
        watched_simkl_movies,
        completed_shows,
        watched_eps,
        watched_kitsu,
        summary,
    )


def detect_anime_series_iids(
    client: httpx.Client,
    client_id: str,
    token: str,
    rows: Iterable[Mapping[str, Any]],
) -> set[str]:
    anime_iids: set[str] = set()
    checked_series_iids: set[str] = set()

    for row in rows:
        cid = str(row.get("content_id") or "").strip()
        iid = imdb_id(cid)
        ctype = str(row.get("content_type") or "").lower()
        if (
            ctype == "series"
            and iid
            and kitsu_id_from_row(row) is None
            and row.get("season") is not None
            and iid not in checked_series_iids
        ):
            checked_series_iids.add(iid)
            time.sleep(0.11)
            r = client.get(
                f"{SIMKL_API}/search/id",
                params=simkl_params(client_id, {"imdb": iid}),
                headers=simkl_headers(token),
            )
            items = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
            if any(isinstance(x, dict) and x.get("type") == "anime" for x in items):
                anime_iids.add(iid)

    if anime_iids:
        print(f"🎌 TVDB-seasoned anime detected on Simkl: {sorted(anime_iids)}", flush=True)
    return anime_iids


def retry_unmatched_movies_by_title(
    client: httpx.Client,
    client_id: str,
    token: str,
    not_found_movies: list[dict[str, Any]],
    rows: Iterable[Mapping[str, Any]],
) -> None:
    if not not_found_movies:
        return
    by_imdb = {
        imdb_id(str(r.get("content_id") or "")): r
        for r in rows
        if str(r.get("content_type") or "").lower() == "movie"
    }
    retry_items: list[dict[str, Any]] = []
    for nf in not_found_movies:
        nf_iid = (nf.get("ids") or {}).get("imdb")
        src = by_imdb.get(nf_iid)
        title = str((src or nf).get("title") or "").strip()
        if not title:
            continue
        time.sleep(0.12)
        sr = client.get(
            f"{SIMKL_API}/search/movie",
            params=simkl_params(client_id, {"q": title}),
            headers=simkl_headers(token),
        )
        if sr.status_code == 200 and isinstance(sr.json(), list):
            norm = title.lower()
            for cand in sr.json():
                if str(cand.get("title") or "").strip().lower() == norm:
                    ids_obj = cand.get("ids") or {}
                    sid = ids_obj.get("simkl_id") or ids_obj.get("simkl")
                    if isinstance(sid, int):
                        print(f"🔎 Retrying '{title}' ({nf_iid}) via Simkl ID {sid}", flush=True)
                        entry: dict[str, Any] = {"title": title, "ids": {"simkl": sid}}
                        watched_at = resolve_watched_at(src) if src else None
                        if watched_at:
                            entry["watched_at"] = watched_at
                        retry_items.append(entry)
                        break
    if retry_items:
        time.sleep(1.1)
        rr = client.post(
            f"{SIMKL_API}/sync/history",
            params=simkl_params(client_id),
            headers=simkl_headers(token, json_body=True),
            json={"movies": retry_items},
        )
        if rr.status_code in range(200, 300):
            print("✅ Title-fallback movies added:", len(retry_items), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply", action="store_true", help="Actually push to Simkl (default: dry run)"
    )
    ap.add_argument(
        "--no-whole-shows",
        action="store_true",
        help="Skip Nuvio 'entire show watched' markers instead of marking shows completed on Simkl",
    )
    args = ap.parse_args()

    env = load_env_file(ROOT / ".env")
    rows = pull_nuvio_watched(env)
    print(f"📥 Nuvio Sync watched items: {len(rows)}", flush=True)

    client_id = env.get("SIMKL_CLIENT_ID", "").strip()

    watched_movies: set[str] = set()
    watched_simkl_movies: set[int] = set()
    completed_shows: set[str] = set()
    watched_eps: set[tuple[str, int, int]] = set()
    watched_kitsu: set[tuple[int, int]] = set()
    anime_iids: set[str] = set()
    token: str | None = None

    with httpx.Client(timeout=60) as client:
        if args.apply:
            if not client_id:
                sys.exit("❌ --apply needs SIMKL_CLIENT_ID in .env")
            token = simkl_pin_auth(client, client_id)
            (
                watched_movies,
                watched_simkl_movies,
                completed_shows,
                watched_eps,
                watched_kitsu,
                pre_summary,
            ) = simkl_already_watched(client, client_id, token)
            print(f"📚 Simkl currently has: {pre_summary}", flush=True)
            anime_iids = detect_anime_series_iids(client, client_id, token, rows)

        payload, stats = build_simkl_history_payload(
            rows,
            already_watched_movies=watched_movies,
            already_watched_simkl_movies=watched_simkl_movies,
            already_completed_shows=completed_shows,
            already_watched_episodes=watched_eps,
            already_watched_kitsu_eps=watched_kitsu,
            anime_imdb_ids=anime_iids,
            include_whole_shows=not args.no_whole_shows,
        )
        print("📊", stats, flush=True)
        if not payload:
            print("✅ Nothing new to send — Simkl is completely caught up!", flush=True)
            return

        if not args.apply:
            print("\n--- DRY RUN: would POST /sync/history to Simkl with ---")
            for m in payload.get("movies", [])[:4]:
                print("  movie  ", m)
            for s in payload.get("shows", [])[:6]:
                if s.get("status") == "completed":
                    detail: Any = "STATUS=completed"
                elif s.get("episodes"):
                    detail = f"flat episodes: {[e['number'] for e in s['episodes']]}"
                else:
                    detail = [(x["number"], len(x["episodes"])) for x in s.get("seasons", [])]
                print("  show   ", s.get("title"), s["ids"], "->", detail)
            print(
                f"  … {stats['movies']} movies, {stats['episodes']} seasoned episodes, "
                f"{stats['kitsu_episodes']} Kitsu episodes, {stats['whole_shows']} completed shows. "
                "Re-run with --apply."
            )
            return

        assert token is not None
        r = client.post(
            f"{SIMKL_API}/sync/history",
            params=simkl_params(client_id),
            headers=simkl_headers(token, json_body=True),
            json=payload,
        )
        if r.status_code not in range(200, 300):
            sys.exit(f"❌ Simkl rejected the batch: HTTP {r.status_code} {r.text[:400]}")
        body = r.json() if r.text.strip() else {}
        added = body.get("added") or {}
        print(
            "✅ Simkl added:",
            {k: (len(v) if isinstance(v, list) else v) for k, v in added.items()},
            flush=True,
        )
        nf = body.get("not_found") or {}
        nf_movies = nf.get("movies") if isinstance(nf.get("movies"), list) else []
        if nf_movies:
            retry_unmatched_movies_by_title(client, client_id, token, nf_movies, rows)
        missing = {k: v for k, v in nf.items() if v and k != "movies"}
        if missing:
            print("⚠️  Not matched on Simkl:", missing, flush=True)

        time.sleep(1.2)
        *_, post_summary = simkl_already_watched(client, client_id, token)
        print(f"🎉 Final Simkl totals: {post_summary}", flush=True)


if __name__ == "__main__":
    main()
