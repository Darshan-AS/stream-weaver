#!/usr/bin/env python3
"""
Scrapes Peterson Academy public course pages for rich metadata (posters, backdrops,
instructor, synopsis, lecture titles, lecture descriptions, and thumbnails) and
builds a static Stremio/Nuvio addon in addon/ linked to your TorBox torrent.
"""

import html
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
ADDON_DIR = ROOT_DIR / "addon"


def infer_peterson_slug(title: str, explicit_slug: Optional[str] = None) -> Optional[str]:
    """Extracts a Peterson Academy course slug from a torrent title like
    'Peterson Academy - Napoleon - Andrew Roberts EDU4U' -> 'napoleon'.
    """
    if explicit_slug:
        return explicit_slug.strip().lower().strip("/")

    if "peterson" not in title.lower():
        return None

    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", title) if p.strip()]
    course_part = None
    for idx, p in enumerate(parts):
        if "peterson" in p.lower() and idx + 1 < len(parts):
            course_part = parts[idx + 1]
            break
    if not course_part and len(parts) >= 2:
        course_part = parts[1]
    if not course_part:
        return None

    # Strip trailing release tags if no separator was used
    course_part = re.sub(r"\b(EDU4U|1080p|720p|WEBRip|WEB-DL)\b.*$", "", course_part, flags=re.IGNORECASE).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", course_part.lower().replace("&", "and")).strip("-")
    return slug or None


def fetch_peterson_course_html(slug: str) -> Optional[str]:
    url = f"https://petersonacademy.com/courses/{slug}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 200:
                return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"   ⚠️  [Metadata] Could not fetch {url}: {exc}")
    return None


def scrape_peterson_course_html(html_text: str) -> Optional[Dict[str, Any]]:
    """Extracts course and lecture metadata from Peterson Academy SSR HTML."""
    m = re.search(
        r'previewCourse:\{id:(\d+),title:"([^"]+)",description:"((?:\\.|[^"])*)",slug:"([^"]+)",label:[^,]+,courseCode:"([^"]+)"',
        html_text,
    )
    if not m:
        return None

    course_id, title, description, slug, course_code = m.groups()
    description = description.encode("utf-8").decode("unicode_escape", errors="replace")

    inst_match = re.search(
        r'instructors:\[\{id:\d+,fullName:"([^"]+)",slug:"([^"]+)",title:"([^"]+)"',
        html_text[m.start() : m.start() + 4000],
    )
    instructor = inst_match.group(1) if inst_match else "Peterson Academy"

    trailer_match = re.search(
        r'trailerPlaybackId:"([^"]+)"',
        html_text[m.start() : m.start() + 2000],
    )
    trailer_mux_id = trailer_match.group(1) if trailer_mux_id_present(trailer_match) else None

    lec_pattern = re.compile(
        r'\{id:"[^"]+",lectureId:(\d+),lectureNumber:(\d+),path:"([^"]+)",title:"((?:\\.|[^"])*)",description:"((?:\\.|[^"])*)",durationSeconds:(\d+),muxAssetId:"([^"]+)"'
    )
    lectures_by_num: Dict[int, Dict[str, Any]] = {}
    for lm in lec_pattern.finditer(html_text):
        lec_id, lec_num_str, path, lec_title, lec_desc, dur_str, mux_id = lm.groups()
        lec_num = int(lec_num_str)
        if lec_num in lectures_by_num:
            continue
        lectures_by_num[lec_num] = {
            "lectureId": int(lec_id),
            "lectureNumber": lec_num,
            "path": path,
            "title": html.unescape(lec_title),
            "description": html.unescape(lec_desc),
            "durationSeconds": int(dur_str),
            "muxAssetId": mux_id,
        }

    poster_url = f"https://ik.imagekit.io/0qkyxdfkk/prod/courses%2F{course_id}%2Fthumb?tr=h-1200&alt=media"
    hero_url = f"https://ik.imagekit.io/0qkyxdfkk/prod/courses%2F{course_id}%2Fhero?tr=w-1920&alt=media"
    thumb_landscape = (
        f"https://image.mux.com/{trailer_mux_id}/thumbnail.jpg?width=640"
        if trailer_mux_id
        else f"https://ik.imagekit.io/0qkyxdfkk/prod/courses%2F{course_id}%2Fhero?tr=w-640&alt=media"
    )

    return {
        "courseId": int(course_id),
        "slug": slug,
        "title": title,
        "courseCode": course_code,
        "instructor": instructor,
        "description": description,
        "poster": poster_url,
        "background": hero_url,
        "episodeThumbnail": thumb_landscape,
        "lectures": [lectures_by_num[k] for k in sorted(lectures_by_num)],
    }


def trailer_mux_id_present(match: Optional[re.Match]) -> bool:
    return bool(match and match.group(1))


def map_torbox_files_to_episodes(files: List[Dict[str, Any]]) -> Dict[int, Tuple[int, str]]:
    """Maps 1-based episode number -> (torbox_file_idx, short_filename) from TorBox's files array."""
    video_exts = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v")
    ep_map: Dict[int, Tuple[int, str]] = {}
    video_entries: List[Tuple[int, str]] = []

    for idx, f in enumerate(files):
        raw_name = str(f.get("short_name") or f.get("name") or "")
        short_name = raw_name.split("/")[-1]
        if not short_name.lower().endswith(video_exts):
            continue
        file_idx = int(f.get("id")) if f.get("id") is not None and int(f.get("id")) < 500 else idx
        video_entries.append((file_idx, short_name))
        m = re.match(r"^\s*0*(\d+)[\.\s_-]", short_name)
        if m:
            ep_map[int(m.group(1))] = (file_idx, short_name)

    if not ep_map:
        for ep_num, (file_idx, short_name) in enumerate(sorted(video_entries, key=lambda x: x[1]), 1):
            ep_map[ep_num] = (file_idx, short_name)

    return ep_map


def build_static_addon_for_course(
    course_meta: Dict[str, Any],
    info_hash: str,
    torbox_torrent_id: int,
    file_index_by_episode: Dict[int, Tuple[int, str]],
) -> None:
    """Writes static Stremio addon JSON files (manifest, catalog, meta, stream) into addon/."""
    slug = course_meta["slug"]
    stremio_id = f"pa_{course_meta['courseId']}"
    torbox_lib_id = f"aiostreams::library.torbox.torrent.{torbox_torrent_id}"

    manifest = {
        "id": "com.darshan.petersonacademy",
        "version": "1.0.0",
        "name": "Peterson Academy Metadata",
        "description": "Rich posters, backdrops, lecture thumbnails, and descriptions for Peterson Academy courses on TorBox",
        "logo": "https://petersonacademy.com/favicon-apple-touch-v3.png",
        "resources": ["catalog", "meta", "stream"],
        "types": ["series", "library"],
        "idPrefixes": ["pa_", "aiostreams::library.torbox.torrent."],
        "catalogs": [
            {
                "type": "series",
                "id": "peterson_academy",
                "name": "Peterson Academy",
            }
        ],
    }

    videos_series: List[Dict[str, Any]] = []
    videos_library: List[Dict[str, Any]] = []

    for lec in course_meta["lectures"]:
        ep_num = lec["lectureNumber"]
        file_idx, filename = file_index_by_episode.get(ep_num, (ep_num - 1, f"{ep_num}. {lec['title']}.mp4"))
        dur_mins = round(lec["durationSeconds"] / 60)

        ep_thumb = course_meta["episodeThumbnail"]
        ep_overview = f"⏱️ {dur_mins} min · {course_meta['instructor']}\n\n{lec['description']}"

        vid_series_id = f"{stremio_id}_s1e{ep_num}"
        vid_playback_id = f"{torbox_lib_id}:{file_idx}" if torbox_torrent_id else vid_series_id
        videos_series.append(
            {
                "id": vid_playback_id,
                "title": f"{ep_num}. {lec['title']}",
                "season": 1,
                "episode": ep_num,
                "thumbnail": ep_thumb,
                "overview": ep_overview,
                "description": ep_overview,
            }
        )

        videos_library.append(
            {
                "id": f"{torbox_lib_id}:{file_idx}",
                "title": f"{ep_num}. {lec['title']}",
                "season": 1,
                "episode": ep_num,
                "thumbnail": ep_thumb,
                "overview": ep_overview,
                "description": ep_overview,
            }
        )

        stream_path = ADDON_DIR / "stream" / "series" / f"{vid_series_id}.json"
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        stream_payload = {
            "streams": [
                {
                    "name": "Peterson Academy",
                    "title": filename,
                    "infoHash": info_hash.lower(),
                    "fileIdx": file_idx,
                    "behaviorHints": {
                        "filename": filename,
                        "bingeGroup": f"pa-{slug}",
                    },
                }
            ]
        }
        stream_path.write_text(json.dumps(stream_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    catalog_card = {
        "id": stremio_id,
        "type": "series",
        "name": f"{course_meta['title']} — {course_meta['instructor']}",
        "poster": course_meta["poster"],
        "background": course_meta["background"],
        "description": f"[{course_meta['courseCode']}] {course_meta['description']}",
        "releaseInfo": course_meta["courseCode"],
        "genres": ["History", "Humanities", "Education"],
        "cast": [course_meta["instructor"]],
    }

    catalog_path = ADDON_DIR / "catalog" / "series" / "peterson_academy.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    existing_metas: List[Dict[str, Any]] = []
    if catalog_path.exists():
        try:
            existing_metas = json.loads(catalog_path.read_text(encoding="utf-8")).get("metas", [])
        except Exception:
            existing_metas = []
    existing_metas = [m for m in existing_metas if m.get("id") != stremio_id]
    existing_metas.insert(0, catalog_card)
    catalog_path.write_text(
        json.dumps({"metas": existing_metas}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    meta_series_path = ADDON_DIR / "meta" / "series" / f"{stremio_id}.json"
    meta_series_path.parent.mkdir(parents=True, exist_ok=True)
    meta_series_payload = {
        "meta": {
            **catalog_card,
            "videos": videos_series,
        }
    }
    meta_series_path.write_text(
        json.dumps(meta_series_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    meta_lib_path = ADDON_DIR / "meta" / "library" / f"{torbox_lib_id}.json"
    meta_lib_path.parent.mkdir(parents=True, exist_ok=True)
    meta_lib_payload = {
        "meta": {
            **catalog_card,
            "id": torbox_lib_id,
            "type": "library",
            "videos": videos_library,
        }
    }
    meta_lib_path.write_text(
        json.dumps(meta_lib_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    ADDON_DIR.mkdir(parents=True, exist_ok=True)
    (ADDON_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    import sys

    html_file = Path(sys.argv[1])
    html_text = html_file.read_text(encoding="utf-8")
    meta = scrape_peterson_course_html(html_text)
    if not meta:
        print("Failed to parse course metadata", file=sys.stderr)
        sys.exit(1)

    napoleon_file_map = {
        1: (4, "1. Rise of Napoleon.mp4"),
        2: (1, "2. Italian and Egyptian Campaigns.mp4"),
        3: (8, "3. Transforming France.mp4"),
        4: (2, "4. Work Habits and Achievements.mp4"),
        5: (6, "5. Austrian and Spanish Campaigns.mp4"),
        6: (5, "6. Napoleon's Invasion of Russia.mp4"),
        7: (3, "7. Hundred Days and Waterloo.mp4"),
        8: (7, "8. Final Years and Legacy.mp4"),
    }
    build_static_addon_for_course(
        meta,
        info_hash="47b0231e499481af9d2e462d961a9a4d7feb0d64",
        torbox_torrent_id=120832854,
        file_index_by_episode=napoleon_file_map,
    )
    print(f"✅ Built rich metadata addon in {ADDON_DIR} for: {meta['title']} ({len(meta['lectures'])} lectures)")
