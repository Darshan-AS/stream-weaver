"""Tests for the composition layer: template engine, library, compile/eject, derived catalogs, migration golden."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from stream_weaver.cli.app import _compile_all_result, _try_context
from stream_weaver.core.composition import (
    AddonInfo,
    AddonResolver,
    CatalogUse,
    ExtraCatalog,
    aiostreams_manifest_id,
    compile_collections,
    compile_doc,
    compile_source,
    config_catalog_keys,
    derive_catalogs,
    eject_collections,
    eject_doc,
    symbolic_source,
)
from stream_weaver.core.library import Library, Recipe, parse_recipe, recipe_to_doc
from stream_weaver.core.result import Err, Ok
from stream_weaver.core.template import (
    ParamSchema,
    bind_params,
    expand,
    parse_param_schema,
    referenced_variables,
)
from stream_weaver.core.tenant import TenantPaths

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "home"

# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------


def test_template_substitution_loops_and_conditionals() -> None:
    schema = parse_param_schema(
        {
            "label": {"type": "string"},
            "decades": {"type": "list", "default": []},
            "series": {"type": "bool", "default": True},
            "min": {"type": "int", "default": 5},
        }
    )
    assert isinstance(schema, Ok)
    bound = bind_params(schema.value, {"label": "Kannada", "decades": ["80s", "90s"]})
    assert isinstance(bound, Ok)
    body = {
        "title": "${label} Movies",
        "min": "${min}",
        "rows": [
            {
                "for": "d",
                "in": "${decades}",
                "item": {"title": "${label} — ${d}", "decade": "${d}"},
            },
            {"if": "${series}", "item": {"title": "${label} Series"}},
        ],
    }
    out = expand(body, bound.value)
    assert isinstance(out, Ok)
    assert out.value == {
        "title": "Kannada Movies",
        "min": 5,  # exact `${min}` keeps the int
        "rows": [
            {"title": "Kannada — 80s", "decade": "80s"},
            {"title": "Kannada — 90s", "decade": "90s"},
            {"title": "Kannada Series"},
        ],
    }
    assert referenced_variables(body) == {"label", "min", "decades", "d", "series"}


def test_template_errors_are_precise() -> None:
    schema = parse_param_schema({"x": {"type": "string"}})
    assert isinstance(schema, Ok)
    assert isinstance(bind_params(schema.value, {}), Err)  # missing required
    assert isinstance(bind_params(schema.value, {"x": "a", "y": 1}), Err)  # unknown
    assert isinstance(bind_params(schema.value, {"x": 3}), Err)  # wrong type
    assert isinstance(expand({"a": "${nope}"}, {}), Err)
    assert isinstance(expand([{"for": "i", "in": "${x}", "item": 1}], {"x": "not-a-list"}), Err)
    assert isinstance(expand([{"if": "${x}", "item": 1}], {"x": "yes"}), Err)
    assert isinstance(parse_param_schema({"x": {"type": "float"}}), Err)


@given(st.text(alphabet=st.characters(blacklist_characters="$"), max_size=40))
def test_param_free_bodies_are_fixed_points(text: str) -> None:
    body = {"a": text, "b": [text, {"c": text}]}
    assert expand(body, {}) == Ok(body)


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------


def _write_recipe(
    root: Path, kind: str, rid: str, body: Any, params: dict[str, Any] | None = None
) -> None:
    sub = {
        "collection": "collections",
        "folder": "folders",
        "catalog": "catalogs",
        "badges": "badges",
        "meta": "meta",
        "settings": "settings",
    }[kind]
    doc: dict[str, Any] = {"kind": kind, "id": rid, kind: body}
    if params:
        doc["params"] = params
    (root / sub).mkdir(parents=True, exist_ok=True)
    (root / sub / f"{rid}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_library_precedence_and_rendering(tmp_path: Path) -> None:
    shared, local = tmp_path / "shared", tmp_path / "local"
    _write_recipe(shared, "folder", "f", {"id": "f", "title": "shared"})
    _write_recipe(local, "folder", "f", {"id": "f", "title": "local"})
    _write_recipe(
        shared, "folder", "g", {"id": "g", "title": "${label}"}, {"label": {"type": "string"}}
    )
    lib = Library.load([local, shared])
    assert lib.errors == ()
    assert lib.render("folder", "f") == Ok({"id": "f", "title": "local"})
    assert lib.root_of("folder", "f") == local
    assert lib.render("folder", "g", {"label": "G!"}) == Ok({"id": "g", "title": "G!"})
    assert isinstance(lib.render("folder", "g"), Err)  # missing param
    assert isinstance(lib.get("folder", "zzz"), Err)
    assert lib.ids("folder") == ("f", "g")


def test_library_rejects_bad_recipes(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    (root / "folders").mkdir(parents=True)
    (root / "folders" / "a.json").write_text(
        json.dumps({"kind": "folder", "id": "b", "folder": {}})
    )  # id ≠ file
    (root / "folders" / "c.json").write_text(
        json.dumps({"kind": "folder", "id": "c", "folder": {"t": "${undeclared}"}})
    )
    (root / "folders" / "d.json").write_text("not json")
    lib = Library.load([root])
    assert len(lib.errors) == 3
    assert lib.recipes == {}


def test_recipe_doc_round_trip() -> None:
    doc = {
        "kind": "catalog",
        "id": "x",
        "params": {"lang": {"type": "string", "default": "kn"}},
        "catalog": {"id": "tmdb.discover.x_${lang}"},
    }
    parsed = parse_recipe(doc, source=Path("/lib/catalogs/x.json"))
    assert isinstance(parsed, Ok)
    again = recipe_to_doc(parsed.value)
    assert again["params"] == doc["params"]
    assert again["catalog"] == doc["catalog"]
    assert isinstance(parse_recipe(again, source=Path("/lib/catalogs/x.json")), Ok)


# ---------------------------------------------------------------------------
# sources / resolver
# ---------------------------------------------------------------------------

RESOLVER = AddonResolver(
    addons={
        "aiometadata": AddonInfo("aiometadata", "aio-metadata"),
        "aiostreams": AddonInfo(
            "aiostreams",
            "s.example.com.00000000-000",
            catalogs=(
                {"id": "itoe3b0.itsout.released", "type": "movie"},
                {"id": "lib791b.aiostreams::library.torbox", "type": "movie"},
            ),
            presets=frozenset({"ito", "lib"}),
        ),
        "bingecat": AddonInfo(
            "bingecat",
            "com.aicat.{{SECRET_BINGECAT_TOKEN}}",
            catalogs=({"id": "aicat_list_1", "type": "movie", "name": "AI Recommendations"},),
        ),
    }
)


def test_aiostreams_manifest_id() -> None:
    assert (
        aiostreams_manifest_id("https://s.example.com", "00000000-0000-4000-8000-000000000001")
        == "s.example.com.00000000-000"
    )


def test_compile_source_forms_and_inverse() -> None:
    literal = compile_source(
        {"addon": "aiometadata", "catalogId": "tmdb.top", "type": "movie", "title": "T"},
        RESOLVER,
        context="t",
    )
    assert literal == Ok(
        {
            "provider": "addon",
            "addonId": "aio-metadata",
            "catalogId": "tmdb.top",
            "type": "movie",
            "title": "T",
            "genre": "None",
        }
    )
    preset = compile_source(
        {"addon": "aiostreams", "preset": "ito", "catalog": "itsout.released", "type": "movie"},
        RESOLVER,
        context="t",
    )
    assert isinstance(preset, Ok)
    assert preset.value["catalogId"] == "itoe3b0.itsout.released"
    matched = compile_source(
        {"addon": "bingecat", "match": {"name": "ai recommendations"}, "type": "movie"},
        RESOLVER,
        context="t",
    )
    assert isinstance(matched, Ok)
    assert matched.value["catalogId"] == "aicat_list_1"
    # inverse
    assert symbolic_source(preset.value, RESOLVER) == {
        "addon": "aiostreams",
        "type": "movie",
        "preset": "ito",
        "catalog": "itsout.released",
    }
    assert symbolic_source(matched.value, RESOLVER) == {
        "addon": "bingecat",
        "type": "movie",
        "match": {"name": "AI Recommendations", "type": "movie"},
    }
    assert symbolic_source(literal.value, RESOLVER) == {
        "addon": "aiometadata",
        "type": "movie",
        "catalogId": "tmdb.top",
        "title": "T",
    }
    # errors
    assert isinstance(
        compile_source(
            {"addon": "ghost", "catalogId": "x", "type": "movie"}, RESOLVER, context="t"
        ),
        Err,
    )
    assert isinstance(
        compile_source(
            {"addon": "aiostreams", "preset": "swc", "catalog": "x", "type": "series"},
            RESOLVER,
            context="t",
        ),
        Err,
    )
    assert isinstance(
        compile_source(
            {"addon": "bingecat", "match": {"name": "nope"}, "type": "movie"}, RESOLVER, context="t"
        ),
        Err,
    )
    raw = {"provider": "addon", "addonId": "weird", "catalogId": "c", "type": "movie"}
    assert compile_source(raw, RESOLVER, context="t") == Ok(raw)  # raw pass-through


# ---------------------------------------------------------------------------
# collections: compile + eject round trip
# ---------------------------------------------------------------------------


def _lib(tmp_path: Path) -> Library:
    root = tmp_path / "lib"
    _write_recipe(
        root,
        "catalog",
        "tmdb.discover.kn",
        {
            "id": "tmdb.discover.kn",
            "type": "movie",
            "name": "KN",
            "metadata": {"discover": {"params": {"with_original_language": "kn"}}},
        },
    )
    _write_recipe(
        root,
        "folder",
        "regional",
        {
            "id": "fld-${lang}",
            "title": "${label}",
            "sources": [
                {
                    "addon": "aiometadata",
                    "type": "movie",
                    "catalog": {"ref": "tmdb.discover.kn"},
                    "title": "${label} Movies",
                },
                {
                    "addon": "aiometadata",
                    "type": "series",
                    "catalogId": "tmdb.top",
                    "title": "${label} Series",
                },
            ],
        },
        {"lang": {"type": "string"}, "label": {"type": "string"}},
    )
    _write_recipe(
        root,
        "collection",
        "regional-col",
        {
            "id": "col-regional",
            "title": "Regional",
            "folders": [{"ref": "regional", "with": {"lang": "kn", "label": "Kannada"}}],
        },
    )
    _write_recipe(root, "badges", "elite", {"platforms": ["tv"], "imports": [{"id": "x"}]})
    lib = Library.load([root])
    assert lib.errors == ()
    return lib


def test_compile_collections_expands_refs_and_tracks_catalog_uses(tmp_path: Path) -> None:
    lib = _lib(tmp_path)
    items = [
        {"ref": "regional-col"},
        {
            "id": "col-inline",
            "title": "Inline",
            "folders": [
                {
                    "id": "f-in",
                    "title": "In",
                    "sources": [
                        {
                            "addon": "bingecat",
                            "match": {"name": "AI Recommendations"},
                            "type": "movie",
                        }
                    ],
                }
            ],
        },
    ]
    res = compile_collections(items, lib, RESOLVER)
    assert isinstance(res, Ok), res
    cols = res.value.collections
    assert [c["id"] for c in cols] == ["col-regional", "col-inline"]
    folder = cols[0]["folders"][0]
    assert folder["id"] == "fld-kn"
    assert folder["sources"][0]["catalogId"] == "tmdb.discover.kn"
    assert folder["sources"][0]["addonId"] == "aio-metadata"
    assert cols[1]["folders"][0]["sources"][0]["catalogId"] == "aicat_list_1"
    uses = {(u.catalog_id, u.type): u for u in res.value.catalog_uses}
    assert set(uses) == {("tmdb.discover.kn", "movie"), ("tmdb.top", "series")}
    assert uses[("tmdb.discover.kn", "movie")].definition is not None
    assert res.value.provenance["fld-kn"] == {
        "ref": "regional",
        "with": {"lang": "kn", "label": "Kannada"},
    }
    assert isinstance(
        compile_collections([{"ref": "regional-col"}, {"ref": "regional-col"}], lib, RESOLVER), Err
    )  # dup ids


def test_eject_keeps_refs_when_equal_and_inlines_changes(tmp_path: Path) -> None:
    lib = _lib(tmp_path)
    items: list[Any] = [{"ref": "regional-col"}]
    compiled = compile_collections(items, lib, RESOLVER)
    assert isinstance(compiled, Ok)
    remote = [json.loads(json.dumps(c)) for c in compiled.value.collections]

    # 1. unchanged → ref survives
    assert eject_collections(remote, items, compiled.value, lib, RESOLVER) == items

    # 2. collection retitled on the device, folder untouched → collection inlined, folder stays a ref
    remote[0]["title"] = "Regional (renamed)"
    ejected = eject_collections(remote, items, compiled.value, lib, RESOLVER)
    assert ejected[0]["title"] == "Regional (renamed)"
    assert ejected[0]["folders"] == [
        {"ref": "regional", "with": {"lang": "kn", "label": "Kannada"}}
    ]

    # 3. a new collection added on the device → inlined with symbolic sources
    remote.append(
        {
            "id": "col-new",
            "title": "New",
            "folders": [
                {
                    "id": "f-new",
                    "title": "N",
                    "sources": [
                        {
                            "provider": "addon",
                            "addonId": "aio-metadata",
                            "catalogId": "tmdb.top",
                            "type": "movie",
                            "genre": "None",
                        }
                    ],
                }
            ],
        }
    )
    ejected = eject_collections(remote, items, compiled.value, lib, RESOLVER)
    assert ejected[1]["folders"][0]["sources"] == [
        {"addon": "aiometadata", "type": "movie", "catalogId": "tmdb.top"}
    ]

    # 4. and the ejected composition compiles back to exactly the remote state (lossless)
    recompiled = compile_collections(ejected, lib, RESOLVER)
    assert isinstance(recompiled, Ok)
    assert list(recompiled.value.collections) == remote


def test_doc_refs_compile_and_eject(tmp_path: Path) -> None:
    lib = _lib(tmp_path)
    assert compile_doc({"ref": "elite"}, "badges", lib) == Ok(
        {"platforms": ["tv"], "imports": [{"id": "x"}]}
    )
    assert compile_doc(None, "badges", lib) == Ok(None)
    assert eject_doc(
        {"version": "1.0.0", "platforms": ["tv"], "imports": [{"id": "x"}]},
        {"ref": "elite"},
        "badges",
        lib,
    ) == {"ref": "elite"}
    changed = eject_doc(
        {"platforms": ["tv", "mobile"], "imports": []}, {"ref": "elite"}, "badges", lib
    )
    assert changed == {"platforms": ["tv", "mobile"], "imports": []}


# ---------------------------------------------------------------------------
# derived catalogs
# ---------------------------------------------------------------------------


def test_config_catalog_keys_cover_aiometadata_id_quirks() -> None:
    assert config_catalog_keys(
        {"id": "streaming.nfx", "type": "movie", "displayType": "movie"}
    ) == (("streaming.nfx", "movie"), ("streaming.nfx_movie", "movie"))
    assert config_catalog_keys({"id": "mal.airing", "type": "anime", "displayType": "series"}) == (
        ("mal.airing", "anime"),
        ("mal.airing_anime", "series"),
    )
    assert config_catalog_keys({"id": "tmdb.top", "type": "series"}) == (
        ("tmdb.top", "series"),
        ("tmdb.top_series", "series"),
    )


def test_derive_catalogs_enables_referenced_keeps_extras_disables_rest_and_appends_library() -> (
    None
):
    instance = [
        {"id": "tmdb.top", "type": "movie", "enabled": True, "showInHome": True},
        {
            "id": "streaming.nfx",
            "type": "movie",
            "displayType": "movie",
            "enabled": False,
            "showInHome": False,
        },
        {
            "id": "mal.airing",
            "type": "anime",
            "displayType": "series",
            "enabled": True,
            "showInHome": True,
        },
        {"id": "trakt.calendar", "type": "series", "enabled": True, "showInHome": False},
        {
            "id": "tmdb.discover.old",
            "type": "movie",
            "enabled": True,
            "showInHome": True,
            "name": "Old",
        },
    ]
    uses = [
        CatalogUse("streaming.nfx_movie", "movie"),
        CatalogUse(
            "tmdb.discover.old",
            "movie",
            definition={"id": "tmdb.discover.old", "type": "movie", "name": "Renamed"},
        ),
        CatalogUse(
            "tmdb.discover.new",
            "movie",
            definition={"id": "tmdb.discover.new", "type": "movie", "name": "New"},
        ),
    ]
    extras = [ExtraCatalog("trakt.calendar", "series", enabled=True, show_in_home=False)]
    res = derive_catalogs(instance, uses, extras)
    assert isinstance(res, Ok)
    by = {(c["id"], c["type"]): c for c in res.value}
    assert (by[("tmdb.top", "movie")]["enabled"], by[("tmdb.top", "movie")]["showInHome"]) == (
        False,
        False,
    )
    assert (
        by[("streaming.nfx", "movie")]["enabled"],
        by[("streaming.nfx", "movie")]["showInHome"],
    ) == (True, True)
    assert by[("mal.airing", "anime")]["enabled"] is False
    assert (
        by[("trakt.calendar", "series")]["enabled"],
        by[("trakt.calendar", "series")]["showInHome"],
    ) == (True, False)
    assert by[("tmdb.discover.old", "movie")]["name"] == "Renamed"
    assert by[("tmdb.discover.new", "movie")] == {
        "id": "tmdb.discover.new",
        "type": "movie",
        "name": "New",
        "enabled": True,
        "showInHome": True,
    }
    assert [c["id"] for c in res.value][:5] == [
        c["id"] for c in instance
    ]  # order preserved, new appended
    assert isinstance(derive_catalogs(instance, [CatalogUse("builtin.missing", "movie")]), Err)


# ---------------------------------------------------------------------------
# golden: the example tenant compiles and its derived state is a fixed point
# ---------------------------------------------------------------------------


def test_example_tenant_compiles_and_is_a_fixed_point() -> None:
    ctx = _try_context(TenantPaths(EXAMPLE))
    assert isinstance(ctx, Ok), ctx
    compiled = _compile_all_result(ctx.value)
    assert isinstance(compiled, Ok), compiled
    (cp,) = compiled.value
    assert cp.collections is not None
    assert len(cp.collections_doc) >= 4
    # every source resolved to a concrete addon id (no symbolic names leak into Nuvio documents)
    for col in cp.collections_doc:
        for folder in col["folders"]:
            for src in folder["sources"]:
                assert "addon" not in src
                assert src["addonId"]
    assert cp.badges is not None
    assert cp.settings is not None
    assert cp.resolved.profile.extra_catalogs  # Stremio-board extras survived extraction


def test_recipe_dataclass_is_frozen_value() -> None:
    r = Recipe(
        kind="folder",
        id="x",
        params=ParamSchema(),
        body={"id": "x"},
        source=Path("/l/folders/x.json"),
    )
    assert not r.is_parameterised
    assert recipe_to_doc(r)["folder"] == {"id": "x"}
