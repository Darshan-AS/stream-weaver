"""
Migration: lift a tenant's raw Nuvio documents into library recipes + profile refs, 1:1.

`extract_composition` is pure and *lossless by construction*: every folder becomes a folder
recipe, every collection a collection recipe referencing its folders, every `tmdb.discover.*`
catalog definition a catalog recipe, and the badges / meta / settings documents become
whole-document recipes. The profile ends up as pure refs and the caller verifies that compiling
those refs reproduces the original documents byte-for-byte before anything is written.

Recipes that depend on a *custom* AIOStreams preset (e.g. a private static addon) are routed to
the tenant-local library; everything else goes to the shared library.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stream_weaver.core.composition import AddonResolver, config_catalog_index, symbolic_source
from stream_weaver.core.library import KIND_DIRS, Recipe
from stream_weaver.core.profiles import ExtraCatalogSpec
from stream_weaver.core.template import ParamSchema
from stream_weaver.core.types import JsonDict, is_json_list, is_json_mapping

_FLAG_KEYS = ("enabled", "showInHome")


@dataclass(frozen=True, slots=True)
class ExtractOutcome:
    shared: tuple[Recipe, ...]
    tenant_private: tuple[Recipe, ...]
    collections: tuple[JsonDict, ...]  # profile `collections` (all refs)
    badges: JsonDict | None
    meta: JsonDict | None
    settings: JsonDict | None
    extra_catalogs: tuple[ExtraCatalogSpec, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def _recipe(kind: str, rid: str, body: Any, root: Path) -> Recipe:
    return Recipe(kind=kind, id=rid, params=ParamSchema(), body=body, source=root / KIND_DIRS[kind] / f"{rid}.json")


def _definition_of(entry: Mapping[str, Any]) -> JsonDict:
    return {k: copy.deepcopy(v) for k, v in entry.items() if k not in _FLAG_KEYS}


def extract_composition(
    *,
    collections: Sequence[Mapping[str, Any]],
    badges: Mapping[str, Any] | None,
    meta: Mapping[str, Any] | None,
    settings: Mapping[str, Any] | None,
    aiometadata_catalogs: Sequence[Mapping[str, Any]],
    resolver: AddonResolver,
    custom_presets: frozenset[str],
    shared_root: Path,
    tenant_root: Path,
    doc_recipe_ids: Mapping[str, str] | None = None,
) -> ExtractOutcome:
    ids = {"badges": "default", "meta": "default", "settings": "default", **(doc_recipe_ids or {})}
    shared: list[Recipe] = []
    private: list[Recipe] = []
    notes: list[str] = []

    # Library catalog recipes: every tmdb.discover.* entry carries its own definition.
    defined: dict[tuple[str, str], Mapping[str, Any]] = {}
    for entry in aiometadata_catalogs:
        if not is_json_mapping(entry):
            continue
        cid = str(entry.get("id") or "")
        if cid.startswith("tmdb.discover."):
            defined[(cid, str(entry.get("type")))] = entry
    emitted_catalogs: set[str] = set()

    referenced: set[tuple[str, str]] = set()
    profile_collections: list[JsonDict] = []
    for col in collections:
        if not is_json_mapping(col) or not col.get("id"):
            continue
        col_private = False
        folder_refs: list[JsonDict] = []
        for folder in col.get("folders", []) or []:
            if not is_json_mapping(folder) or not folder.get("id"):
                continue
            fid = str(folder["id"])
            body = copy.deepcopy(dict(folder))
            f_private = False
            sources: list[JsonDict] = []
            for raw in folder.get("sources", []) or []:
                if not is_json_mapping(raw):
                    continue
                sym = symbolic_source(raw, resolver)
                if sym.get("addon") == "aiometadata":
                    key = (str(sym.get("catalogId")), str(sym.get("type")))
                    referenced.add(key)
                    if key in defined:
                        cid = key[0]
                        if cid not in emitted_catalogs:
                            shared.append(_recipe("catalog", cid, _definition_of(defined[key]), shared_root))
                            emitted_catalogs.add(cid)
                        sym.pop("catalogId", None)
                        sym["catalog"] = {"ref": cid}
                if sym.get("addon") == "aiostreams" and str(sym.get("preset") or "") in custom_presets:
                    f_private = True
                sources.append(sym)
            body["sources"] = sources
            (private if f_private else shared).append(_recipe("folder", fid, body, tenant_root if f_private else shared_root))
            col_private = col_private or f_private
            folder_refs.append({"ref": fid})
        cid_col = str(col["id"])
        col_body = copy.deepcopy(dict(col))
        col_body["folders"] = folder_refs
        (private if col_private else shared).append(
            _recipe("collection", cid_col, col_body, tenant_root if col_private else shared_root)
        )
        if col_private:
            notes.append(f"collection `{cid_col}` depends on a custom preset → tenant library")
        profile_collections.append({"ref": cid_col})

    def _doc(kind: str, doc: Mapping[str, Any] | None) -> JsonDict | None:
        if doc is None:
            return None
        shared.append(_recipe(kind, ids[kind], copy.deepcopy(dict(doc)), shared_root))
        return {"ref": ids[kind]}

    badges_ref = _doc("badges", badges)
    meta_ref = _doc("meta", meta)
    settings_ref = _doc("settings", settings)

    extras: list[ExtraCatalogSpec] = []
    entries = [e for e in aiometadata_catalogs if is_json_mapping(e)]
    index = config_catalog_index(entries)
    referenced_positions = {index[key] for key in referenced if key in index}
    for pos, entry in enumerate(entries):
        if pos in referenced_positions:
            continue
        if bool(entry.get("enabled", False)) or bool(entry.get("showInHome", False)):
            # Record the extra under the manifest-side key so profiles speak one id language.
            key = (str(entry.get("id")), str(entry.get("type")))
            extras.append(
                ExtraCatalogSpec(
                    catalog_id=key[0],
                    type=key[1],
                    enabled=bool(entry.get("enabled", False)),
                    show_in_home=bool(entry.get("showInHome", False)),
                )
            )
    if extras:
        notes.append(f"{len(extras)} enabled catalog(s) not used by any collection kept via aiometadata.extraCatalogs")

    return ExtractOutcome(
        shared=tuple(shared),
        tenant_private=tuple(private),
        collections=tuple(profile_collections),
        badges=badges_ref,
        meta=meta_ref,
        settings=settings_ref,
        extra_catalogs=tuple(extras),
        notes=tuple(notes),
    )


def custom_preset_ids(aiostreams_doc: Mapping[str, Any]) -> frozenset[str]:
    """Instance ids of `type: custom` presets in an AIOStreams document (private addon wiring)."""
    out: set[str] = set()
    presets = aiostreams_doc.get("presets")
    if is_json_list(presets):
        for p in presets:
            if is_json_mapping(p) and p.get("type") == "custom" and isinstance(p.get("instanceId"), str):
                out.add(p["instanceId"])
    return frozenset(out)
