#!/usr/bin/env python3
"""
Add magnet links or .torrent files directly to your TorBox Cloud Library
and force-refresh AIOStreams so they immediately show up in Nuvio's
Home screen ("TorBox Cloud Library") and Search Bar.

Usage:
  ./scripts/add_magnet.sh "magnet:?xt=urn:btih:..."
  ./scripts/add_magnet.sh /path/to/course.torrent
  ./scripts/add_magnet.sh --list
"""

import argparse
import base64
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aio import CONFIGS_DIR, ENV_FILE, load_env

TORBOX_API_BASE = "https://api.torbox.app/v1/api"
MAX_ESSENTIAL_BYTES = 200 * (1024**3)  # 200 GiB warning threshold


def format_bytes(size: int) -> str:
    if not size or size <= 0:
        return "Unknown size"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.2f} {unit}" if unit in ("GB", "TB") else f"{size:.0f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def extract_info_hash_from_magnet(magnet: str) -> Tuple[Optional[str], Optional[str]]:
    """Returns (info_hash_hex, display_name) from a magnet URI or raw 40-char/32-char hash."""
    raw = magnet.strip()
    if re.fullmatch(r"[a-fA-F0-9]{40}", raw):
        return raw.lower(), None

    if not raw.lower().startswith("magnet:?"):
        return None, None

    parsed = urllib.parse.urlparse(raw)
    qs = urllib.parse.parse_qs(parsed.query)
    dn = qs.get("dn", [None])[0]
    if dn:
        dn = urllib.parse.unquote_plus(dn)

    for xt in qs.get("xt", []):
        m = re.search(r"urn:btih:([a-zA-Z0-9]+)", xt, re.IGNORECASE)
        if m:
            h = m.group(1)
            if len(h) == 40:
                return h.lower(), dn
            if len(h) == 32:
                try:
                    decoded = base64.b32decode(h.upper()).hex()
                    return decoded.lower(), dn
                except Exception:
                    pass
    return None, dn


# Minimal bencode decoder/encoder to compute infoHash & file list from .torrent files
def _bdecode(data: bytes, idx: int = 0) -> Tuple[Any, int]:
    ch = chr(data[idx])
    if ch == "i":
        end = data.index(b"e", idx + 1)
        return int(data[idx + 1 : end]), end + 1
    if ch == "l":
        idx += 1
        out = []
        while chr(data[idx]) != "e":
            val, idx = _bdecode(data, idx)
            out.append(val)
        return out, idx + 1
    if ch == "d":
        idx += 1
        out = {}
        while chr(data[idx]) != "e":
            k, idx = _bdecode(data, idx)
            val_start = idx
            v, idx = _bdecode(data, idx)
            out[k] = (v, data[val_start:idx])
        return out, idx + 1
    colon = data.index(b":", idx)
    length = int(data[idx:colon])
    start = colon + 1
    return data[start : start + length], start + length


def parse_torrent_file(path: Path) -> Tuple[str, str, int, List[Tuple[str, int]]]:
    """Returns (info_hash, name, total_bytes, [(file_path, size), ...])."""
    raw = path.read_bytes()
    top, _ = _bdecode(raw, 0)
    info_tuple = top.get(b"info")
    if not info_tuple:
        raise ValueError(f"Invalid .torrent file (missing 'info' dictionary): {path}")
    info_dict, info_raw_bytes = info_tuple
    info_hash = hashlib.sha1(info_raw_bytes).hexdigest().lower()

    name_bytes = info_dict.get(b"name", (path.stem.encode("utf-8"), b""))[0]
    name = name_bytes.decode("utf-8", errors="replace")

    files_out: List[Tuple[str, int]] = []
    total_size = 0
    if b"files" in info_dict:
        for f_entry in info_dict[b"files"][0]:
            f_len = int(f_entry.get(b"length", (0, b""))[0])
            path_parts = [
                p.decode("utf-8", errors="replace")
                for p in f_entry.get(b"path", ([], b""))[0]
            ]
            f_path = "/".join(path_parts)
            files_out.append((f_path, f_len))
            total_size += f_len
    else:
        total_size = int(info_dict.get(b"length", (0, b""))[0])
        files_out.append((name, total_size))

    return info_hash, name, total_size, files_out


def torbox_request(
    method: str,
    endpoint: str,
    api_key: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
) -> Tuple[int, Any]:
    url = f"{TORBOX_API_BASE}{endpoint}"
    req_headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Stream-Weaver-CLI/1.0",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except Exception:
            return err.code, {"detail": raw}


def check_cached_on_torbox(info_hash: str, api_key: str) -> Optional[Dict[str, Any]]:
    status, resp = torbox_request(
        "GET",
        f"/torrents/checkcached?hash={info_hash}&format=object&list_files=true",
        api_key,
    )
    if status == 200 and isinstance(resp, dict):
        data = resp.get("data")
        if isinstance(data, dict):
            return data.get(info_hash.lower()) or data.get(info_hash.upper())
    return None


def add_magnet_to_torbox(
    magnet_uri: str, api_key: str, name: Optional[str] = None, as_queued: bool = False
) -> Tuple[int, Any]:
    boundary = f"----StreamWeaverBoundary{uuid.uuid4().hex}"
    fields = {"magnet": magnet_uri, "allow_zip": "true"}
    if as_queued:
        fields["as_queued"] = "true"
    if name:
        fields["name"] = name

    lines: List[bytes] = []
    for k, v in fields.items():
        lines.append(f"--{boundary}\r\n".encode("utf-8"))
        lines.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("utf-8"))
        lines.append(f"{v}\r\n".encode("utf-8"))
    lines.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(lines)

    status, resp = torbox_request(
        "POST",
        "/torrents/createtorrent",
        api_key,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        body=body,
    )
    if not as_queued and (
        status not in (200, 201) or not (isinstance(resp, dict) and resp.get("success"))
    ):
        return add_magnet_to_torbox(magnet_uri, api_key, name=name, as_queued=True)
    return status, resp


def add_torrent_file_to_torbox(
    torrent_path: Path, api_key: str, as_queued: bool = False
) -> Tuple[int, Any]:
    boundary = f"----StreamWeaverBoundary{uuid.uuid4().hex}"
    file_bytes = torrent_path.read_bytes()

    parts: List[bytes] = [
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="file"; filename="{torrent_path.name}"\r\n'.encode("utf-8"),
        b"Content-Type: application/x-bittorrent\r\n\r\n",
        file_bytes,
        b"\r\n",
    ]
    if as_queued:
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                b'Content-Disposition: form-data; name="as_queued"\r\n\r\ntrue\r\n',
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    status, resp = torbox_request(
        "POST",
        "/torrents/createtorrent",
        api_key,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        body=body,
    )
    if not as_queued and (
        status not in (200, 201) or not (isinstance(resp, dict) and resp.get("success"))
    ):
        return add_torrent_file_to_torbox(torrent_path, api_key, as_queued=True)
    return status, resp


def extract_magnets_from_text_file(path: Path) -> List[str]:
    """Extracts all magnet:?xt=urn:btih:... URIs from a Markdown (.md) or text file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    magnets = re.findall(r"magnet:\?xt=urn:btih:[^\s\)\"'>\]|]+", text, re.IGNORECASE)
    seen = set()
    ordered: List[str] = []
    for m in magnets:
        ih, _ = extract_info_hash_from_magnet(m)
        key = ih or m
        if key not in seen:
            seen.add(key)
            ordered.append(m)
    return ordered


def refresh_aiostreams_library_cache() -> None:
    """Calls the AIOStreams library refresh action so the new torrent appears in Nuvio immediately."""
    aiomanager_path = CONFIGS_DIR / "aiomanager.json"
    if not aiomanager_path.exists():
        return
    try:
        mgr = json.loads(aiomanager_path.read_text(encoding="utf-8"))
        install_url = None
        for sa in mgr.get("addons", {}).get("savedAddons", []):
            if sa.get("name") == "AIOStreams" or str(sa.get("manifest", {}).get("id", "")).startswith("aiostreams"):
                install_url = str(sa.get("installUrl", "")).rstrip("/")
                break
        if not install_url:
            return
        if install_url.endswith("/manifest.json"):
            install_url = install_url[: -len("/manifest.json")]

        refresh_url = f"{install_url}/stream/movie/aiostreams::library.torbox.action.refresh.json"
        req = urllib.request.Request(
            refresh_url,
            headers={"User-Agent": "Stream-Weaver-CLI/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print("🔄 [AIOStreams] Refreshed TorBox Cloud Library cache — ready in Nuvio!")
    except Exception as exc:
        print(f"ℹ️  [AIOStreams] Library cache will auto-refresh shortly ({exc})")


def list_torbox_library(api_key: str) -> int:
    st_q, resp_q = torbox_request("GET", "/queued/getqueued?bypass_cache=true&limit=1000", api_key)
    q_items = (resp_q.get("data") or []) if (st_q == 200 and isinstance(resp_q, dict)) else []
    if q_items:
        print(f"⏳ TorBox Cloud Queue ({len(q_items)} items — Web UI only displays 12 at a time):\n")
        for idx, qitem in enumerate(q_items, 1):
            qname = qitem.get("name") or qitem.get("hash") or "Untitled"
            qid = qitem.get("id")
            print(f"  Q{idx:02d}. 🕒 Queued (ID: {qid}) | {qname}")
        print()

    status, resp = torbox_request("GET", "/torrents/mylist?bypass_cache=true&limit=1000", api_key)
    if status != 200 or not isinstance(resp, dict):
        print(f"❌ Failed to fetch TorBox library (HTTP {status}): {resp}", file=sys.stderr)
        return 1

    items = resp.get("data") or []
    if not items and not q_items:
        print("📭 Your TorBox Cloud Library is currently empty.")
        return 0

    print(f"📚 TorBox Cloud Library ({len(items)} items — all searchable in Nuvio):\n")
    for idx, item in enumerate(items, 1):
        name = item.get("name", "Untitled")
        size = format_bytes(int(item.get("size") or 0))
        state = item.get("download_state", "unknown")
        progress = float(item.get("progress") or 0.0) * 100
        files = item.get("files") or []
        video_count = sum(
            1
            for f in files
            if str(f.get("name", "")).lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"))
        )
        badge = "✅ Ready" if item.get("download_finished") else f"⏳ {state} ({progress:.1f}%)"
        print(f"  {idx:2d}. {badge} | {size:>10} | {video_count:2d} videos | {name}")
    return 0


def fetch_torbox_torrent_files(torrent_id: int, api_key: str) -> List[Dict[str, Any]]:
    if not torrent_id or torrent_id <= 0:
        return []
    status, resp = torbox_request(
        "GET", f"/torrents/mylist?id={torrent_id}&bypass_cache=true", api_key
    )
    if status == 200 and isinstance(resp, dict):
        data = resp.get("data")
        if isinstance(data, dict) and int(data.get("id") or 0) == torrent_id:
            return data.get("files") or []
        if isinstance(data, list):
            for item in data:
                if int(item.get("id") or 0) == torrent_id:
                    return item.get("files") or []
    return []


def maybe_build_course_metadata(
    display_title: str,
    info_hash: str,
    torrent_id: int,
    api_key: str,
    explicit_url: Optional[str] = None,
    explicit_slug: Optional[str] = None,
    force_course: bool = False,
    preloaded_files: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    from build_course_metadata import (
        ADDON_DIR,
        TorBoxFileFallbackProvider,
        build_static_addon_for_course,
        match_torbox_files_to_lectures,
        resolve_course_metadata,
    )

    lower_title = display_title.lower()
    has_known_course_kw = any(
        kw in lower_title for kw in TorBoxFileFallbackProvider.PROVIDER_KEYWORDS
    )
    allow_fallback = bool(force_course or has_known_course_kw)

    tb_files = (
        preloaded_files
        if preloaded_files is not None
        else fetch_torbox_torrent_files(torrent_id, api_key)
    )
    meta = resolve_course_metadata(
        display_title=display_title,
        torbox_files=tb_files,
        torbox_torrent_id=torrent_id,
        explicit_url=explicit_url,
        explicit_slug=explicit_slug,
        allow_fallback=allow_fallback,
    )
    if not meta:
        return False

    print(
        f"   🎨 [Metadata] Matched provider '{meta.provider}' for '{meta.title}' — weaving artwork & synopses..."
    )
    ep_map = match_torbox_files_to_lectures(tb_files, meta.lectures)
    build_static_addon_for_course(meta, info_hash, torrent_id, ep_map)
    status_note = (
        f"{len(ep_map)}/{len(meta.lectures) or len(ep_map)} files linked"
        if ep_map
        else "queued (run --sync-library once download finishes)"
    )
    print(
        f"   ✨ [Metadata] Built '{meta.title}' in {ADDON_DIR.relative_to(ADDON_DIR.parent)}/ ({meta.stremio_id}, {status_note})"
    )
    return True


def sync_library_metadata(api_key: str, force_course: bool = False) -> int:
    """Scans all torrents in TorBox Cloud Library and builds/updates course metadata in addon/."""
    status, resp = torbox_request("GET", "/torrents/mylist?bypass_cache=true&limit=1000", api_key)
    if status != 200 or not isinstance(resp, dict):
        print(f"❌ Failed to fetch TorBox library (HTTP {status}): {resp}", file=sys.stderr)
        return 1

    st_q, resp_q = torbox_request("GET", "/queued/getqueued?bypass_cache=true&limit=1000", api_key)
    q_items = (resp_q.get("data") or []) if (st_q == 200 and isinstance(resp_q, dict)) else []

    items = resp.get("data") or []
    built_count = 0
    active_count = 0
    for item in items:
        t_id = item.get("id")
        name = item.get("name") or ""
        info_hash = item.get("hash") or ""
        files = item.get("files") or []
        if not t_id or not name or not info_hash:
            continue
        if not item.get("download_finished"):
            active_count += 1
            continue
        if maybe_build_course_metadata(
            display_title=name,
            info_hash=info_hash,
            torrent_id=int(t_id),
            api_key=api_key,
            force_course=force_course,
            preloaded_files=files,
        ):
            built_count += 1

    # If fewer than 3 active slots are running and items are waiting in the queue, start them immediately
    while active_count < 3 and q_items:
        next_q = q_items.pop(0)
        qid = next_q.get("id")
        qname = next_q.get("name") or next_q.get("hash")
        if qid:
            body = json.dumps({"queued_id": qid, "operation": "start"}).encode("utf-8")
            st_s, resp_s = torbox_request(
                "POST",
                "/queued/controlqueued",
                api_key,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            if st_s == 200 and isinstance(resp_s, dict) and resp_s.get("success"):
                active_count += 1
                print(f"🚀 [TorBox Queue] Promoted queued download to active slot: {qname} (Queue ID: {qid})")
            else:
                break

    print(
        f"\n✅ Synced metadata for {built_count} completed course(s) in addon/ "
        f"({active_count} actively downloading, {len(q_items)} waiting in TorBox Cloud Queue)."
    )
    return 0


def watch_and_drain_queue(api_key: str, force_course: bool = False, poll_seconds: int = 20) -> int:
    """Continuously keeps 3 active downloads running on TorBox and syncs finished courses until queue is empty."""
    import time

    from build_course_metadata import ADDON_DIR

    synced_ids = set()
    lib_meta_dir = ADDON_DIR / "meta" / "library"
    if lib_meta_dir.exists():
        for p in lib_meta_dir.glob("aiostreams::library.torbox.torrent.*.json"):
            m = re.search(r"\.(\d+)\.json$", p.name)
            if m:
                synced_ids.add(int(m.group(1)))

    print(
        "👀 Watching TorBox Queue — keeping 3 parallel slots active & auto-syncing finished courses...",
        flush=True,
    )
    while True:
        st_l, resp_l = torbox_request("GET", "/torrents/mylist?bypass_cache=true&limit=1000", api_key)
        st_q, resp_q = torbox_request("GET", "/queued/getqueued?bypass_cache=true&limit=1000", api_key)
        items = (resp_l.get("data") or []) if (st_l == 200 and isinstance(resp_l, dict)) else []
        q_items = (resp_q.get("data") or []) if (st_q == 200 and isinstance(resp_q, dict)) else []

        active_items = []
        for item in items:
            t_id = int(item.get("id") or 0)
            name = item.get("name") or ""
            info_hash = item.get("hash") or ""
            files = item.get("files") or []
            if not t_id or not name or not info_hash:
                continue
            if not item.get("download_finished") or not files:
                active_items.append(item)
            elif t_id not in synced_ids:
                if maybe_build_course_metadata(
                    display_title=name,
                    info_hash=info_hash,
                    torrent_id=t_id,
                    api_key=api_key,
                    force_course=force_course,
                    preloaded_files=files,
                ):
                    synced_ids.add(t_id)
                else:
                    synced_ids.add(t_id)
                sys.stdout.flush()

        active_count = len(active_items)
        while active_count < 3 and q_items:
            next_q = q_items.pop(0)
            qid = next_q.get("id")
            qname = next_q.get("name") or next_q.get("hash")
            if qid:
                body = json.dumps({"queued_id": qid, "operation": "start"}).encode("utf-8")
                st_s, resp_s = torbox_request(
                    "POST",
                    "/queued/controlqueued",
                    api_key,
                    headers={"Content-Type": "application/json"},
                    body=body,
                )
                if st_s == 200 and isinstance(resp_s, dict) and resp_s.get("success"):
                    active_count += 1
                    print(
                        f"🚀 [TorBox Queue] Started next download ({active_count}/3 active, {len(q_items)} queued): {qname}",
                        flush=True,
                    )
                else:
                    break

        if active_count == 0 and not q_items:
            refresh_aiostreams_library_cache()
            print(
                "\n🎉 All queued TorBox downloads have finished and synced into addon/!",
                flush=True,
            )
            return 0

        prog_summary = ", ".join(
            f"{str(a.get('name',''))[:28]} ({float(a.get('progress') or 0)*100:.0f}%)"
            for a in active_items
        )
        print(
            f"⏳ Active: {active_count}/3 [{prog_summary or 'starting...'}] | Queued: {len(q_items)}",
            flush=True,
        )
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add course/educational magnet links, markdown/text lists, or .torrent files to TorBox & Nuvio"
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Magnet links, 40-char infoHashes, .md/.txt magnet list files, or .torrent files",
    )
    parser.add_argument(
        "--from-file",
        help="Path to a Markdown (.md) or text file containing magnet links (e.g. tpb-magnets.md)",
    )
    parser.add_argument(
        "--name",
        help="Optional custom title override for the torrent in your TorBox Library",
    )
    parser.add_argument(
        "--url",
        help="Optional course webpage URL (e.g. MasterClass, Wondrium, Peterson Academy, Coursera) to scrape metadata from",
    )
    parser.add_argument(
        "--slug",
        help="Optional course slug override (e.g. 'napoleon')",
    )
    parser.add_argument(
        "--course",
        action="store_true",
        help="Force building structured course metadata from TorBox video filenames even without a webpage URL",
    )
    parser.add_argument(
        "--sync-library",
        action="store_true",
        help="Scan your TorBox Cloud Library and rebuild/update course metadata for all finished downloads",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch TorBox queue continuously, keeping 3 parallel slots active and syncing finished courses until empty",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all torrents currently in your TorBox Cloud Library",
    )
    args = parser.parse_args()

    env = load_env(ENV_FILE)
    api_key = env.get("SECRET_TORBOX_API_KEY", "")
    if not api_key:
        print("❌ Error: SECRET_TORBOX_API_KEY is missing in .env", file=sys.stderr)
        return 1

    if args.list:
        return list_torbox_library(api_key)

    if args.watch:
        return watch_and_drain_queue(api_key, force_course=args.course)

    if args.sync_library:
        return sync_library_metadata(api_key, force_course=args.course)

    expanded_targets: List[str] = []
    if args.from_file:
        fpath = Path(args.from_file).expanduser()
        if not fpath.exists():
            print(f"❌ File not found: {fpath}", file=sys.stderr)
            return 1
        found = extract_magnets_from_text_file(fpath)
        print(f"📄 Extracted {len(found)} magnet links from {fpath.name}")
        expanded_targets.extend(found)

    for t in args.targets:
        if not t.strip().lower().startswith("magnet:?") and len(t) < 255:
            try:
                cand = Path(t).expanduser()
                if cand.exists() and cand.is_file() and cand.suffix.lower() in (".md", ".txt", ".csv"):
                    found = extract_magnets_from_text_file(cand)
                    print(f"📄 Extracted {len(found)} magnet links from {cand.name}")
                    expanded_targets.extend(found)
                    continue
            except OSError:
                pass
        expanded_targets.append(t)

    if not expanded_targets:
        parser.print_help()
        return 1

    added_any = False
    cached_count = 0
    queued_count = 0
    failed_count = 0

    # Pre-fetch existing TorBox library and queue once to avoid redundant POSTs / 429 rate limits
    library_by_hash: Dict[str, Dict[str, Any]] = {}
    queued_by_hash: Dict[str, Dict[str, Any]] = {}
    active_downloads = 0

    st_lib, resp_lib = torbox_request("GET", "/torrents/mylist?bypass_cache=true", api_key)
    if st_lib == 200 and isinstance(resp_lib, dict):
        for item in resp_lib.get("data") or []:
            h = str(item.get("hash") or "").lower()
            if h:
                library_by_hash[h] = item
            if not item.get("download_finished"):
                active_downloads += 1

    st_q, resp_q = torbox_request("GET", "/queued/getqueued", api_key)
    if st_q == 200 and isinstance(resp_q, dict):
        for qitem in resp_q.get("data") or []:
            h = str(qitem.get("hash") or "").lower()
            if h:
                queued_by_hash[h] = qitem

    for idx, target in enumerate(expanded_targets, 1):
        is_file_target = False
        t_path = None
        if not target.strip().lower().startswith("magnet:?") and len(target) < 255:
            try:
                candidate = Path(target).expanduser()
                if candidate.exists() and candidate.is_file():
                    is_file_target = True
                    t_path = candidate
            except OSError:
                is_file_target = False

        prefix_label = f"[{idx}/{len(expanded_targets)}] " if len(expanded_targets) > 1 else ""

        if is_file_target and t_path is not None:
            print(f"\n📦 {prefix_label}Inspecting .torrent file: {t_path.name}")
            info_hash, t_name, total_size, files = parse_torrent_file(t_path)
            display_title = args.name or t_name
            video_files = [
                (p, s)
                for p, s in files
                if p.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"))
            ]
            print(f"   • Name:       {t_name}")
            print(f"   • InfoHash:   {info_hash}")
            print(f"   • Total Size: {format_bytes(total_size)} ({len(video_files)} video files)")
            if total_size > MAX_ESSENTIAL_BYTES:
                print(
                    f"   ⚠️  Warning: Torrent size ({format_bytes(total_size)}) exceeds the 200 GB Essential plan limit!"
                )
            magnet_uri = f"magnet:?xt=urn:btih:{info_hash}"
        else:
            info_hash, dn = extract_info_hash_from_magnet(target)
            if not info_hash:
                print(f"❌ Could not parse magnet link or find file: {target}", file=sys.stderr)
                failed_count += 1
                continue
            magnet_uri = (
                target.strip()
                if target.strip().lower().startswith("magnet:?")
                else f"magnet:?xt=urn:btih:{info_hash}"
            )
            display_title = args.name or dn or info_hash
            print(f"\n🧲 {prefix_label}Inspecting magnet: {display_title}")
            print(f"   • InfoHash:   {info_hash}")

        ih_lower = info_hash.lower()
        if ih_lower in library_by_hash:
            lib_item = library_by_hash[ih_lower]
            t_id = int(lib_item.get("id") or 0)
            is_done = bool(lib_item.get("download_finished"))
            lib_files = (lib_item.get("files") or []) if is_done else []
            if is_done:
                cached_count += 1
                print(f"   • Status:     ⚡ Already in TorBox Library & Ready (ID: {t_id})")
            else:
                queued_count += 1
                prog = float(lib_item.get("progress") or 0.0) * 100
                print(
                    f"   • Status:     ⏳ Actively Downloading on TorBox ({prog:.1f}%, ID: {t_id})"
                )
            maybe_build_course_metadata(
                display_title,
                info_hash,
                t_id if is_done else 0,
                api_key,
                explicit_url=args.url,
                explicit_slug=args.slug,
                force_course=args.course,
                preloaded_files=lib_files,
            )
            added_any = True
            continue

        if ih_lower in queued_by_hash:
            q_item = queued_by_hash[ih_lower]
            q_id = q_item.get("id")
            queued_count += 1
            print(f"   • Status:     ⏳ Already Queued on TorBox (Queue ID: {q_id})")
            maybe_build_course_metadata(
                display_title,
                info_hash,
                0,
                api_key,
                explicit_url=args.url,
                explicit_slug=args.slug,
                force_course=args.course,
                preloaded_files=[],
            )
            added_any = True
            continue

        cached_info = check_cached_on_torbox(info_hash, api_key)
        if cached_info:
            c_size = int(cached_info.get("size") or 0)
            c_files = cached_info.get("files") or []
            print(
                f"   • Status:     ⚡ INSTANTLY CACHED on TorBox ({format_bytes(c_size)}, {len(c_files)} files)"
            )
            if c_size > MAX_ESSENTIAL_BYTES:
                print(
                    f"   ⚠️  Warning: Torrent size ({format_bytes(c_size)}) exceeds the 200 GB Essential plan limit!"
                )
            should_queue = False
        else:
            print("   • Status:     ⏳ Uncached (TorBox will queue & download it in the cloud)")
            should_queue = active_downloads >= 3

        if is_file_target and t_path is not None:
            status, resp = add_torrent_file_to_torbox(t_path, api_key, as_queued=should_queue)
        else:
            status, resp = add_magnet_to_torbox(
                magnet_uri, api_key, name=args.name or dn, as_queued=should_queue
            )

        if status in (200, 201) and isinstance(resp, dict) and resp.get("success"):
            detail = resp.get("detail") or "Added to TorBox Cloud Library"
            data_obj = resp.get("data") or {}
            t_id = data_obj.get("torrent_id") or data_obj.get("queued_id")
            if cached_info or "Cached" in str(detail):
                cached_count += 1
            else:
                queued_count += 1
                if not should_queue and "torrent_id" in data_obj:
                    active_downloads += 1
            print(f"   ✅ [TorBox] {detail}" + (f" (ID: {t_id})" if t_id else ""))
            if info_hash:
                real_tid = int(t_id) if (t_id and "torrent_id" in data_obj and cached_info) else 0
                maybe_build_course_metadata(
                    display_title,
                    info_hash,
                    real_tid,
                    api_key,
                    explicit_url=args.url,
                    explicit_slug=args.slug,
                    force_course=args.course,
                    preloaded_files=None if real_tid else [],
                )
            added_any = True
        else:
            failed_count += 1
            detail = resp.get("detail") if isinstance(resp, dict) else str(resp)
            print(f"   ❌ [TorBox] Failed (HTTP {status}): {detail}", file=sys.stderr)

    if len(expanded_targets) > 1:
        print(
            f"\n📊 Batch Summary: {cached_count} instantly cached, {queued_count} queued on TorBox, {failed_count} failed."
        )

    if added_any:
        refresh_aiostreams_library_cache()
        print("\n🎬 Done! Open Nuvio -> 'Courses & Lectures' (or 'TorBox Cloud Library') on Home to watch.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

