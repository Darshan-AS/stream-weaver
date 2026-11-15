"""Unit tests for inline custom catalog definitions, TMDB discover builder, and MDBList parser."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from stream_weaver.core.composition import (
    AddonInfo,
    AddonResolver,
    compile_collections,
    derive_catalogs,
    eject_collections,
)
from stream_weaver.core.custom_builder import (
    build_mdblist_source,
    build_tmdb_discover_sources,
    custom_builder_metadata,
    parse_mdblist_reference,
)
from stream_weaver.core.library import Library
from stream_weaver.core.result import Err, Ok

REPO = Path(__file__).resolve().parents[2]
SHARED_LIB = REPO / "library"
EXAMPLE = REPO / "examples" / "home"


def test_custom_tmdb_and_mdblist_builders_compile_and_materialize_inline() -> None:
    meta_tables = custom_builder_metadata()
    assert len(meta_tables["genres"]) >= 12
    assert len(meta_tables["languages"]) >= 20

    tmdb_res = build_tmdb_discover_sources(
        "90s Korean Thrillers",
        media="both",
        genre="thriller",
        language="ko",
        decade="1990s",
        sort="vote_average.desc",
    )
    assert isinstance(tmdb_res, Ok)
    assert len(tmdb_res.value) == 2
    assert tmdb_res.value[0]["catalog"]["metadata"]["discover"]["params"]["with_original_language"] == "ko"
    assert tmdb_res.value[0]["catalog"]["metadata"]["discover"]["params"]["primary_release_date.gte"] == "1990-01-01"

    mdb_res = build_mdblist_source(88434, title="Festival Winners", media_type="movie")
    assert isinstance(mdb_res, Ok)
    assert mdb_res.value["catalog"]["id"] == "mdblist.88434"
    assert mdb_res.value["catalog"]["source"] == "mdblist"

    inline_col = {
        "id": "col-user-curated",
        "title": "My Custom Picks",
        "folders": [
            {
                "id": "fld-ko-90s",
                "title": "90s Korean Thrillers",
                "sources": tmdb_res.value,
            },
            {
                "id": "fld-fest",
                "title": "Festival Winners",
                "sources": [mdb_res.value],
            },
        ],
    }

    lib = Library.load((SHARED_LIB,))
    resolver = AddonResolver(addons={"aiometadata": AddonInfo(name="aiometadata", addon_id="aio-metadata")})
    compiled_res = compile_collections([inline_col], lib, resolver)
    assert isinstance(compiled_res, Ok), compiled_res

    base_meta = json.loads((EXAMPLE / "aiometadata.json").read_text(encoding="utf-8"))
    derived_res = derive_catalogs(base_meta["catalogs"], compiled_res.value.catalog_uses)
    assert isinstance(derived_res, Ok), derived_res
    enabled_ids = {(c["id"], c["type"]) for c in derived_res.value if c["enabled"]}
    assert ("mdblist.88434", "movie") in enabled_ids
    assert (tmdb_res.value[0]["catalog"]["id"], "movie") in enabled_ids
    assert (tmdb_res.value[1]["catalog"]["id"], "series") in enabled_ids

    # `eject_collections` preserves inline `catalog` definitions even when another folder on TV changed
    remote_cols = copy.deepcopy(list(compiled_res.value.collections))
    remote_cols[0]["title"] = "Renamed On TV"
    ejected = eject_collections(remote_cols, [inline_col], compiled_res.value, lib, resolver)
    assert ejected[0]["title"] == "Renamed On TV"
    assert ejected[0]["folders"][1]["sources"][0]["catalog"]["id"] == "mdblist.88434"


def test_parse_mdblist_reference_variants() -> None:
    r_num = parse_mdblist_reference("88434")
    assert isinstance(r_num, Ok)
    assert r_num.value.list_id == "88434"

    r_pref = parse_mdblist_reference("mdblist.12345")
    assert isinstance(r_pref, Ok)
    assert r_pref.value.list_id == "12345"

    r_url_id = parse_mdblist_reference("https://mdblist.com/lists/99887/")
    assert isinstance(r_url_id, Ok)
    assert r_url_id.value.list_id == "99887"

    r_slug = parse_mdblist_reference("https://www.mdblist.com/lists/alice/cyberpunk-gems")
    assert isinstance(r_slug, Ok)
    assert (r_slug.value.username, r_slug.value.slug) == ("alice", "cyberpunk-gems")

    r_short = parse_mdblist_reference("alice/cyberpunk-gems")
    assert isinstance(r_short, Ok)
    assert (r_short.value.username, r_short.value.slug) == ("alice", "cyberpunk-gems")

    assert isinstance(parse_mdblist_reference(""), Err)
    assert isinstance(parse_mdblist_reference("https://example.com/lists/foo/bar"), Err)
    assert isinstance(build_tmdb_discover_sources("Empty Filters"), Err)
