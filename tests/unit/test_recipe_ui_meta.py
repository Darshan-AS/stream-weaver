"""Unit tests for self-describing recipe UI metadata, parameter options, and `describe_recipe`."""

from __future__ import annotations

from pathlib import Path

from stream_weaver.core.library import Library, describe_recipe, parse_recipe, recipe_to_doc
from stream_weaver.core.result import Err, Ok

REPO = Path(__file__).resolve().parents[2]
SHARED_LIB = REPO / "library"
EXAMPLE_LIB = REPO / "examples" / "home" / "library"


def test_shared_library_ui_metadata_and_presets_round_trip() -> None:
    lib = Library.load((EXAMPLE_LIB, SHARED_LIB))
    assert not lib.errors, lib.errors

    reg = lib.get("collection", "regional-cinema")
    assert isinstance(reg, Ok)
    assert reg.value.ui.title == "Regional & World Cinema"
    assert len(reg.value.ui.presets) >= 10
    tamil = next(p for p in reg.value.ui.presets if p.params.get("slug") == "tamil")
    assert tamil.params["language"] == "ta"

    # Lossless round-trip of `ui` and parameter `options`
    doc = recipe_to_doc(reg.value)
    reparsed = parse_recipe(doc, source=reg.value.source)
    assert isinstance(reparsed, Ok)
    assert reparsed.value == reg.value


def test_describe_recipe_extracts_folder_previews_and_required_secrets() -> None:
    lib = Library.load((EXAMPLE_LIB, SHARED_LIB))

    fy = lib.get("collection", "col-for-you-discovery")
    assert isinstance(fy, Ok)
    fy_summary = describe_recipe(fy.value, lib, is_local=True)
    assert fy_summary.title == "For You & Discovery"
    assert len(fy_summary.folders) == 4
    assert fy_summary.folders[0].title == "AI Picks For You"
    assert fy_summary.folders[0].cover_url.startswith("https://")
    assert "SECRET_BINGECAT_TOKEN" in fy_summary.required_secrets
    assert "SECRET_ITSOUT_TOKEN" in fy_summary.required_secrets

    meta = lib.get("meta", "default")
    assert isinstance(meta, Ok)
    meta_summary = describe_recipe(meta.value, lib)
    assert set(meta_summary.required_secrets) == {"SECRET_MDBLIST_API_KEY", "SECRET_TMDB_API_KEY"}
    wp_param = meta_summary.params.by_name()["watchProgressSource"]
    assert [o.value for o in wp_param.options] == ["trakt", "simkl", "nuvio"]

    # Custom bound params override preset sample when describing `regional-cinema`
    reg = lib.get("collection", "regional-cinema")
    assert isinstance(reg, Ok)
    ta_summary = describe_recipe(
        reg.value, lib, with_params={"language": "ta", "slug": "tamil", "label": "Tamil"}
    )
    assert [f.id for f in ta_summary.folders] == ["fld-tamil-movies", "fld-tamil-series"]


def test_invalid_ui_preset_or_option_type_is_rejected() -> None:
    bad_preset = parse_recipe(
        {
            "version": 1,
            "kind": "collection",
            "id": "bad",
            "params": {"count": {"type": "int"}},
            "ui": {"presets": [{"label": "Oops", "with": {"count": "not-an-int"}}]},
            "collection": {"id": "c", "title": "${count}", "folders": []},
        },
        source=Path("bad.json"),
    )
    assert isinstance(bad_preset, Err)

    bad_option = parse_recipe(
        {
            "version": 1,
            "kind": "settings",
            "id": "bad",
            "params": {
                "size": {
                    "type": "int",
                    "default": 18,
                    "options": [{"label": "Big", "value": "twenty"}],
                }
            },
            "settings": {"version": "1.0.0"},
        },
        source=Path("bad.json"),
    )
    assert isinstance(bad_option, Err)


def test_expanded_library_collections_compile_and_derive_catalogs_cleanly() -> None:
    import json

    from stream_weaver.core.composition import (
        AddonInfo,
        AddonResolver,
        compile_collections,
        derive_catalogs,
    )

    lib = Library.load((EXAMPLE_LIB, SHARED_LIB))
    assert not lib.errors, lib.errors

    # Trakt hub infers SECRET_TRAKT_TOKEN_ID without needing BingeCat or ItsOut
    trakt_col = lib.get("collection", "col-trakt-personal")
    assert isinstance(trakt_col, Ok)
    trakt_summary = describe_recipe(trakt_col.value, lib)
    assert trakt_summary.required_secrets == ("SECRET_TRAKT_TOKEN_ID",)

    resolver = AddonResolver(addons={"aiometadata": AddonInfo(name="aiometadata", addon_id="aio-metadata")})
    items = [
        {"ref": "col-trakt-personal"},
        {"ref": "col-kdrama-global"},
        {"ref": "col-studios-auteurs"},
        {
            "ref": "col-genres-moods",
            "with": {"genres": ["fld-genre-horror", "fld-genre-drama", "fld-genre-family", "fld-genre-war-history", "fld-genre-standup"]},
        },
        {
            "ref": "col-streaming-services",
            "with": {"services": ["fld-netflix", "fld-crunchyroll", "fld-mubi", "fld-shudder", "fld-bbc"]},
        },
        {
            "ref": "col-anime-japanese",
            "with": {"tabs": ["fld-anime-upcoming-favorites", "fld-anime-golden-era"]},
        },
    ]
    comp = compile_collections(items, lib, resolver)
    assert isinstance(comp, Ok), comp

    base_meta = json.loads((REPO / "examples" / "home" / "aiometadata.json").read_text(encoding="utf-8"))
    derived = derive_catalogs(base_meta["catalogs"], comp.value.catalog_uses)
    assert isinstance(derived, Ok), derived

    by_id_type = {(c["id"], c["type"]): c for c in derived.value if c["enabled"]}
    assert ("tmdb.discover.horror_movies", "movie") in by_id_type
    assert ("tmdb.discover.a24_movies", "movie") in by_id_type
    assert ("tmdb.discover.kdrama_series", "series") in by_id_type
    assert ("tmdb.discover.crunchyroll_series", "series") in by_id_type
    assert ("trakt.watchlist.movies", "movie") in by_id_type
    # MAL decade & upcoming catalogs updated in-place with displayType="series" (no duplicate entries)
    assert by_id_type[("mal.90sDecade", "anime")]["displayType"] == "series"
    assert sum(1 for c in derived.value if c["id"] == "mal.90sDecade") == 1


def test_settings_and_meta_recipe_params_zero_drift_and_every_option_normalizes() -> None:
    from stream_weaver.core.composition import compile_doc
    from stream_weaver.core.nuvio_meta import normalize_nuvio_meta_doc
    from stream_weaver.core.nuvio_settings import normalize_nuvio_settings_doc

    lib = Library.load((EXAMPLE_LIB, SHARED_LIB))
    assert not lib.errors, lib.errors

    default_settings = compile_doc({"ref": "default"}, "settings", lib)
    assert isinstance(default_settings, Ok), default_settings
    assert default_settings.value is not None
    assert default_settings.value["appearance"]["theme"] == "ocean"
    assert default_settings.value["appearance"]["amoledBlack"] is True
    assert default_settings.value["playback"]["streamSelection"]["mode"] == "manual"
    assert default_settings.value["audio"]["preferredLanguage"] == "original"
    assert default_settings.value["subtitles"]["preferredLanguage"] == "en"
    assert default_settings.value["subtitles"]["secondaryLanguage"] is None
    assert default_settings.value["tvLayout"]["homeLayout"] == "modern"
    assert default_settings.value["tvLayout"]["details"]["blurUnwatchedEpisodes"] is False
    assert isinstance(normalize_nuvio_settings_doc(default_settings.value), Ok)

    default_meta = compile_doc({"ref": "default"}, "meta", lib)
    assert isinstance(default_meta, Ok), default_meta
    assert default_meta.value is not None
    assert default_meta.value["episodeRatings"] == "show"
    assert default_meta.value["tracking"]["watchProgressSource"] == "trakt"
    assert default_meta.value["tracking"]["librarySource"] == "trakt"
    assert default_meta.value["metaScreen"]["blurUnwatchedEpisodes"] is False
    assert isinstance(normalize_nuvio_meta_doc(default_meta.value), Ok)

    # Every advertised option in `settings/default` normalizes cleanly
    settings_recipe = lib.get("settings", "default")
    assert isinstance(settings_recipe, Ok)
    for spec in settings_recipe.value.params.params:
        for opt in spec.options:
            rendered = compile_doc({"ref": "default", "with": {spec.name: opt.value}}, "settings", lib)
            assert isinstance(rendered, Ok), (spec.name, opt.value, rendered)
            norm = normalize_nuvio_settings_doc(rendered.value)
            assert isinstance(norm, Ok), (spec.name, opt.value, norm)
        if spec.type == "bool":
            for bval in (True, False):
                rendered = compile_doc({"ref": "default", "with": {spec.name: bval}}, "settings", lib)
                assert isinstance(rendered, Ok), (spec.name, bval, rendered)
                norm = normalize_nuvio_settings_doc(rendered.value)
                assert isinstance(norm, Ok), (spec.name, bval, norm)

    # Every advertised option in `meta/default` normalizes cleanly
    meta_recipe = lib.get("meta", "default")
    assert isinstance(meta_recipe, Ok)
    for spec in meta_recipe.value.params.params:
        for opt in spec.options:
            rendered = compile_doc({"ref": "default", "with": {spec.name: opt.value}}, "meta", lib)
            assert isinstance(rendered, Ok), (spec.name, opt.value, rendered)
            norm = normalize_nuvio_meta_doc(rendered.value)
            assert isinstance(norm, Ok), (spec.name, opt.value, norm)
        if spec.type == "bool":
            for bval in (True, False):
                rendered = compile_doc({"ref": "default", "with": {spec.name: bval}}, "meta", lib)
                assert isinstance(rendered, Ok), (spec.name, bval, rendered)
                norm = normalize_nuvio_meta_doc(rendered.value)
                assert isinstance(norm, Ok), (spec.name, bval, norm)
