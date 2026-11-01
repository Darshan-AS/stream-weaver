#!/usr/bin/env python3
"""
Parameterisation pass (Phase 2b): replaces the one-off regional catalog recipes with
parameterised `regional-*` recipes and adds the decade / classic-series / regional folder and
collection recipes used by Dad's profile. Idempotent; verifies that every replaced catalog renders
byte-identically before deleting the old file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stream_weaver.core.library import Library  # noqa: E402
from stream_weaver.core.result import Ok  # noqa: E402

LIB = ROOT / "library"
SCHEMA = "https://stream-weaver.dev/schemas/recipe.{kind}.v1.json"

LANG = {"language": {"type": "string"}, "slug": {"type": "string"}, "label": {"type": "string"}}


def _catalog(
    rid: str, *, ctype: str, cid: str, name: str, params: dict[str, Any], discover: dict[str, Any]
) -> dict[str, Any]:
    return {
        "$schema": SCHEMA.format(kind="catalog"),
        "version": 1,
        "kind": "catalog",
        "id": rid,
        "params": params,
        "catalog": {
            "id": cid,
            "type": ctype,
            "name": name,
            "displayType": ctype,
            "source": "tmdb",
            "metadata": {"discover": {"version": 1, "mediaType": "tv" if ctype == "series" else ctype, "params": discover}},
            "cacheTTL": 86400,
        },
    }


CATALOGS = {
    "regional-new-movies": _catalog(
        "regional-new-movies",
        ctype="movie",
        cid="tmdb.discover.${slug}_new_movies",
        name="New ${label} Releases",
        params={**LANG, "minVotes": {"type": "int", "default": 3}},
        discover={"with_original_language": "${language}", "sort_by": "primary_release_date.desc", "vote_count.gte": "${minVotes}"},
    ),
    "regional-popular-movies": _catalog(
        "regional-popular-movies",
        ctype="movie",
        cid="tmdb.discover.${slug}_movies",
        name="${label} Movies",
        params=LANG,
        discover={"with_original_language": "${language}", "sort_by": "popularity.desc"},
    ),
    "regional-top-movies": _catalog(
        "regional-top-movies",
        ctype="movie",
        cid="tmdb.discover.${slug}_top_movies",
        name="Top Rated ${label} Movies",
        params={**LANG, "minVotes": {"type": "int", "default": 20}},
        discover={"with_original_language": "${language}", "sort_by": "vote_average.desc", "vote_count.gte": "${minVotes}"},
    ),
    "regional-series": _catalog(
        "regional-series",
        ctype="series",
        cid="tmdb.discover.${slug}_series",
        name="${label} Series",
        params=LANG,
        discover={"with_original_language": "${language}", "sort_by": "popularity.desc"},
    ),
    "regional-decade-movies": _catalog(
        "regional-decade-movies",
        ctype="movie",
        cid="tmdb.discover.${slug}_${decade}_movies",
        name="${label} Classics — ${decadeLabel}",
        params={
            **LANG,
            "decade": {"type": "string"},
            "decadeLabel": {"type": "string"},
            "from": {"type": "string"},
            "to": {"type": "string"},
            "minVotes": {"type": "int", "default": 5},
        },
        discover={
            "with_original_language": "${language}",
            "primary_release_date.gte": "${from}",
            "primary_release_date.lte": "${to}",
            "sort_by": "vote_count.desc",
            "vote_count.gte": "${minVotes}",
        },
    ),
    "regional-classic-series": _catalog(
        "regional-classic-series",
        ctype="series",
        cid="tmdb.discover.${slug}_classic_series",
        name="${label} Classic Series",
        params={**LANG, "to": {"type": "string", "default": "2009-12-31"}, "minVotes": {"type": "int", "default": 3}},
        discover={
            "with_original_language": "${language}",
            "first_air_date.lte": "${to}",
            "sort_by": "vote_count.desc",
            "vote_count.gte": "${minVotes}",
        },
    ),
}

# One-off recipe → parameterised equivalent (must render identically before the one-off is removed).
REPLACEMENTS: dict[str, tuple[str, dict[str, Any]]] = {
    "tmdb.discover.kannada_new_movies": ("regional-new-movies", {"language": "kn", "slug": "kannada", "label": "Kannada"}),
    "tmdb.discover.kannada_movies": ("regional-popular-movies", {"language": "kn", "slug": "kannada", "label": "Kannada"}),
    "tmdb.discover.kannada_top_movies": ("regional-top-movies", {"language": "kn", "slug": "kannada", "label": "Kannada"}),
    "tmdb.discover.kannada_series": ("regional-series", {"language": "kn", "slug": "kannada", "label": "Kannada"}),
    "tmdb.discover.hindi_new_movies": ("regional-new-movies", {"language": "hi", "slug": "hindi", "label": "Hindi", "minVotes": 5}),
    "tmdb.discover.hindi_movies": ("regional-popular-movies", {"language": "hi", "slug": "hindi", "label": "Hindi"}),
    "tmdb.discover.hindi_series": ("regional-series", {"language": "hi", "slug": "hindi", "label": "Hindi"}),
}

DECADES_PARAM = {
    "type": "list",
    "items": "object",
    "default": [
        {"id": "80s", "label": "80s", "from": "1900-01-01", "to": "1989-12-31"},
        {"id": "90s", "label": "90s", "from": "1990-01-01", "to": "1999-12-31"},
        {"id": "2000s", "label": "2000s", "from": "2000-01-01", "to": "2009-12-31"},
    ],
}

FOLDERS = {
    "regional-movies": {
        "$schema": SCHEMA.format(kind="folder"),
        "version": 1,
        "kind": "folder",
        "id": "regional-movies",
        "params": {
            **LANG,
            "decades": DECADES_PARAM,
            "top": {"type": "bool", "default": True},
            "newMinVotes": {"type": "int", "default": 3},
            "topMinVotes": {"type": "int", "default": 20},
            "decadeMinVotes": {"type": "int", "default": 5},
            "coverImageUrl": {"type": "string", "default": ""},
        },
        "folder": {
            "id": "fld-${slug}-movies",
            "title": "${label} Movies",
            "sources": [
                {
                    "addon": "aiometadata",
                    "type": "movie",
                    "title": "New ${label} Releases",
                    "catalog": {"ref": "regional-new-movies", "with": {"language": "${language}", "slug": "${slug}", "label": "${label}", "minVotes": "${newMinVotes}"}},
                },
                {
                    "addon": "aiometadata",
                    "type": "movie",
                    "title": "Popular ${label} Movies",
                    "catalog": {"ref": "regional-popular-movies", "with": {"language": "${language}", "slug": "${slug}", "label": "${label}"}},
                },
                {
                    "if": "${top}",
                    "item": {
                        "addon": "aiometadata",
                        "type": "movie",
                        "title": "Top Rated ${label} Movies",
                        "catalog": {"ref": "regional-top-movies", "with": {"language": "${language}", "slug": "${slug}", "label": "${label}", "minVotes": "${topMinVotes}"}},
                    },
                },
                {
                    "for": "d",
                    "in": "${decades}",
                    "item": {
                        "addon": "aiometadata",
                        "type": "movie",
                        "title": "${label} Classics — ${d.label}",
                        "catalog": {
                            "ref": "regional-decade-movies",
                            "with": {
                                "language": "${language}",
                                "slug": "${slug}",
                                "label": "${label}",
                                "decade": "${d.id}",
                                "decadeLabel": "${d.label}",
                                "from": "${d.from}",
                                "to": "${d.to}",
                                "minVotes": "${decadeMinVotes}",
                            },
                        },
                    },
                },
            ],
            "hideTitle": False,
            "tileShape": "LANDSCAPE",
            "coverImageUrl": "${coverImageUrl}",
            "focusGifEnabled": False,
        },
    },
    "regional-series": {
        "$schema": SCHEMA.format(kind="folder"),
        "version": 1,
        "kind": "folder",
        "id": "regional-series",
        "params": {
            **LANG,
            "classics": {"type": "bool", "default": True},
            "classicsUntil": {"type": "string", "default": "2009-12-31"},
            "coverImageUrl": {"type": "string", "default": ""},
        },
        "folder": {
            "id": "fld-${slug}-series",
            "title": "${label} Series",
            "sources": [
                {
                    "addon": "aiometadata",
                    "type": "series",
                    "title": "Popular ${label} Series",
                    "catalog": {"ref": "regional-series", "with": {"language": "${language}", "slug": "${slug}", "label": "${label}"}},
                },
                {
                    "if": "${classics}",
                    "item": {
                        "addon": "aiometadata",
                        "type": "series",
                        "title": "${label} Classic Series",
                        "catalog": {"ref": "regional-classic-series", "with": {"language": "${language}", "slug": "${slug}", "label": "${label}", "to": "${classicsUntil}"}},
                    },
                },
            ],
            "hideTitle": False,
            "tileShape": "LANDSCAPE",
            "coverImageUrl": "${coverImageUrl}",
            "focusGifEnabled": False,
        },
    },
}

COLLECTIONS = {
    "regional-cinema": {
        "$schema": SCHEMA.format(kind="collection"),
        "version": 1,
        "kind": "collection",
        "id": "regional-cinema",
        "params": {
            **LANG,
            "decades": DECADES_PARAM,
            "series": {"type": "bool", "default": True},
            "moviesCoverImageUrl": {"type": "string", "default": ""},
            "seriesCoverImageUrl": {"type": "string", "default": ""},
            "backdropImageUrl": {"type": "string", "default": ""},
        },
        "collection": {
            "id": "col-${slug}",
            "title": "${label}",
            "folders": [
                {
                    "ref": "regional-movies",
                    "with": {"language": "${language}", "slug": "${slug}", "label": "${label}", "decades": "${decades}", "coverImageUrl": "${moviesCoverImageUrl}"},
                },
                {
                    "if": "${series}",
                    "item": {
                        "ref": "regional-series",
                        "with": {"language": "${language}", "slug": "${slug}", "label": "${label}", "coverImageUrl": "${seriesCoverImageUrl}"},
                    },
                },
            ],
            "pinToTop": False,
            "viewMode": "TABBED_GRID",
            "showAllTab": True,
            "backdropImageUrl": "${backdropImageUrl}",
            "focusGlowEnabled": True,
        },
    }
}


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    for rid, doc in CATALOGS.items():
        _write(LIB / "catalogs" / f"{rid}.json", doc)
    for rid, doc in FOLDERS.items():
        _write(LIB / "folders" / f"{rid}.json", doc)
    for rid, doc in COLLECTIONS.items():
        _write(LIB / "collections" / f"{rid}.json", doc)

    lib = Library.load([LIB])
    if lib.errors:
        sys.exit("\n".join(str(e) for e in lib.errors))

    # Verify + rewrite references in the one-off folder recipes, then delete the one-off catalogs.
    for old_id, (new_ref, params) in REPLACEMENTS.items():
        old_path = LIB / "catalogs" / f"{old_id}.json"
        if not old_path.exists():
            continue
        old_body = json.loads(old_path.read_text(encoding="utf-8"))["catalog"]
        rendered = lib.render("catalog", new_ref, params)
        if not isinstance(rendered, Ok) or rendered.value != old_body:
            shown = rendered.value if isinstance(rendered, Ok) else rendered.error
            sys.exit(f"❌ {new_ref}{params} does not reproduce {old_id}:\n{shown}\nvs\n{json.dumps(old_body, indent=1)}")
        for folder_file in sorted((LIB / "folders").glob("*.json")) + sorted((ROOT / "tenants").glob("*/library/folders/*.json")):
            doc = json.loads(folder_file.read_text(encoding="utf-8"))
            changed = False
            for src in doc.get("folder", {}).get("sources", []):
                if isinstance(src, dict) and src.get("catalog") == {"ref": old_id}:
                    src["catalog"] = {"ref": new_ref, "with": params}
                    changed = True
            if changed:
                _write(folder_file, doc)
                print(f"↻ {folder_file.relative_to(ROOT)}: {old_id} → {new_ref}{params}")
        old_path.unlink()
        print(f"🗑️  catalogs/{old_id}.json (now rendered by {new_ref})")
    print("✅ parameterised recipes in place")


if __name__ == "__main__":
    main()
