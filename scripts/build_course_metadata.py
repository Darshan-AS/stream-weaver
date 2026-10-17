#!/usr/bin/env python3
"""
Stream Weaver — Extensible Course Metadata & Static Stremio Addon Builder.

Architecture:
  1. Pluggable Providers (`CourseProvider` subclasses):
     - `PetersonAcademyProvider`: Auto-detects "Peterson Academy" titles or
       petersonacademy.com URLs/slugs and scrapes SSR hydration data (posters,
       1080p hero backdrops, Mux lecture thumbnails, durations, and synopses).
     - `GenericWebCourseProvider`: Scrapes any course webpage (--url) using
       Schema.org JSON-LD (`Course`, `VideoObject`, `ItemList`) + OpenGraph tags.
     - `TorBoxFileFallbackProvider`: Zero-config fallback that builds clean
       structured course & episode metadata directly from TorBox video filenames.
  2. Smart Episode Matcher (`match_torbox_files_to_lectures`):
     - Matches TorBox video files to lectures via `01.`, `S01E01`, `Lecture 1`,
       `Part 1`, or fuzzy title similarity.
  3. Static Stremio Addon Writer (`build_static_addon_for_course`):
     - Writes `addon/manifest.json`, `addon/catalog/series/courses.json` (plus
       static `genre=<Genre>.json` slices), `addon/meta/series/sw_*.json`, and
       `addon/stream/series/sw_*.json`.
"""

import argparse
import dataclasses
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aio import CONFIGS_DIR, ENV_FILE, ROOT_DIR, load_env

ADDON_DIR = ROOT_DIR / "addon"
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v")


# ============================================================================
# 1. Normalized Data Models
# ============================================================================


@dataclasses.dataclass
class LectureMetadata:
    number: int
    title: str
    description: str = ""
    duration_seconds: int = 0
    thumbnail: str = ""
    season: int = 1


@dataclasses.dataclass
class CourseMetadata:
    stremio_id: str  # e.g. "sw_pa_3215", "sw_mc_negotiation", "sw_tb_120832854"
    provider: str  # e.g. "Peterson Academy", "MasterClass", "The Great Courses"
    title: str
    instructor: str = ""
    course_code: str = ""
    description: str = ""
    poster: str = ""
    background: str = ""
    episode_thumbnail: str = ""
    genres: List[str] = dataclasses.field(default_factory=list)
    lectures: List[LectureMetadata] = dataclasses.field(default_factory=list)
    slug: str = ""


# ============================================================================
# 2. HTTP & Parsing Helpers
# ============================================================================


def fetch_url_text(url: str, timeout: int = 15) -> Optional[str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def build_typographic_artwork(title: str, subtitle: str = "") -> Tuple[str, str, str]:
    """Generates clean dark-mode typographic poster, backdrop, and thumbnail URLs."""
    label = f"{title}\n{subtitle}".strip() if subtitle else title
    encoded = urllib.parse.quote(label)
    poster = f"https://placehold.co/600x900/111827/f9fafb/png?text={encoded}"
    backdrop = f"https://placehold.co/1920x1080/111827/f9fafb/png?text={encoded}"
    thumb = f"https://placehold.co/640x360/1f2937/f9fafb/png?text={encoded}"
    return poster, backdrop, thumb


def clean_video_stem(filename: str) -> str:
    """Strips extension, leading numbering, and release tags from a video filename."""
    stem = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", filename.split("/")[-1])
    stem = re.sub(
        r"^(?:s\d+e\d+|ep?\s*\d+|lecture\s*\d+|lesson\s*\d+|part\s*\d+|\d+)[\.\s_:\-]+",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    stem = stem.replace("_", " ").strip()
    return stem or filename


# ============================================================================
# 3. Pluggable Course Metadata Providers
# ============================================================================


class CourseProvider:
    name: str = "Generic"

    def can_handle(
        self,
        display_title: str,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> bool:
        raise NotImplementedError

    def fetch_metadata(
        self,
        display_title: str,
        torbox_files: List[Dict[str, Any]],
        torbox_torrent_id: int = 0,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> Optional[CourseMetadata]:
        raise NotImplementedError


class PetersonAcademyProvider(CourseProvider):
    name = "Peterson Academy"
    BASE_URL = "https://petersonacademy.com"

    def can_handle(
        self,
        display_title: str,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> bool:
        if explicit_url and "petersonacademy.com" in explicit_url.lower():
            return True
        if explicit_slug:
            return True
        return "peterson academy" in display_title.lower()

    @staticmethod
    def infer_slug(
        display_title: str,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> Optional[str]:
        if explicit_url and "petersonacademy.com/courses/" in explicit_url.lower():
            path = urllib.parse.urlparse(explicit_url).path.rstrip("/")
            return path.split("/")[-1].lower()
        if explicit_slug:
            return explicit_slug.strip().strip("/").lower()

        cleaned = re.sub(
            r"\b(EDU4U|1080p|720p|2160p|4K|WEB-DL|WEBRip|x264|x265|HEVC|AAC|MP4|MKV)\b.*",
            "",
            display_title,
            flags=re.IGNORECASE,
        ).strip(" -_.")
        parts = [p.strip() for p in re.split(r"\s+-\s+|\s+–\s+", cleaned) if p.strip()]
        if len(parts) >= 2 and "peterson" in parts[0].lower():
            course_name = parts[1]
        elif parts:
            course_name = re.sub(r"^peterson\s+academy\s*[:\-]?\s*", "", parts[0], flags=re.IGNORECASE)
        else:
            return None
        slug = re.sub(r"[^a-z0-9]+", "-", course_name.lower()).strip("-")
        return slug or None

    def fetch_metadata(
        self,
        display_title: str,
        torbox_files: List[Dict[str, Any]],
        torbox_torrent_id: int = 0,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> Optional[CourseMetadata]:
        slug = self.infer_slug(display_title, explicit_url, explicit_slug)
        if not slug:
            return None

        url = f"{self.BASE_URL}/courses/{slug}"
        raw_html = fetch_url_text(url)
        if not raw_html:
            return None

        m = re.search(
            r'previewCourse:\{id:(\d+),title:"([^"]+)",description:"((?:\\.|[^"])*)",slug:"([^"]+)",label:[^,]+,courseCode:"([^"]+)"',
            raw_html,
        )
        if not m:
            return None

        course_id, title, description, parsed_slug, course_code = m.groups()
        description = description.encode("utf-8").decode("unicode_escape", errors="replace")

        inst_match = re.search(
            r'instructors:\[\{id:\d+,fullName:"([^"]+)",slug:"([^"]+)",title:"([^"]+)"',
            raw_html[m.start() : m.start() + 4000],
        )
        instructor = inst_match.group(1) if inst_match else ""

        trailer_match = re.search(
            r'trailerPlaybackId:"([^"]+)"',
            raw_html[m.start() : m.start() + 2000],
        )
        mux_id = trailer_match.group(1).strip() if trailer_match and trailer_match.group(1) else None

        poster_url = f"https://ik.imagekit.io/0qkyxdfkk/prod/courses%2F{course_id}%2Fthumb?tr=h-1200&alt=media"
        hero_url = f"https://ik.imagekit.io/0qkyxdfkk/prod/courses%2F{course_id}%2Fhero?tr=w-1920&alt=media"
        thumb_landscape = (
            f"https://image.mux.com/{mux_id}/thumbnail.jpg?width=640"
            if mux_id
            else f"https://ik.imagekit.io/0qkyxdfkk/prod/courses%2F{course_id}%2Fhero?tr=w-640&alt=media"
        )

        lec_pattern = re.compile(
            r'\{id:"[^"]+",lectureId:(\d+),lectureNumber:(\d+),path:"([^"]+)",title:"((?:\\.|[^"])*)",description:"((?:\\.|[^"])*)",durationSeconds:(\d+),muxAssetId:"([^"]+)"'
        )
        lectures_by_num: Dict[int, LectureMetadata] = {}
        for lm in lec_pattern.finditer(raw_html):
            _, lec_num_str, _, lec_title, lec_desc, dur_str, _ = lm.groups()
            lec_num = int(lec_num_str)
            if lec_num not in lectures_by_num:
                lectures_by_num[lec_num] = LectureMetadata(
                    number=lec_num,
                    title=html.unescape(lec_title),
                    description=html.unescape(lec_desc),
                    duration_seconds=int(dur_str),
                    thumbnail=thumb_landscape,
                )

        genres = ["Peterson Academy", "History", "Humanities", "Education"]

        return CourseMetadata(
            stremio_id=f"sw_pa_{course_id}",
            provider="Peterson Academy",
            title=title,
            instructor=instructor,
            course_code=course_code,
            description=description,
            poster=poster_url,
            background=hero_url,
            episode_thumbnail=thumb_landscape,
            genres=genres,
            lectures=[lectures_by_num[k] for k in sorted(lectures_by_num)],
            slug=parsed_slug or slug,
        )


class GenericWebCourseProvider(CourseProvider):
    """Scrapes any course URL (MasterClass, Wondrium, Coursera, Gumroad, YouTube, etc.) via JSON-LD & OpenGraph."""

    name = "Web Course"

    KNOWN_DOMAINS = {
        "masterclass.com": ("MasterClass", "mc"),
        "wondrium.com": ("Wondrium", "won"),
        "thegreatcourses.com": ("The Great Courses", "tgc"),
        "coursera.org": ("Coursera", "crs"),
        "udemy.com": ("Udemy", "udm"),
        "edx.org": ("edX", "edx"),
        "ocw.mit.edu": ("MIT OpenCourseWare", "mit"),
        "youtube.com": ("YouTube Courses", "yt"),
    }

    def can_handle(
        self,
        display_title: str,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> bool:
        return bool(explicit_url and explicit_url.startswith(("http://", "https://")))

    def fetch_metadata(
        self,
        display_title: str,
        torbox_files: List[Dict[str, Any]],
        torbox_torrent_id: int = 0,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> Optional[CourseMetadata]:
        if not explicit_url:
            return None
        raw_html = fetch_url_text(explicit_url)
        if not raw_html:
            return None

        parsed_url = urllib.parse.urlparse(explicit_url)
        host = parsed_url.netloc.lower().removeprefix("www.")
        provider_name, prefix = self.KNOWN_DOMAINS.get(host, ("Course Library", "web"))

        def og(prop: str) -> str:
            m = re.search(
                rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]+)"',
                raw_html,
                re.IGNORECASE,
            ) or re.search(
                rf'<meta\s+content="([^"]+)"\s+(?:property|name)="{re.escape(prop)}"',
                raw_html,
                re.IGNORECASE,
            )
            return html.unescape(m.group(1).strip()) if m else ""

        title = og("og:title") or display_title
        description = og("og:description") or og("description")
        image = og("og:image") or og("twitter:image")
        site_name = og("og:site_name")
        if provider_name == "Course Library" and site_name:
            provider_name = site_name

        instructor = ""
        lectures: List[LectureMetadata] = []

        # Inspect Schema.org JSON-LD blocks if present
        for ld_match in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            raw_html,
            re.DOTALL | re.IGNORECASE,
        ):
            try:
                data = json.loads(ld_match.group(1).strip())
                items = data if isinstance(data, list) else data.get("@graph", [data])
                for obj in items:
                    if not isinstance(obj, dict):
                        continue
                    otype = str(obj.get("@type", ""))
                    if otype in ("Course", "CreativeWorkSeries", "TVSeries"):
                        title = obj.get("name") or title
                        description = obj.get("description") or description
                        inst = obj.get("instructor") or obj.get("author") or obj.get("creator")
                        if isinstance(inst, dict):
                            instructor = inst.get("name", "")
                        elif isinstance(inst, list) and inst and isinstance(inst[0], dict):
                            instructor = inst[0].get("name", "")
                        parts = obj.get("hasPart") or obj.get("syllabusSections") or []
                        for idx, part in enumerate(parts, 1):
                            if isinstance(part, dict) and part.get("name"):
                                lectures.append(
                                    LectureMetadata(
                                        number=idx,
                                        title=str(part["name"]),
                                        description=str(part.get("description", "")),
                                        thumbnail=image,
                                    )
                                )
            except Exception:
                continue

        short_hash = hashlib.sha1(explicit_url.encode("utf-8")).hexdigest()[:8]
        poster, backdrop, thumb = build_typographic_artwork(title, instructor or provider_name)

        return CourseMetadata(
            stremio_id=f"sw_{prefix}_{short_hash}",
            provider=provider_name,
            title=title,
            instructor=instructor,
            description=description,
            poster=image or poster,
            background=image or backdrop,
            episode_thumbnail=image or thumb,
            genres=[provider_name, "Education"],
            lectures=lectures,
            slug=short_hash,
        )


class TorBoxFileFallbackProvider(CourseProvider):
    """Zero-config fallback provider that constructs structured metadata from any multi-video torrent."""

    name = "Course Library"

    PROVIDER_KEYWORDS = {
        "masterclass": ("MasterClass", "mc"),
        "the great courses": ("The Great Courses", "tgc"),
        "wondrium": ("Wondrium", "won"),
        "ttc": ("The Great Courses", "tgc"),
        "peterson academy": ("Peterson Academy", "pa"),
        "coursera": ("Coursera", "crs"),
        "udemy": ("Udemy", "udm"),
        "mit ocw": ("MIT OpenCourseWare", "mit"),
        "bbc": ("BBC Documentaries", "bbc"),
        "pbs": ("PBS Documentaries", "pbs"),
        "curiositystream": ("Curiosity Stream", "cts"),
    }

    def can_handle(
        self,
        display_title: str,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> bool:
        return True

    def fetch_metadata(
        self,
        display_title: str,
        torbox_files: List[Dict[str, Any]],
        torbox_torrent_id: int = 0,
        explicit_url: Optional[str] = None,
        explicit_slug: Optional[str] = None,
    ) -> Optional[CourseMetadata]:
        cleaned = re.sub(
            r"\b(EDU4U|1080p|720p|2160p|4K|WEB-DL|WEBRip|BluRay|x264|x265|HEVC|AAC|MP4|MKV)\b.*",
            "",
            display_title,
            flags=re.IGNORECASE,
        ).strip(" -_.")

        provider_name = "Course Library"
        prefix = "tb"
        lower_title = cleaned.lower()
        for kw, (pname, pfx) in self.PROVIDER_KEYWORDS.items():
            if kw in lower_title:
                provider_name, prefix = pname, pfx
                break

        parts = [p.strip() for p in re.split(r"\s+-\s+|\s+–\s+", cleaned) if p.strip()]
        instructor = ""
        if len(parts) >= 3:
            course_title = parts[1]
            instructor = parts[2]
        elif len(parts) == 2:
            if parts[0].lower() in self.PROVIDER_KEYWORDS:
                course_title = parts[1]
            else:
                course_title, instructor = parts[0], parts[1]
        else:
            course_title = cleaned or display_title

        poster, backdrop, thumb = build_typographic_artwork(course_title, instructor or provider_name)
        ep_map = match_torbox_files_to_lectures(torbox_files, [])
        lectures = [
            LectureMetadata(
                number=ep_num,
                title=clean_video_stem(fname),
                description=f"Lecture {ep_num} from {course_title}"
                + (f" ({instructor})" if instructor else ""),
                thumbnail=thumb,
            )
            for ep_num, (_, fname) in sorted(ep_map.items())
        ]

        id_suffix = str(torbox_torrent_id) if torbox_torrent_id else hashlib.sha1(display_title.encode()).hexdigest()[:8]
        return CourseMetadata(
            stremio_id=f"sw_{prefix}_{id_suffix}",
            provider=provider_name,
            title=course_title,
            instructor=instructor,
            description=f"{course_title}" + (f" taught by {instructor}." if instructor else "."),
            poster=poster,
            background=backdrop,
            episode_thumbnail=thumb,
            genres=[provider_name, "Education"],
            lectures=lectures,
            slug=re.sub(r"[^a-z0-9]+", "-", course_title.lower()).strip("-"),
        )


PROVIDERS: List[CourseProvider] = [
    PetersonAcademyProvider(),
    GenericWebCourseProvider(),
    TorBoxFileFallbackProvider(),
]


def resolve_course_metadata(
    display_title: str,
    torbox_files: List[Dict[str, Any]],
    torbox_torrent_id: int = 0,
    explicit_url: Optional[str] = None,
    explicit_slug: Optional[str] = None,
    allow_fallback: bool = False,
) -> Optional[CourseMetadata]:
    """Runs the provider chain and returns normalized `CourseMetadata`."""
    chain = PROVIDERS if allow_fallback else PROVIDERS[:-1]
    for provider in chain:
        if provider.can_handle(display_title, explicit_url=explicit_url, explicit_slug=explicit_slug):
            meta = provider.fetch_metadata(
                display_title,
                torbox_files=torbox_files,
                torbox_torrent_id=torbox_torrent_id,
                explicit_url=explicit_url,
                explicit_slug=explicit_slug,
            )
            if meta:
                return meta
    return None


# ============================================================================
# 4. Smart Episode-to-File Matcher
# ============================================================================


def extract_episode_number_from_filename(short_name: str) -> Optional[int]:
    """Extracts 1-based episode number from common course/series naming patterns."""
    patterns = [
        r"(?:s\d+e|ep?\s*|lecture\s*|lesson\s*|part\s*|chapter\s*|module\s*\d+\s*[-_.]\s*)0*(\d+)\b",
        r"^\s*0*(\d+)[\.\s_:\-]",
    ]
    for pat in patterns:
        m = re.search(pat, short_name, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def match_torbox_files_to_lectures(
    files: List[Dict[str, Any]],
    lectures: List[LectureMetadata],
) -> Dict[int, Tuple[int, str]]:
    """Maps 1-based episode number -> (torbox_file_idx, short_filename)."""
    ep_map: Dict[int, Tuple[int, str]] = {}
    video_entries: List[Tuple[int, str]] = []

    for idx, f in enumerate(files):
        raw_name = str(f.get("short_name") or f.get("name") or "")
        short_name = raw_name.split("/")[-1]
        if not short_name.lower().endswith(VIDEO_EXTENSIONS):
            continue
        file_idx = int(f.get("id")) if f.get("id") is not None and int(f.get("id")) < 500 else idx
        video_entries.append((file_idx, short_name))
        ep_num = extract_episode_number_from_filename(short_name)
        if ep_num is not None and ep_num not in ep_map:
            ep_map[ep_num] = (file_idx, short_name)

    # Fuzzy title fallback for unnumbered files when lecture titles are known
    if len(ep_map) < len(lectures) and video_entries:
        used_indices = {f_idx for f_idx, _ in ep_map.values()}
        for lec in lectures:
            if lec.number in ep_map:
                continue
            lec_tokens = set(re.findall(r"[a-z0-9]{3,}", lec.title.lower()))
            if not lec_tokens:
                continue
            best_match: Optional[Tuple[int, str]] = None
            best_score = 0
            for f_idx, s_name in video_entries:
                if f_idx in used_indices:
                    continue
                file_tokens = set(re.findall(r"[a-z0-9]{3,}", s_name.lower()))
                score = len(lec_tokens & file_tokens)
                if score > best_score:
                    best_score = score
                    best_match = (f_idx, s_name)
            if best_match and best_score > 0:
                ep_map[lec.number] = best_match
                used_indices.add(best_match[0])

    if not ep_map:
        for ep_num, (file_idx, short_name) in enumerate(sorted(video_entries, key=lambda x: x[1]), 1):
            ep_map[ep_num] = (file_idx, short_name)

    return ep_map


# ============================================================================
# 5. Static Stremio Addon Generator
# ============================================================================


def build_static_addon_for_course(
    course_meta: CourseMetadata,
    info_hash: str,
    torbox_torrent_id: int,
    file_index_by_episode: Dict[int, Tuple[int, str]],
) -> None:
    """Writes static Stremio addon JSON files (manifest, catalog, meta, stream) into addon/."""
    stremio_id = course_meta.stremio_id
    torbox_lib_id = f"aiostreams::library.torbox.torrent.{torbox_torrent_id}"

    lectures = course_meta.lectures
    if not lectures and file_index_by_episode:
        lectures = [
            LectureMetadata(
                number=ep_num,
                title=clean_video_stem(fname),
                thumbnail=course_meta.episode_thumbnail,
            )
            for ep_num, (_, fname) in sorted(file_index_by_episode.items())
        ]

    videos_series: List[Dict[str, Any]] = []
    videos_library: List[Dict[str, Any]] = []

    for lec in lectures:
        ep_num = lec.number
        file_idx, filename = file_index_by_episode.get(
            ep_num, (ep_num - 1, f"{ep_num}. {lec.title}.mp4")
        )
        dur_mins = round(lec.duration_seconds / 60) if lec.duration_seconds else 0
        ep_thumb = lec.thumbnail or course_meta.episode_thumbnail

        header_bits = []
        if dur_mins > 0:
            header_bits.append(f"⏱️ {dur_mins} min")
        if course_meta.instructor:
            header_bits.append(course_meta.instructor)
        elif course_meta.provider:
            header_bits.append(course_meta.provider)

        header = " · ".join(header_bits)
        ep_overview = f"{header}\n\n{lec.description}".strip() if header else lec.description

        vid_series_id = f"{stremio_id}_s{lec.season}e{ep_num}"
        vid_playback_id = f"{torbox_lib_id}:{file_idx}" if torbox_torrent_id else vid_series_id

        videos_series.append(
            {
                "id": vid_playback_id,
                "title": f"{ep_num}. {lec.title}",
                "season": lec.season,
                "episode": ep_num,
                "thumbnail": ep_thumb,
                "overview": ep_overview,
                "description": ep_overview,
            }
        )

        videos_library.append(
            {
                "id": f"{torbox_lib_id}:{file_idx}",
                "title": f"{ep_num}. {lec.title}",
                "season": lec.season,
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
                    "name": course_meta.provider,
                    "title": filename,
                    "infoHash": info_hash.lower(),
                    "fileIdx": file_idx,
                    "behaviorHints": {
                        "filename": filename,
                        "bingeGroup": f"sw-{course_meta.slug or stremio_id}",
                    },
                }
            ]
        }
        stream_path.write_text(
            json.dumps(stream_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    display_name = (
        f"{course_meta.title} — {course_meta.instructor}"
        if course_meta.instructor
        else course_meta.title
    )
    full_desc = (
        f"[{course_meta.course_code}] {course_meta.description}"
        if course_meta.course_code
        else course_meta.description
    )

    catalog_card = {
        "id": stremio_id,
        "type": "series",
        "name": display_name,
        "poster": course_meta.poster,
        "background": course_meta.background,
        "description": full_desc,
        "releaseInfo": course_meta.course_code or course_meta.provider,
        "genres": course_meta.genres,
        "cast": [course_meta.instructor] if course_meta.instructor else [],
    }

    # Update unified catalog (`addon/catalog/series/courses.json`)
    catalog_path = ADDON_DIR / "catalog" / "series" / "courses.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    existing_metas: List[Dict[str, Any]] = []
    if catalog_path.exists():
        try:
            existing_metas = json.loads(catalog_path.read_text(encoding="utf-8")).get("metas", [])
        except Exception:
            existing_metas = []

    # Remove old ID or legacy pa_ ID for the same course
    legacy_id = stremio_id.replace("sw_pa_", "pa_")
    existing_metas = [
        m for m in existing_metas if m.get("id") not in (stremio_id, legacy_id)
    ]
    existing_metas.insert(0, catalog_card)
    catalog_path.write_text(
        json.dumps({"metas": existing_metas}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Also build static per-genre catalog slices (`addon/catalog/series/courses/genre=<Genre>.json`)
    all_genres: List[str] = []
    for m in existing_metas:
        for g in m.get("genres", []):
            if g not in all_genres:
                all_genres.append(g)

    genre_dir = ADDON_DIR / "catalog" / "series" / "courses"
    genre_dir.mkdir(parents=True, exist_ok=True)
    for g in all_genres:
        filtered = [m for m in existing_metas if g in m.get("genres", [])]
        (genre_dir / f"genre={g}.json").write_text(
            json.dumps({"metas": filtered}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # Write series meta (`addon/meta/series/sw_*.json`)
    meta_series_path = ADDON_DIR / "meta" / "series" / f"{stremio_id}.json"
    meta_series_path.parent.mkdir(parents=True, exist_ok=True)
    meta_series_payload = {"meta": {**catalog_card, "videos": videos_series}}
    meta_series_path.write_text(
        json.dumps(meta_series_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Write TorBox library meta (`addon/meta/library/aiostreams::library.torbox.torrent.<id>.json`)
    if torbox_torrent_id:
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

    # Write provider-agnostic manifest (`addon/manifest.json`)
    manifest = {
        "id": "com.darshan.streamweaver",
        "version": "1.1.0",
        "name": "Stream Weaver Courses",
        "description": "Rich posters, backdrops, lecture thumbnails, and synopses for courses and educational series on TorBox",
        "logo": "https://petersonacademy.com/favicon-apple-touch-v3.png",
        "resources": ["catalog", "meta", "stream"],
        "types": ["series", "library"],
        "idPrefixes": ["sw_", "pa_", "aiostreams::library.torbox.torrent."],
        "catalogs": [
            {
                "type": "series",
                "id": "courses",
                "name": "Courses & Lectures",
                "extra": [
                    {
                        "name": "genre",
                        "isRequired": false if False else False,
                        "options": all_genres,
                    }
                ],
            }
        ],
    }
    manifest_path = ADDON_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build static Stremio course metadata for TorBox torrents"
    )
    parser.add_argument("--title", default="", help="Torrent display title")
    parser.add_argument("--slug", help="Optional Peterson Academy slug (e.g. 'napoleon')")
    parser.add_argument("--url", help="Optional course webpage URL (MasterClass, Wondrium, etc.)")
    parser.add_argument("--hash", required=True, help="40-char torrent infoHash")
    parser.add_argument("--torrent-id", type=int, default=0, help="TorBox torrent ID")
    parser.add_argument("--clean", action="store_true", help="Clean addon/ directory before building")
    args = parser.parse_args()

    if args.clean and ADDON_DIR.exists():
        import shutil

        shutil.rmtree(ADDON_DIR)

    from magnet import fetch_torbox_torrent_files

    env = load_env(ENV_FILE)
    api_key = env.get("SECRET_TORBOX_API_KEY", "")
    tb_files = (
        fetch_torbox_torrent_files(args.torrent_id, api_key)
        if (args.torrent_id and api_key)
        else []
    )

    display_title = args.title or args.slug or args.url or args.hash
    meta = resolve_course_metadata(
        display_title=display_title,
        torbox_files=tb_files,
        torbox_torrent_id=args.torrent_id,
        explicit_url=args.url,
        explicit_slug=args.slug,
        allow_fallback=True,
    )
    if not meta:
        print(f"❌ Could not build metadata for '{display_title}'", file=sys.stderr)
        return 1

    ep_map = match_torbox_files_to_lectures(tb_files, meta.lectures)
    build_static_addon_for_course(meta, args.hash, args.torrent_id, ep_map)
    print(f"✅ Built {meta.provider} metadata for '{meta.title}' ({meta.stremio_id})")
    print(f"   Poster:   {meta.poster}")
    print(f"   Backdrop: {meta.background}")
    print(f"   Lectures: {len(meta.lectures)} episodes mapped in {ADDON_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
