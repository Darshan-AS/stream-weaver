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
