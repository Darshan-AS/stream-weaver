"""
Pure builders for user-defined custom Nuvio folders & inline AIOMetadata catalogs
(TMDB Discover filter rows and MDBList community/personal list rows).

Why inline catalog definitions? Instead of committing user-specific catalogs into the shared
`library/` tree, custom sources embed their `catalog` definition directly inside the profile's
folder `sources[]`; `compile_collections` + `derive_catalogs` materialize and enable them in the
user's `aiometadata.json` automatically, keeping the shared repo free of catalog bloat.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict


@dataclass(frozen=True, slots=True)
class TmdbGenreSpec:
    slug: str
    label: str
    movie_id: str
    tv_id: str


TMDB_GENRES: tuple[TmdbGenreSpec, ...] = (
    TmdbGenreSpec("action", "Action & Adventure", "28", "10759"),
    TmdbGenreSpec("scifi", "Sci-Fi", "878", "10765"),
    TmdbGenreSpec("fantasy", "Fantasy", "14", "10765"),
    TmdbGenreSpec("thriller", "Thriller & Mystery", "53", "9648"),
    TmdbGenreSpec("crime", "Crime", "80", "80"),
    TmdbGenreSpec("horror", "Horror", "27", "9648"),
    TmdbGenreSpec("drama", "Drama", "18", "18"),
    TmdbGenreSpec("comedy", "Comedy", "35", "35"),
    TmdbGenreSpec("romance", "Romance", "10749", "18"),
    TmdbGenreSpec("animation", "Animation", "16", "16"),
    TmdbGenreSpec("documentary", "Documentary", "99", "99"),
    TmdbGenreSpec("family", "Kids & Family", "10751", "10762"),
    TmdbGenreSpec("history", "History & War", "36,10752", "10768"),
    TmdbGenreSpec("music", "Music", "10402", "18"),
    TmdbGenreSpec("western", "Western", "37", "37"),
)

_GENRES_BY_SLUG = {g.slug: g for g in TMDB_GENRES}

TMDB_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("zh", "Chinese"),
    ("ar", "Arabic"),
    ("tr", "Turkish"),
    ("ru", "Russian"),
    ("pl", "Polish"),
    ("nl", "Dutch"),
    ("sv", "Swedish"),
    ("da", "Danish"),
    ("no", "Norwegian"),
    ("th", "Thai"),
    ("hi", "Hindi"),
    ("kn", "Kannada"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("ml", "Malayalam"),
    ("bn", "Bengali"),
    ("mr", "Marathi"),
)

_LANGS_BY_CODE = dict(TMDB_LANGUAGES)

TMDB_DECADES: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("any", "Any Era", None, None),
    ("2020s", "2020s", "2020-01-01", "2029-12-31"),
    ("2010s", "2010s", "2010-01-01", "2019-12-31"),
    ("2000s", "2000s", "2000-01-01", "2009-12-31"),
    ("1990s", "1990s", "1990-01-01", "1999-12-31"),
    ("1980s", "1980s", "1980-01-01", "1989-12-31"),
    ("1970s", "1970s", "1970-01-01", "1979-12-31"),
    ("classic", "Pre-1970 Classics", None, "1969-12-31"),
)

_DECADES_BY_ID = {d[0]: d for d in TMDB_DECADES}

TMDB_SORT_MODES: tuple[tuple[str, str], ...] = (
    ("popularity.desc", "Most Popular"),
    ("vote_average.desc", "Highest Rated"),
    ("vote_count.desc", "Most Acclaimed"),
    ("release_date.desc", "Newest Releases"),
)

_VALID_SORTS = {s[0] for s in TMDB_SORT_MODES}


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return s[:28] or "custom"


def custom_builder_metadata() -> JsonDict:
    """Metadata tables for the Studio Custom Folder / Row Builder UI."""
    return {
        "genres": [{"slug": g.slug, "label": g.label} for g in TMDB_GENRES],
        "languages": [{"code": code, "label": lbl} for code, lbl in TMDB_LANGUAGES],
        "decades": [{"id": d[0], "label": d[1]} for d in TMDB_DECADES],
        "sorts": [{"id": s[0], "label": s[1]} for s in TMDB_SORT_MODES],
    }


def build_tmdb_discover_sources(
    title: str,
    *,
    media: str = "both",
    genre: str = "",
    language: str = "",
    decade: str = "any",
    sort: str = "popularity.desc",
    min_votes: int | None = None,
) -> Result[list[JsonDict], ConfigValidationError]:
    """
    Builds 1 (`movie` or `series`) or 2 (`both`) symbolic AIOMetadata sources backed by inline
    `tmdb.discover.custom_*` catalog definitions.
    """
    clean_title = title.strip()
    if not clean_title:
        return Err(ConfigValidationError("Custom TMDB row requires a non-empty title"))
    media_norm = media.strip().lower()
    if media_norm not in ("movie", "series", "both"):
        return Err(ConfigValidationError(f"Unsupported media mode `{media}` (choose movie, series, or both)"))
    genre_key = genre.strip().lower()
    if genre_key and genre_key not in _GENRES_BY_SLUG:
        return Err(ConfigValidationError(f"Unknown TMDB genre `{genre}`"))
    lang_key = language.strip().lower()
    if lang_key and lang_key not in _LANGS_BY_CODE:
        return Err(ConfigValidationError(f"Unknown language code `{language}`"))
    decade_key = decade.strip().lower() or "any"
    if decade_key not in _DECADES_BY_ID:
        return Err(ConfigValidationError(f"Unknown decade `{decade}`"))
    sort_key = sort.strip() or "popularity.desc"
    if sort_key not in _VALID_SORTS:
        return Err(ConfigValidationError(f"Unknown sort mode `{sort}`"))
    if not genre_key and not lang_key and decade_key == "any":
        return Err(
            ConfigValidationError("Choose at least one filter (Genre, Language, or Era) for a custom TMDB row")
        )

    targets = ["movie", "series"] if media_norm == "both" else [media_norm]
    _, _, date_gte, date_lte = _DECADES_BY_ID[decade_key]
    sig = hashlib.sha1(f"{genre_key}|{lang_key}|{decade_key}|{sort_key}".encode()).hexdigest()[:6]
    base_slug = f"{_slugify(clean_title)}_{sig}"

    sources: list[JsonDict] = []
    for stype in targets:
        tmdb_media = "movie" if stype == "movie" else "tv"
        params: dict[str, Any] = {}
        if genre_key:
            g_spec = _GENRES_BY_SLUG[genre_key]
            params["with_genres"] = g_spec.movie_id if stype == "movie" else g_spec.tv_id
        if lang_key:
            params["with_original_language"] = lang_key
        date_prefix = "primary_release_date" if stype == "movie" else "first_air_date"
        if date_gte:
            params[f"{date_prefix}.gte"] = date_gte
        if date_lte:
            params[f"{date_prefix}.lte"] = date_lte

        if sort_key == "release_date.desc":
            params["sort_by"] = f"{date_prefix}.desc"
        else:
            params["sort_by"] = sort_key

        default_votes = 150 if sort_key == "vote_average.desc" else (25 if lang_key and lang_key != "en" else 50)
        if stype == "series":
            default_votes = max(15, default_votes // 2)
        params["vote_count.gte"] = min_votes if min_votes is not None else default_votes

        suffix = "movies" if stype == "movie" else "series"
        row_label = (
            f"{clean_title} — {'Movies' if stype == 'movie' else 'Series'}"
            if len(targets) > 1
            else clean_title
        )
        cat_id = f"tmdb.discover.custom_{base_slug}_{suffix}"
        cat_def: JsonDict = {
            "id": cat_id,
            "type": stype,
            "name": row_label,
            "displayType": stype,
            "source": "tmdb",
            "metadata": {
                "discover": {
                    "version": 1,
                    "mediaType": tmdb_media,
                    "params": params,
                }
            },
            "cacheTTL": 86400,
        }
        sources.append(
            {
                "addon": "aiometadata",
                "type": stype,
                "title": row_label,
                "catalog": cat_def,
            }
        )
    return Ok(sources)


@dataclass(frozen=True, slots=True)
class MdbListRef:
    list_id: str | None = None
    username: str | None = None
    slug: str | None = None


_NUMERIC_ID_RE = re.compile(r"^(?:mdblist\.)?(\d+)$", re.IGNORECASE)
_SLUG_PAIR_RE = re.compile(r"^([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$")


def parse_mdblist_reference(raw: str) -> Result[MdbListRef, ConfigValidationError]:
    """
    Parses an MDBList list reference from any user-friendly form:
    - Numeric ID: `88434` or `mdblist.88434`
    - Direct ID URL: `https://mdblist.com/lists/88434`
    - Public list URL or slug: `https://mdblist.com/lists/linaspurinis/top-watched-movies-of-the-week`
      or `linaspurinis/top-watched-movies-of-the-week`
    """
    cleaned = raw.strip()
    if not cleaned:
        return Err(ConfigValidationError("Enter an MDBList URL (https://mdblist.com/lists/user/list) or numeric ID"))
    m_num = _NUMERIC_ID_RE.match(cleaned)
    if m_num:
        return Ok(MdbListRef(list_id=m_num.group(1)))

    if "://" in cleaned or cleaned.lower().startswith("mdblist.com/"):
        norm_url = cleaned if "://" in cleaned else f"https://{cleaned}"
        parsed = urlsplit(norm_url)
        host = parsed.netloc.lower().removeprefix("www.")
        if host not in ("mdblist.com", "api.mdblist.com"):
            return Err(ConfigValidationError(f"Expected an mdblist.com URL, got `{parsed.netloc}`"))
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0].lower() == "lists":
            if parts[1].isdigit():
                return Ok(MdbListRef(list_id=parts[1]))
            if len(parts) >= 3:
                return Ok(MdbListRef(username=parts[1], slug=parts[2]))
        return Err(
            ConfigValidationError(
                "MDBList URL must look like https://mdblist.com/lists/<user>/<slug> or https://mdblist.com/lists/<id>"
            )
        )

    m_pair = _SLUG_PAIR_RE.match(cleaned.strip("/"))
    if m_pair:
        return Ok(MdbListRef(username=m_pair.group(1), slug=m_pair.group(2)))

    return Err(
        ConfigValidationError(
            f"Unrecognized MDBList input `{raw}` (use https://mdblist.com/lists/<user>/<slug> or numeric ID)"
        )
    )


def build_mdblist_source(
    list_id: str | int,
    *,
    title: str,
    media_type: str = "movie",
) -> Result[JsonDict, ConfigValidationError]:
    """Builds a symbolic AIOMetadata source backed by an inline `mdblist.<id>` catalog definition."""
    lid = str(list_id).strip().removeprefix("mdblist.")
    if not lid.isdigit():
        return Err(ConfigValidationError(f"MDBList numeric list ID must be digits, got `{list_id}`"))
    mtype = "series" if media_type.strip().lower() in ("series", "show", "tv", "shows") else "movie"
    clean_title = title.strip() or f"MDBList {lid}"
    cat_def: JsonDict = {
        "id": f"mdblist.{lid}",
        "name": clean_title,
        "type": mtype,
        "source": "mdblist",
        "sort": "default",
        "order": "asc",
        "cacheTTL": 3600,
    }
    return Ok(
        {
            "addon": "aiometadata",
            "type": mtype,
            "title": clean_title,
            "catalog": cat_def,
        }
    )
