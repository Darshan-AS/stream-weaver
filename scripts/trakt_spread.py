#!/usr/bin/env python3
"""
Back-date old watches on Trakt from a manifest: exact per-episode dates, date ranges, or air dates.

For things watched before any tracker existed. Each episode gets its own `watched_at`, so Trakt
history shows a plausible timeline instead of hundreds of plays on one day.

Usage (from the repo root; tenant via STREAM_WEAVER_DIR or STREAM_WEAVER_TENANT, default `don`):

    .venv/bin/python scripts/trakt_spread.py past.json            # dry run: prints the plan
    .venv/bin/python scripts/trakt_spread.py past.json --apply    # Trakt device auth, then push

Manifest (JSON list; see scripts/trakt_spread.example.json). Every entry needs `title` (+ optional
`year`) or `id` (IMDb id or Trakt slug; beats title search). Shows take ONE timing mode:

    {"title": "Naruto", "year": 2002, "from": "2023-09-17", "to": "2023-11-10"}     # spread over a range
    {"id": "tt2560140", "only": ["S1-S3", "S4E1-16"], "from": "2021-05-20", "to": "2021-06-12"}
    {"title": "Loki", "only": ["S2"], "when": "released"}                            # each episode on its air date
    {"title": "House", "exact": {"2023-10-29": ["S1E1|Pilot", "S1E2", "S1E3"]}}      # known dates
    {"type": "movie", "title": "Tenet", "year": 2020, "on": "2021-04-01"}

`only` selectors: `S2`, `S1-S3`, `S4E5`, `S4E1-16` (default: every aired non-special episode).
`"rewatch": true` allows a second play of something already in history. Dates may be YYYY,
YYYY-MM or YYYY-MM-DD; a partial `from` means the start of that period, a partial `to` its end.
In `exact`, the optional `|Title` is checked against Trakt's episode title to catch numbering
mismatches between services.

Timestamps use two daily viewing slots (BRUNCH_HOUR and DINNER_HOUR, local time): a day's
episodes are split evenly between them; a single episode or a movie goes to the dinner slot.

Every --apply first writes an undo log next to the manifest (`trakt_undo/<time>.json`);
`--undo <log> --apply` removes exactly those plays and nothing else.

Rewatches. Observed on a free Trakt account (Nov 2026), contrary to the API reference: adding a
play to an item that ALREADY has one does not add a rewatch; Trakt answers `updated` and re-dates
the existing play. Several plays of one item sent in the SAME request, while the item has none,
are all stored. So whenever a run adds a play to an item that already has plays (a `rewatch`
entry, or `--allow-repeat`), the script removes the existing plays, waits for the removal to
settle, and sends old and new plays together; plays of one item are never split across requests.

`--replace unknown` re-dates items whose existing plays all have no date (bulk "unknown date"
marks); `--replace all` replaces whatever plays the planned items have; `--replace-window
FROM..TO` replaces only the existing plays dated inside that range and keeps the items' others.

Requires TRAKT_CLIENT_ID in the tenant .env. The Trakt user token from the device sign-in is
cached in the tenant's git-ignored `.cache/`. With --apply, anything Trakt already has is skipped
(for `rewatch` entries: anything with a play within 36 hours of the planned time), so re-running
never adds duplicate plays. A dry run reads existing history only if a cached sign-in exists.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta, tzinfo
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trakt_backfill import (  # noqa: E402
    TENANT_DIR,
    TRAKT_API,
    load_env_file,
    trakt_device_auth,
    trakt_headers,
)

BRUNCH_HOUR = 10  # local start of the morning viewing slot (about 10:00-12:00)
DINNER_HOUR = 18  # local start of the evening viewing slot (about 18:00-20:00)
BATCH = 500
REWATCH_WINDOW = timedelta(hours=36)
SETTLE_SECONDS = 20  # Trakt needs a moment before a removal shows up in history
SETTLE_POLLS = 12
TOKEN_CACHE = TENANT_DIR / ".cache" / "trakt_token"  # git-ignored (tenants/*/.cache/)

Episode = Mapping[str, Any]
Planned = tuple[Episode, datetime]
Play = dict[str, Any]  # kind, parent, item, watched_at, label (+ history_id / resent)
Existing = tuple[datetime, int]  # (watched_at, history id)
Window = tuple[datetime, datetime]  # inclusive range of existing plays to replace


# ── pure ─────────────────────────────────────────────────────────────────────


def parse_bound(raw: str, *, end: bool) -> date:
    """Parses YYYY / YYYY-MM / YYYY-MM-DD; partial dates snap to the period's start or end."""
    parts = [int(p) for p in str(raw).strip().split("-")]
    if len(parts) == 1:
        return date(parts[0], 12, 31) if end else date(parts[0], 1, 1)
    if len(parts) == 2:
        last = calendar.monthrange(parts[0], parts[1])[1]
        return date(parts[0], parts[1], last if end else 1)
    return date(parts[0], parts[1], parts[2])


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def label(ep: Episode) -> str:
    return f"S{int(ep['season']):02d}E{int(ep['number']):02d}"


def parse_selector(sel: str) -> tuple[int, int, int, int]:
    """`S2` / `S1-S3` / `S4E5` / `S4E1-16` -> (season_lo, season_hi, episode_lo, episode_hi)."""
    m = re.fullmatch(r"S(\d+)(?:-S?(\d+))?(?:E(\d+)(?:-E?(\d+))?)?", sel.strip().upper())
    if not m:
        raise ValueError(f"Bad episode selector: {sel!r}")
    s_lo, s_hi, e_lo, e_hi = m.groups()
    if e_lo is None:
        return int(s_lo), int(s_hi or s_lo), 1, 10**6
    return int(s_lo), int(s_lo), int(e_lo), int(e_hi or e_lo)


def select_episodes(
    episodes: Sequence[Episode], only: Sequence[str] | None, *, now: datetime
) -> tuple[list[Episode], int]:
    """Aired episodes matching `only` (default: all non-specials), in order; plus unaired count."""
    ranges = [parse_selector(s) for s in only or []]
    out: list[Episode] = []
    unaired = 0
    for ep in sorted(episodes, key=lambda e: (int(e["season"]), int(e["number"]))):
        season, number = int(ep["season"]), int(ep["number"])
        if ranges:
            if not any(a <= season <= b and c <= number <= d for a, b, c, d in ranges):
                continue
        elif season == 0:
            continue
        aired = ep.get("first_aired")
        if not aired or parse_iso(aired) > now:
            unaired += 1
            continue
        out.append(ep)
    return out, unaired


def day_slots(day: date, runtimes: Sequence[int], tz: tzinfo) -> list[datetime]:
    """
    Start times for the episodes watched on one day, in order, using the two usual viewing slots.

    The first half (rounded up) goes back-to-back from BRUNCH_HOUR, the rest from DINNER_HOUR;
    a single episode goes to the dinner slot. Six episodes therefore split 3 + 3.
    """
    n = len(runtimes)
    morning = (n + 1) // 2 if n > 1 else 0
    out: list[datetime] = []
    for hour, chunk in ((BRUNCH_HOUR, runtimes[:morning]), (DINNER_HOUR, runtimes[morning:])):
        cursor = datetime(day.year, day.month, day.day, hour, tzinfo=tz)
        for runtime in chunk:
            out.append(cursor)
            cursor += timedelta(minutes=max(int(runtime or 0), 1))
    return out


def spread_schedule(runtimes: Sequence[int], start: date, end: date, tz: tzinfo) -> list[datetime]:
    """
    One timestamp per episode, in order, spread evenly over [start, end].

    Episode i lands on day floor(i * days / n); each day's episodes are then placed in the
    brunch and dinner slots by `day_slots`.
    """
    if end < start:
        raise ValueError(f"'to' ({end}) is before 'from' ({start})")
    n, days = len(runtimes), (end - start).days + 1
    by_day: dict[date, list[int]] = {}
    for i, runtime in enumerate(runtimes):
        by_day.setdefault(start + timedelta(days=i * days // n), []).append(runtime)
    return [slot for day, chunk in by_day.items() for slot in day_slots(day, chunk, tz)]


def runtime_of(ep: Episode) -> int:
    return int(ep.get("runtime") or 24)


_PARTS = {"iii": "3", "ii": "2", "i": "1"}


def norm_title(raw: str) -> str:
    """Comparison key for titles: lowercase alphanumerics, no articles, `Part II` / `(2)` -> `2`."""
    t = re.sub(
        r"\b(?:part|pt)\.?\s*(iii|ii|i|\d+)\b", lambda m: _PARTS.get(m[1], m[1]), raw.lower()
    )
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\bthe\b", "", t))


def titles_close(a: str, b: str) -> bool:
    """Loose episode-title equality across services (spelling, punctuation, dropped words)."""
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 8 and (a in b or b in a)):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def match_exact(
    season: int, number: int, want_title: str, episodes: Sequence[Episode]
) -> tuple[Episode | None, str]:
    """
    Finds the Trakt episode for a source `SxEy|Title`: returns (episode, how).

    Services number episodes differently (split two-parters, reordered or regrouped seasons), so
    the title wins: `how` is "number" when the numbered slot agrees (or no title was given),
    "title" when another episode of the show carries that title, "mismatch" when nothing does
    but the numbered slot's title is at least similar (a retitled episode) and is used anyway,
    and "missing" when there is no such slot or its title is unrelated (marking it would record
    an episode that was never watched).
    """
    numbered = next(
        (e for e in episodes if int(e["season"]) == season and int(e["number"]) == number), None
    )
    if not want_title:
        return numbered, "number" if numbered else "missing"
    want = norm_title(want_title)

    def key(e: Episode) -> str:
        return norm_title(str(e.get("title") or ""))

    regular = [e for e in episodes if int(e["season"]) > 0]
    # Identical titles beat merely similar ones, so `X (1)` is never taken for `X (2)`.
    if numbered is not None and key(numbered) == want:
        return numbered, "number"
    if (same := next((e for e in regular if key(e) == want), None)) is not None:
        return same, "title"
    if numbered is not None and titles_close(want_title, str(numbered.get("title") or "")):
        return numbered, "number"
    close = [e for e in regular if titles_close(want_title, str(e.get("title") or ""))]
    if close:
        return max(close, key=lambda e: SequenceMatcher(None, want, key(e)).ratio()), "title"
    if numbered is None:
        return None, "missing"
    similar = SequenceMatcher(None, want, key(numbered)).ratio() >= 0.5
    return (numbered, "mismatch") if similar else (None, "missing")


def plan_show(
    entry: Mapping[str, Any], episodes: Sequence[Episode], tz: tzinfo, *, now: datetime
) -> tuple[list[Planned], dict[str, int], list[str]]:
    """
    Applies an entry's timing mode to a show's episodes: returns (planned, stats, warnings).

    A planned time earlier than the episode's air date is clamped to the air date.
    """
    stats = {
        "planned": 0,
        "unaired": 0,
        "clamped_to_air_date": 0,
        "missing": 0,
        "title_mismatch": 0,
        "matched_by_title": 0,
        "merged_parts": 0,
    }
    warnings: list[str] = []
    planned: list[Planned] = []

    if "exact" in entry:
        for day_raw, items in sorted(entry["exact"].items()):
            todays: list[Episode] = []
            for item in items:
                sel, _, want_title = str(item).partition("|")
                season, _, number, _ = parse_selector(sel)
                ep, how = match_exact(season, number, want_title, episodes)
                if how == "missing" or ep is None:
                    stats["missing"] += 1
                    warnings.append(f"{sel} {want_title!r}: not on Trakt")
                    continue
                if how == "title":
                    stats["matched_by_title"] += 1
                elif how == "mismatch":
                    stats["title_mismatch"] += 1
                    warnings.append(
                        f"{sel}: source says {want_title!r}, Trakt says {ep.get('title')!r}"
                    )
                if any(ep is seen for seen in todays):
                    stats["merged_parts"] += 1  # two source parts are one episode on Trakt
                    continue
                todays.append(ep)
            slots = day_slots(parse_bound(day_raw, end=False), [runtime_of(e) for e in todays], tz)
            planned.extend(zip(todays, slots, strict=True))
    else:
        chosen, stats["unaired"] = select_episodes(episodes, entry.get("only"), now=now)
        if entry.get("when") == "released":
            cursor: datetime | None = None
            for ep in chosen:  # a season dropped at one instant is watched back-to-back
                aired = parse_iso(ep["first_aired"])
                at = aired if cursor is None or aired > cursor else cursor
                planned.append((ep, at))
                cursor = at + timedelta(minutes=runtime_of(ep))
        else:
            start = parse_bound(entry["from"], end=False)
            end = parse_bound(entry["to"], end=True)
            slots = spread_schedule([runtime_of(e) for e in chosen], start, end, tz)
            planned = list(zip(chosen, slots, strict=True))

    out: list[Planned] = []
    for ep, at in planned:
        aired = ep.get("first_aired")
        if aired and at < parse_iso(aired):
            at = parse_iso(aired)
            stats["clamped_to_air_date"] += 1
        out.append((ep, at))
    stats["planned"] = len(out)
    return out, stats, warnings


def already_played(at: datetime, plays: Sequence[datetime], *, rewatch: bool) -> bool:
    """True when history already covers this play: any play, or (rewatch) one within 36 hours."""
    if not rewatch:
        return bool(plays)
    return any(abs(p - at) <= REWATCH_WINDOW for p in plays)


def is_unknown(at: datetime) -> bool:
    """Trakt stores 'unknown date' plays at the Unix epoch."""
    return at.year <= 1970


def in_window(at: datetime, window: Window | None) -> bool:
    return window is not None and window[0] <= at <= window[1]


def decide(
    at: datetime,
    existing: Sequence[datetime],
    *,
    rewatch: bool,
    replace: str | None,
    window: Window | None = None,
) -> str:
    """
    What to do with one planned play, given the item's existing plays.

    "add": nothing there yet. "skip": history already covers it. "replace": remove the existing
    plays and add the planned one (`--replace`). "redate": some existing plays fall inside the
    `--replace-window`; those are dropped, the others are kept, and the planned play is added.
    "regroup": a genuine extra play (rewatch); Trakt would re-date the existing play if it were
    simply added, so the existing plays are removed and re-sent together with the new one.
    """
    if not existing:
        return "add"
    if replace == "all" or (replace == "unknown" and all(is_unknown(d) for d in existing)):
        return "replace"
    if any(in_window(d, window) for d in existing):
        return "redate"
    if already_played(at, existing, rewatch=rewatch):
        return "skip"
    return "regroup"


def history_index(history: Sequence[Mapping[str, Any]]) -> dict[int, list[Existing]]:
    """History items (one per play) -> {item trakt id: [(watched_at, history id)]}."""
    out: dict[int, list[Existing]] = {}
    for item in history:
        obj = item.get("episode") or item.get("movie") or {}
        out.setdefault(int(obj["ids"]["trakt"]), []).append(
            (parse_iso(item["watched_at"]), int(item["id"]))
        )
    return out


def pack(groups: Sequence[Sequence[Play]], size: int) -> list[list[Play]]:
    """Batches of at most `size` plays that never split a group (all plays of one item)."""
    batches: list[list[Play]] = [[]]
    for group in groups:
        if batches[-1] and len(batches[-1]) + len(group) > size:
            batches.append([])
        batches[-1].extend(group)
    return [b for b in batches if b]


# ── effects ──────────────────────────────────────────────────────────────────


def resolve(
    client: httpx.Client, headers: Mapping[str, str], entry: Mapping[str, Any], kind: str
) -> dict[str, Any] | None:
    """Resolves a manifest entry to a Trakt show/movie summary (`kind` is 'show' or 'movie')."""
    if entry.get("id"):
        r = client.get(f"{TRAKT_API}/{kind}s/{entry['id']}", headers=headers)
        return r.json() if r.status_code == 200 else None
    params: dict[str, Any] = {"query": str(entry.get("title") or ""), "fields": "title"}
    if entry.get("year"):
        params["years"] = entry["year"]
    r = client.get(f"{TRAKT_API}/search/{kind}", params=params, headers=headers)
    if r.status_code != 200:
        return None
    hits = [x[kind] for x in r.json() if isinstance(x, dict) and kind in x]
    want, year = norm_title(str(entry.get("title") or "")), entry.get("year")

    def rank(hit: Mapping[str, Any]) -> tuple[int, int]:
        same_title = norm_title(str(hit.get("title") or "")) == want
        near_year = year is None or abs(int(hit.get("year") or 0) - int(year)) <= 1
        return (int(same_title), int(near_year))

    return max(hits, key=rank, default=None)  # max keeps Trakt's relevance order on ties


def match_warning(entry: Mapping[str, Any], found: Mapping[str, Any]) -> str | None:
    """Flags a search result whose title or year differs from what the manifest asked for."""
    if entry.get("id"):
        return None
    asked, got = str(entry.get("title") or ""), str(found.get("title") or "")
    year = entry.get("year")
    off_year = year is not None and abs(int(found.get("year") or 0) - int(year)) > 1
    if not titles_close(asked, got) or off_year:
        return f"asked for {asked!r} ({year}), matched {got!r} ({found.get('year')})"
    return None


def fetch_episodes(
    client: httpx.Client, headers: Mapping[str, str], show_trakt_id: int
) -> list[dict[str, Any]]:
    r = client.get(
        f"{TRAKT_API}/shows/{show_trakt_id}/seasons",
        params={"extended": "full,episodes"},
        headers=headers,
    )
    r.raise_for_status()
    return [ep for season in r.json() for ep in season.get("episodes", [])]


def fetch_history(
    client: httpx.Client, headers: Mapping[str, str], kind: str, trakt_id: int
) -> list[dict[str, Any]]:
    """Every history item (one per play) for one show or movie."""
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        r = client.get(
            f"{TRAKT_API}/sync/history/{kind}s/{trakt_id}",
            params={"page": page, "limit": 1000},
            headers=headers,
        )
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if page >= int(r.headers.get("X-Pagination-Page-Count", "1")) or not batch:
            return out
        page += 1


def signed_in_headers(
    client: httpx.Client, env: Mapping[str, str], client_id: str
) -> dict[str, str]:
    """Headers with a user token: reuses the cached one while Trakt accepts it, else device auth."""
    if TOKEN_CACHE.exists():
        headers = trakt_headers(client_id, TOKEN_CACHE.read_text().strip())
        if client.get(f"{TRAKT_API}/users/settings", headers=headers).status_code == 200:
            return headers
    token = trakt_device_auth(client, client_id, env.get("TRAKT_CLIENT_SECRET", ""))
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(token)
    TOKEN_CACHE.chmod(0o600)
    return trakt_headers(client_id, token)


def post_batches(
    client: httpx.Client, headers: Mapping[str, str], path: str, bodies: Sequence[Mapping[str, Any]]
) -> None:
    for body in bodies:
        r = client.post(f"{TRAKT_API}{path}", headers=headers, json=body)
        if r.status_code not in (200, 201):
            sys.exit(f"❌ Trakt rejected a batch: HTTP {r.status_code} {r.text[:400]}")
        res = r.json()
        shown = {k: res[k] for k in ("added", "updated", "deleted") if k in res}
        print("✅ Trakt:", shown, flush=True)
        missing = {k: v for k, v in res.get("not_found", {}).items() if v}
        if missing:
            print("⚠️  Not matched on Trakt:", missing, flush=True)


def remove_plays(
    client: httpx.Client,
    headers: Mapping[str, str],
    history_ids: Sequence[int],
    affected: Sequence[Play],
) -> None:
    """
    Removes plays by history id, then waits until Trakt no longer lists any play for the affected
    items. Adding before the removal has settled would re-date a play instead of creating one.
    """
    ids = list(history_ids)
    post_batches(
        client,
        headers,
        "/sync/history/remove",
        [{"ids": ids[i : i + BATCH]} for i in range(0, len(ids), BATCH)],
    )
    parents = {(p["kind"], int(p["parent"])) for p in affected}
    items = {int(p["item"]) for p in affected}
    for _ in range(SETTLE_POLLS):
        time.sleep(SETTLE_SECONDS)
        left = sum(
            1
            for kind, parent in parents
            for item in history_index(fetch_history(client, headers, kind, parent))
            if item in items
        )
        print(
            f"   waiting for Trakt to drop the removed plays: {left} items still listed", flush=True
        )
        if left == 0:
            time.sleep(SETTLE_SECONDS)
            return
    sys.exit(
        "❌ Removed plays are still listed; nothing was re-added. "
        "Restore them with --undo <log> --apply."
    )


def send_plays(client: httpx.Client, headers: Mapping[str, str], plays: Sequence[Play]) -> None:
    """Adds plays, keeping every play of one item inside a single request."""
    groups: dict[tuple[str, int], list[Play]] = {}
    for play in plays:
        groups.setdefault((play["kind"], int(play["item"])), []).append(play)
    bodies = []
    for batch in pack(list(groups.values()), BATCH):
        body: dict[str, list[dict[str, Any]]] = {}
        for play in batch:
            key = "movies" if play["kind"] == "movie" else "episodes"
            body.setdefault(key, []).append(
                {"ids": {"trakt": int(play["item"])}, "watched_at": play["watched_at"]}
            )
        bodies.append(body)
    post_batches(client, headers, "/sync/history", bodies)


def undo(client: httpx.Client, headers: Mapping[str, str], log_path: Path, *, apply: bool) -> None:
    """
    Reverts one --apply run: removes exactly the plays it added (found again by item id +
    `watched_at`, removed by history id) and puts back any plays it had removed.
    """
    log = json.loads(log_path.read_text())
    plays: list[Play] = log["plays"]
    removed: list[Play] = log.get("removed", [])
    by_parent: dict[tuple[str, int], list[Play]] = {}
    for play in plays:
        by_parent.setdefault((play["kind"], int(play["parent"])), []).append(play)
    history_ids: list[int] = []
    for (kind, parent), wanted in by_parent.items():
        index = history_index(fetch_history(client, headers, kind, parent))
        for play in wanted:
            at = parse_iso(play["watched_at"]).replace(microsecond=0)
            entries = index.get(int(play["item"]), [])
            match = next((e for e in entries if e[0].replace(microsecond=0) == at), None)
            if match is not None:
                entries.remove(match)
                history_ids.append(match[1])
    print(
        f"↩️  {log_path.name}: {len(plays)} plays added by that run, {len(history_ids)} still in "
        f"history; {len(removed)} plays it removed would be put back"
    )
    if not apply:
        print("--- DRY RUN: nothing changed. Add --apply to revert.")
        return
    if history_ids:
        remove_plays(client, headers, history_ids, plays)
    if removed:
        send_plays(client, headers, removed)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("manifest", type=Path, nargs="?", help="JSON list of shows/movies with dates")
    ap.add_argument("--apply", action="store_true", help="Write to Trakt (default: dry run)")
    ap.add_argument("--match", help="Only entries whose title/id contains this text")
    ap.add_argument("--title", help="Only entries whose title is exactly this (case-insensitive)")
    ap.add_argument(
        "--allow-repeat",
        action="store_true",
        help="Treat every entry as a rewatch: add its plays next to the ones already in history",
    )
    ap.add_argument(
        "--replace",
        choices=("unknown", "all"),
        help="Replace existing plays of the planned items with the planned dates: 'unknown' only "
        "where every existing play has no date (bulk 'unknown date' marks), 'all' always",
    )
    ap.add_argument(
        "--replace-window",
        metavar="FROM..TO",
        help="Replace only existing plays dated inside this range (e.g. 2018-09..2018-11) with the "
        "planned dates; the items' other plays are kept",
    )
    ap.add_argument("--undo", type=Path, help="Undo log to revert (with --apply; else a dry run)")
    args = ap.parse_args()

    env = load_env_file(TENANT_DIR / ".env")
    client_id = env.get("TRAKT_CLIENT_ID", "")
    if not client_id:
        sys.exit("❌ TRAKT_CLIENT_ID missing in the tenant .env (see this script's docstring)")

    if args.undo:
        with httpx.Client(timeout=60) as client:
            undo(client, signed_in_headers(client, env, client_id), args.undo, apply=args.apply)
        return
    if args.manifest is None:
        ap.error("a manifest is required unless --undo is given")

    manifest = json.loads(args.manifest.read_text())
    if args.match:
        needle = args.match.lower()
        manifest = [e for e in manifest if needle in str(e.get("title") or e.get("id")).lower()]
    if args.title:
        manifest = [e for e in manifest if str(e.get("title") or "").lower() == args.title.lower()]

    tz = datetime.now().astimezone().tzinfo or UTC
    now = datetime.now(tz=UTC)
    to_add: list[Play] = []  # new plays, plus existing plays that must be re-sent alongside them
    to_remove: list[Play] = []  # existing plays to remove first (each carries its history id)
    after: dict[tuple[str, int], list[datetime]] = {}  # plays a reworked item will end up with
    cache: dict[int, list[dict[str, Any]]] = {}
    window: Window | None = None
    if args.replace_window:
        lo, _, hi = args.replace_window.partition("..")
        first, last = parse_bound(lo, end=False), parse_bound(hi or lo, end=True)
        window = (
            datetime(first.year, first.month, first.day, tzinfo=tz),
            datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=tz),
        )

    def consider(
        kind: str,
        parent: int,
        item: int,
        at: datetime,
        name: str,
        existing: list[Existing],
        *,
        rewatch: bool,
    ) -> str:
        """Queues one planned play; returns what was decided for it."""
        key = (kind, item)
        new: Play = {
            "kind": kind,
            "parent": parent,
            "item": item,
            "watched_at": iso_utc(at),
            "label": name,
        }
        if key in after:  # an earlier entry already reworked this item: only avoid a double play
            if already_played(at, after[key], rewatch=True):
                return "skip"
            after[key].append(at)
            to_add.append(new)
            return "add"
        action = decide(
            at, [d for d, _ in existing], rewatch=rewatch, replace=args.replace, window=window
        )
        if action == "skip":
            return action
        kept: list[datetime] = []
        if action != "add":
            for old_at, history_id in existing:
                old: Play = {**new, "watched_at": iso_utc(old_at), "history_id": history_id}
                to_remove.append(old)
                stale = action == "replace" or (action == "redate" and in_window(old_at, window))
                if not stale:  # keep the old play: it goes back in the same request
                    to_add.append({**old, "resent": True})
                    kept.append(old_at)
        after[key] = kept
        if not already_played(at, kept, rewatch=True):
            after[key].append(at)
            to_add.append(new)
        return action

    with httpx.Client(timeout=60) as client:
        headers = trakt_headers(client_id)
        signed_in = args.apply or TOKEN_CACHE.exists()  # a dry run never starts a sign-in
        if signed_in:
            headers = signed_in_headers(client, env, client_id)
        else:
            print("ℹ️  Dry run: not signed in, so existing Trakt history is not looked at yet.\n")

        for entry in manifest:
            kind = "movie" if str(entry.get("type") or "show").lower() == "movie" else "show"
            rewatch = bool(entry.get("rewatch")) or args.allow_repeat
            found = resolve(client, headers, entry, kind)
            if found is None:
                print(f"⚠️  Not found on Trakt, skipped: {entry.get('title') or entry.get('id')}")
                continue
            tid = int(found["ids"]["trakt"])
            name = f"{found.get('title')} ({found.get('year')})"
            if (bad := match_warning(entry, found)) is not None:
                print(f"❗ CHECK MATCH: {bad}")
            index = history_index(fetch_history(client, headers, kind, tid)) if signed_in else {}

            if kind == "movie":
                day = parse_bound(entry["on"], end=False)
                at = datetime(day.year, day.month, day.day, DINNER_HOUR, tzinfo=tz)
                action = consider("movie", tid, tid, at, name, index.get(tid, []), rewatch=rewatch)
                note = {"skip": "already in history, skipped", "add": str(day)}.get(
                    action, f"{day} ({action})"
                )
                print(f"🎬 {name}: {note}")
                continue

            if tid not in cache:
                cache[tid] = fetch_episodes(client, headers, tid)
            planned, stats, warnings = plan_show(entry, cache[tid], tz, now=now)
            fresh: list[Planned] = []
            actions: Counter[str] = Counter()
            for ep, at in planned:
                item = int(ep["ids"]["trakt"])
                action = consider(
                    "show",
                    tid,
                    item,
                    at,
                    f"{name} {label(ep)}",
                    index.get(item, []),
                    rewatch=rewatch,
                )
                actions[action] += 1
                if action != "skip":
                    fresh.append((ep, at))
            stats["already_in_history"] = actions["skip"]
            stats["replacing"] = actions["replace"] + actions["redate"]
            stats["added_next_to_existing"] = actions["regroup"]
            mode = "exact" if "exact" in entry else entry.get("when") or "range"
            print(f"📺 {name}  [trakt.tv/shows/{found['ids'].get('slug')}]  {mode}")
            print(f"   {stats}")
            if fresh:
                per_season = Counter(int(ep["season"]) for ep, _ in fresh)
                per_day = Counter(iso_utc(at)[:10] for _, at in fresh)
                print(
                    f"   {label(fresh[0][0])} @ {iso_utc(fresh[0][1])} → "
                    f"{label(fresh[-1][0])} @ {iso_utc(fresh[-1][1])}; "
                    f"seasons {dict(sorted(per_season.items()))}; max {max(per_day.values())}/day"
                )
            for w in warnings[:5]:
                print(f"   ⚠️  {w}")
            if len(warnings) > 5:
                print(f"   ⚠️  … and {len(warnings) - 5} more")

        new = [p for p in to_add if not p.get("resent")]
        print(
            f"\n📊 Total: {sum(p['kind'] == 'show' for p in new)} episodes, "
            f"{sum(p['kind'] == 'movie' for p in new)} movies to add; "
            f"{len(to_remove)} existing plays to remove first "
            f"({len(to_add) - len(new)} of them re-sent with their original dates)"
        )
        if not args.apply:
            print("--- DRY RUN: nothing sent. Check the matched titles above, then use --apply.")
            return
        if not to_add:
            print("✅ Nothing new to send.")
            return

        # The undo log is written before anything is sent, so a failed run can still be reverted.
        log_path = args.manifest.parent / "trakt_undo" / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"created": iso_utc(now), "plays": to_add, "removed": to_remove}, indent=1)
        )
        print(f"📝 Undo log: {log_path}  (revert with --undo {log_path} --apply)")

        if to_remove:
            remove_plays(client, headers, [int(p["history_id"]) for p in to_remove], to_remove)
        send_plays(client, headers, to_add)


if __name__ == "__main__":
    main()
