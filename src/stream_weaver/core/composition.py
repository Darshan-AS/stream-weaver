"""
Composition — compiles a profile's *taste* (recipe refs + inline fragments) into the raw Nuvio /
AIOMetadata documents the adapters push, and ejects server-side edits back into the profile.

Three ideas, all pure:

* **ref-or-inline** — every composable list item is either `{"ref": id, "with": {...}}` or the
  full inline object. Compile renders refs through the `Library`; eject keeps a ref when the
  server still equals its rendering and replaces it with the inline object otherwise.
* **symbolic addons** — sources name addons by role (`aiometadata`, `aiostreams`, `bingecat`,
  any `addons.extra[].name`), never by manifest id. An `AddonResolver` built from the profile's
  bindings turns them into real ids; account-minted catalog ids are found by `match`, AIOStreams
  catalogs by `preset` + `catalog`.
* **derived catalogs** — an AIOMetadata instance enables exactly the catalogs its profiles'
  collections reference (plus explicit extras); library catalog recipes are materialised.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.library import Library
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict, is_json_list, is_json_mapping

AIOMETADATA_MANIFEST_ID = "aio-metadata"
_AIOSTREAMS_CATALOG_RE = re.compile(r"^([a-z0-9]{3})([0-9a-f]{4})\.(.+)$")


# ---------------------------------------------------------------------------
# Addon resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AddonInfo:
    """What the compiler knows about one symbolic addon of a profile."""

    name: str  # symbolic (aiometadata | aiostreams | extra name)
    addon_id: str  # manifest id as Nuvio expects it in `addonId`
    catalogs: tuple[Mapping[str, Any], ...] = ()  # cached manifest catalogs ({id,type,name,...})
    presets: frozenset[str] = frozenset()  # AIOStreams: preset instance ids present (e.g. {"ito","lib"})


@dataclass(frozen=True, slots=True)
class AddonResolver:
    addons: Mapping[str, AddonInfo]

    def get(self, name: str) -> AddonInfo | None:
        return self.addons.get(name)

    def by_addon_id(self, addon_id: str) -> AddonInfo | None:
        for info in self.addons.values():
            if info.addon_id == addon_id:
                return info
        return None


def aiostreams_manifest_id(base_url: str, uuid: str) -> str:
    """AIOStreams names its manifest `<host>.<first 12 chars of the user UUID>`."""
    host = urlsplit(base_url).netloc or base_url
    return f"{host}.{uuid[:12]}" if uuid else host


def _err(msg: str) -> Err[ConfigValidationError]:
    return Err(ConfigValidationError(msg))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

_SOURCE_PASSTHROUGH = ("title", "genre", "provider")


def compile_source(src: Mapping[str, Any], resolver: AddonResolver, *, context: str) -> Result[JsonDict, ConfigValidationError]:
    """Turns a symbolic source into Nuvio's `{provider, addonId, catalogId, type, genre, title}`."""
    if "addonId" in src and "addon" not in src:
        # Legacy / already-raw source: pass through untouched.
        return Ok(dict(src))
    name = src.get("addon")
    if not isinstance(name, str) or not name:
        return _err(f"{context}: source needs `addon` (symbolic name) or a raw `addonId`")
    info = resolver.get(name)
    if info is None:
        return _err(
            f"{context}: profile has no addon named `{name}` (instances: aiometadata, aiostreams; extras: addons.extra[].name)"
        )
    ctype = src.get("type")
    if not isinstance(ctype, str) or not ctype:
        return _err(f"{context}: source needs `type` (movie | series | anime | …)")

    catalog_id: str
    if "catalogId" in src:
        catalog_id = str(src["catalogId"])
    elif "catalog" in src and is_json_mapping(src["catalog"]) and "id" in src["catalog"]:
        catalog_id = str(src["catalog"]["id"])  # rendered catalog recipe (already expanded by caller)
    elif "preset" in src and "catalog" in src:
        preset, cat = str(src["preset"]), str(src["catalog"])
        if info.catalogs:
            hits = [
                str(c.get("id"))
                for c in info.catalogs
                if (m := _AIOSTREAMS_CATALOG_RE.match(str(c.get("id") or "")))
                and m.group(1) == preset
                and m.group(3) == cat
                and str(c.get("type")) == ctype
            ]
            if not hits:
                return _err(
                    f"{context}: addon `{name}` has no catalog for preset `{preset}` / `{cat}` ({ctype}); "
                    f"is the preset installed on that instance?"
                )
            catalog_id = hits[0]
        else:
            return _err(
                f"{context}: cannot resolve preset `{preset}` on `{name}` — no cached manifest yet; "
                "run `sw pull aiomanager` after the instance has synced once"
            )
    elif "match" in src and is_json_mapping(src["match"]):
        want_name = str(src["match"].get("name") or "").strip().lower()
        want_type = str(src["match"].get("type") or ctype)
        if not info.catalogs:
            return _err(f"{context}: cannot `match` on `{name}` — no cached manifest yet; run `sw pull aiomanager`")
        hits = [
            str(c.get("id"))
            for c in info.catalogs
            if str(c.get("name") or "").strip().lower() == want_name and str(c.get("type")) == want_type
        ]
        if not hits:
            return _err(f"{context}: addon `{name}` has no catalog named {want_name!r} of type {want_type}")
        catalog_id = hits[0]
    else:
        return _err(f"{context}: source needs one of `catalogId`, `catalog` (recipe), `preset`+`catalog`, or `match`")

    out: JsonDict = {"provider": src.get("provider", "addon"), "addonId": info.addon_id, "catalogId": catalog_id, "type": ctype}
    for k in _SOURCE_PASSTHROUGH:
        if k in src and k != "provider":
            out[k] = src[k]
    if "genre" not in out:
        out["genre"] = "None"
    return Ok(out)


def symbolic_source(raw: Mapping[str, Any], resolver: AddonResolver) -> JsonDict:
    """Inverse of `compile_source` as far as the resolver allows (used when ejecting server edits)."""
    addon_id = str(raw.get("addonId") or "")
    info = resolver.by_addon_id(addon_id)
    if info is None:
        return dict(raw)
    out: JsonDict = {"addon": info.name, "type": raw.get("type")}
    catalog_id = str(raw.get("catalogId") or "")
    m = _AIOSTREAMS_CATALOG_RE.match(catalog_id)
    if info.name == "aiostreams" and m:
        out["preset"], out["catalog"] = m.group(1), m.group(3)
    elif info.name not in ("aiometadata", "aiostreams") and info.catalogs:
        named = next(
            (c for c in info.catalogs if str(c.get("id")) == catalog_id and str(c.get("type")) == str(raw.get("type"))),
            None,
        )
        if named is not None and named.get("name"):
            out["match"] = {"name": str(named["name"]), "type": str(raw.get("type"))}
        else:
            out["catalogId"] = catalog_id
    else:
        out["catalogId"] = catalog_id
    for k in _SOURCE_PASSTHROUGH:
        if k in raw and not (k == "provider" and raw[k] == "addon") and not (k == "genre" and raw[k] == "None"):
            out[k] = raw[k]
    return out


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalogUse:
    """One AIOMetadata catalog a compiled collection depends on (for derived instance catalogs)."""

    catalog_id: str
    type: str
    definition: Mapping[str, Any] | None = None  # rendered `catalog` recipe body, if any


@dataclass(frozen=True, slots=True)
class CompiledCollections:
    collections: tuple[JsonDict, ...]
    catalog_uses: tuple[CatalogUse, ...]
    provenance: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """folder id → `{"ref", "with"}` for folders that came from a recipe (used by eject)."""


def _is_ref(item: Any) -> bool:
    return is_json_mapping(item) and "ref" in item and set(item) <= {"ref", "with"}


def _render(library: Library, kind: str, item: Mapping[str, Any], *, context: str) -> Result[Any, ConfigValidationError]:
    rid = item["ref"]
    if not isinstance(rid, str):
        return _err(f"{context}: `ref` must be a string")
    params = item.get("with")
    if params is not None and not is_json_mapping(params):
        return _err(f"{context}: `with` must be an object")
    return library.render(kind, rid, params)


def _compile_folder(
    folder: Mapping[str, Any],
    library: Library,
    resolver: AddonResolver,
    *,
    context: str,
    uses: list[CatalogUse],
) -> Result[JsonDict, ConfigValidationError]:
    out = copy.deepcopy(dict(folder))
    sources = folder.get("sources", [])
    if not is_json_list(sources):
        return _err(f"{context}: `sources` must be a list")
    compiled_sources: list[JsonDict] = []
    for i, src in enumerate(sources):
        if not is_json_mapping(src):
            return _err(f"{context}.sources[{i}]: must be an object")
        src_ctx = f"{context}.sources[{i}]"
        src_map: dict[str, Any] = dict(src)
        definition: Mapping[str, Any] | None = None
        cat = src.get("catalog")
        if is_json_mapping(cat) and _is_ref(cat):
            match _render(library, "catalog", cat, context=f"{src_ctx}.catalog"):
                case Err() as err:
                    return err
                case Ok(rendered):
                    pass
            if not is_json_mapping(rendered) or not rendered.get("id"):
                return _err(f"{src_ctx}.catalog: catalog recipe must render to an object with `id`")
            definition = rendered
            src_map["catalog"] = rendered
            src_map.setdefault("type", rendered.get("type"))
        match compile_source(src_map, resolver, context=src_ctx):
            case Err() as err:
                return err
            case Ok(compiled):
                pass
        compiled_sources.append(compiled)
        if compiled.get("addonId") == AIOMETADATA_MANIFEST_ID or (
            (info := resolver.get(str(src.get("addon") or ""))) is not None and info.name == "aiometadata"
        ):
            uses.append(CatalogUse(catalog_id=str(compiled["catalogId"]), type=str(compiled["type"]), definition=definition))
    out["sources"] = compiled_sources
    return Ok(out)


def compile_collections(
    items: Sequence[Any], library: Library, resolver: AddonResolver
) -> Result[CompiledCollections, ConfigValidationError]:
    """Compiles a profile's `collections` (ref-or-inline, two levels) into raw Nuvio collections."""
    out: list[JsonDict] = []
    uses: list[CatalogUse] = []
    provenance: dict[str, Mapping[str, Any]] = {}
    for ci, item in enumerate(items):
        ctx = f"collections[{ci}]"
        if _is_ref(item):
            match _render(library, "collection", item, context=ctx):
                case Err() as err:
                    return err
                case Ok(collection):
                    pass
            ctx = f"{ctx}({item['ref']})"
        elif is_json_mapping(item):
            collection = item
        else:
            return _err(f"{ctx}: must be a `{{ref}}` or an inline collection object")
        if not is_json_mapping(collection) or not collection.get("id"):
            return _err(f"{ctx}: collection must be an object with `id`")
        compiled = copy.deepcopy(dict(collection))
        folders = collection.get("folders", [])
        if not is_json_list(folders):
            return _err(f"{ctx}: `folders` must be a list")
        compiled_folders: list[JsonDict] = []
        for fi, fitem in enumerate(folders):
            fctx = f"{ctx}.folders[{fi}]"
            origin: Mapping[str, Any] | None = None
            if _is_ref(fitem):
                match _render(library, "folder", fitem, context=fctx):
                    case Err() as err:
                        return err
                    case Ok(folder):
                        pass
                origin = {"ref": fitem["ref"], **({"with": fitem["with"]} if fitem.get("with") else {})}
                fctx = f"{fctx}({fitem['ref']})"
            elif is_json_mapping(fitem):
                folder = fitem
            else:
                return _err(f"{fctx}: must be a `{{ref}}` or an inline folder object")
            if not is_json_mapping(folder) or not folder.get("id"):
                return _err(f"{fctx}: folder must be an object with `id`")
            match _compile_folder(folder, library, resolver, context=fctx, uses=uses):
                case Err() as err:
                    return err
                case Ok(cf):
                    compiled_folders.append(cf)
            if origin is not None:
                provenance[str(folder["id"])] = origin
        compiled["folders"] = compiled_folders
        out.append(compiled)
    ids = [c["id"] for c in out]
    if len(set(ids)) != len(ids):
        return _err(f"collections: duplicate collection ids {sorted({i for i in ids if ids.count(i) > 1})}")
    return Ok(CompiledCollections(collections=tuple(out), catalog_uses=tuple(uses), provenance=provenance))


def eject_collections(
    remote: Sequence[Mapping[str, Any]],
    items: Sequence[Any],
    compiled: CompiledCollections,
    library: Library,
    resolver: AddonResolver,
) -> list[Any]:
    """
    Reconciles the server's collections with the profile's composition:
    equal to its rendering → keep the original item; changed → inline (folders that still match
    their recipe stay refs); unknown → inline with symbolic sources; missing on server → dropped.
    """
    compiled_by_id = {str(c["id"]): c for c in compiled.collections}
    item_by_id: dict[str, Any] = {}
    for item, c in zip(items, compiled.collections, strict=False):
        item_by_id[str(c["id"])] = item

    def _folder_matches_recipe(folder: Mapping[str, Any]) -> Mapping[str, Any] | None:
        origin = compiled.provenance.get(str(folder.get("id")))
        if origin is None:
            return None
        match library.render("folder", str(origin["ref"]), origin.get("with")):
            case Ok(rendered) if is_json_mapping(rendered):
                pass
            case _:
                return None
        match _compile_folder(rendered, library, resolver, context="eject", uses=[]):
            case Ok(rc) if rc == dict(folder):
                return origin
            case _:
                return None

    def _inline_collection(col: Mapping[str, Any]) -> JsonDict:
        out = copy.deepcopy(dict(col))
        folders: list[Any] = []
        for f in col.get("folders", []) or []:
            if not is_json_mapping(f):
                continue
            origin = _folder_matches_recipe(f)
            if origin is not None:
                folders.append(dict(origin))
                continue
            inline = copy.deepcopy(dict(f))
            inline["sources"] = [symbolic_source(s, resolver) for s in f.get("sources", []) if is_json_mapping(s)]
            folders.append(inline)
        out["folders"] = folders
        return out

    result: list[Any] = []
    for rc in remote:
        if not is_json_mapping(rc):
            continue
        cid = str(rc.get("id"))
        local = compiled_by_id.get(cid)
        if local is not None and dict(rc) == local:
            result.append(item_by_id[cid])
        else:
            result.append(_inline_collection(rc))
    return result


# ---------------------------------------------------------------------------
# Whole-document refs (badges / meta / settings)
# ---------------------------------------------------------------------------


def compile_doc(item: Any, kind: str, library: Library) -> Result[JsonDict | None, ConfigValidationError]:
    """`{"ref": id}` → rendered document; inline object → itself; None → None."""
    if item is None:
        return Ok(None)
    if _is_ref(item):
        match _render(library, kind, item, context=kind):
            case Err() as err:
                return err
            case Ok(rendered):
                if not is_json_mapping(rendered):
                    return _err(f"{kind} recipe `{item['ref']}` must render to an object")
                return Ok(dict(rendered))
    if is_json_mapping(item):
        return Ok(dict(item))
    return _err(f"`{kind}` must be a `{{ref}}` or an inline document")


def eject_doc(remote: Mapping[str, Any] | None, item: Any, kind: str, library: Library) -> Any:
    """Keeps the ref when the server document equals its rendering, otherwise returns the inline document."""
    if remote is None:
        return item
    match compile_doc(item, kind, library):
        case Ok(rendered) if rendered is not None and _normalised(rendered) == _normalised(remote):
            return item
        case _:
            return dict(remote)


def _normalised(doc: Mapping[str, Any]) -> JsonDict:
    out = copy.deepcopy(dict(doc))
    out.pop("version", None)
    return out


# ---------------------------------------------------------------------------
# Derived AIOMetadata catalogs
# ---------------------------------------------------------------------------


def config_catalog_keys(entry: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """
    Manifest-side `(catalogId, type)` keys under which an AIOMetadata *config* entry is exposed.
    AIOMetadata publishes some sources with a type suffix and a display type, e.g.
    config `streaming.nfx` (movie) → manifest `streaming.nfx_movie` (movie), and
    config `mal.airing` (anime, displayType series) → manifest `mal.airing_anime` (series);
    `tmdb.*` / `trakt.*` keep their ids. Both forms are indexed so either resolves.
    """
    cid = str(entry.get("id") or "")
    ctype = str(entry.get("type") or "")
    display = str(entry.get("displayType") or ctype)
    keys: list[tuple[str, str]] = [(cid, ctype), (f"{cid}_{ctype}", display)]
    if ctype == "anime" and display != "series":
        keys.append((f"{cid}_anime", "series"))
    return tuple(keys)


def config_catalog_index(entries: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], int]:
    """manifest key → index of the config entry (first entry wins on collisions)."""
    index: dict[tuple[str, str], int] = {}
    for i, entry in enumerate(entries):
        if not is_json_mapping(entry):
            continue
        for key in config_catalog_keys(entry):
            index.setdefault(key, i)
    return index


@dataclass(frozen=True, slots=True)
class ExtraCatalog:
    catalog_id: str
    type: str
    enabled: bool = True
    show_in_home: bool = False
    definition: Mapping[str, Any] | None = None  # rendered catalog recipe (materialised if absent)


def derive_catalogs(
    instance_catalogs: Sequence[Mapping[str, Any]],
    uses: Sequence[CatalogUse],
    extras: Sequence[ExtraCatalog] = (),
    *,
    home_rows: str = "all",
) -> Result[list[JsonDict], ConfigValidationError]:
    """
    Rewrites an AIOMetadata instance's `catalogs` so that: referenced catalogs are enabled + shown
    on Home (library definitions overlaid), explicit extras keep their requested flags, and
    everything else is disabled. Order of existing entries is preserved; new library catalogs
    are appended. Referencing a built-in catalog the instance doesn't list is an error.
    """
    used: dict[tuple[str, str], CatalogUse] = {}
    for u in uses:
        used.setdefault((u.catalog_id, u.type), u)
    entries = [e for e in instance_catalogs if is_json_mapping(e)]
    index = config_catalog_index(entries)
    extra_by_key = {(e.catalog_id, e.type): e for e in extras}

    # Map manifest-side uses/extras onto config entry positions.
    used_by_pos: dict[int, CatalogUse] = {}
    missing: list[CatalogUse] = []
    for key, u in used.items():
        pos = index.get(key)
        if pos is None:
            missing.append(u)
        else:
            used_by_pos.setdefault(pos, u)
    extra_by_pos: dict[int, ExtraCatalog] = {}
    missing_extras: list[ExtraCatalog] = []
    for key, e in extra_by_key.items():
        pos = index.get(key)
        if pos is not None:
            extra_by_pos.setdefault(pos, e)
        elif e.definition:
            missing_extras.append(e)

    out: list[JsonDict] = []
    for pos, entry in enumerate(entries):
        new = copy.deepcopy(dict(entry))
        if pos in used_by_pos:
            definition = used_by_pos[pos].definition
            if definition:
                for k, v in definition.items():
                    if k not in ("enabled", "showInHome"):
                        new[k] = copy.deepcopy(v)
            new["enabled"] = True
            new["showInHome"] = home_rows == "all"
        elif pos in extra_by_pos:
            e = extra_by_pos[pos]
            if e.definition:
                for k, v in e.definition.items():
                    if k not in ("enabled", "showInHome"):
                        new[k] = copy.deepcopy(v)
            new["enabled"] = e.enabled
            new["showInHome"] = e.show_in_home
        else:
            new["enabled"] = False
            new["showInHome"] = False
        out.append(new)

    for u in missing:
        if not u.definition:
            return _err(
                f"collections reference catalog `{u.catalog_id}` ({u.type}) which the AIOMetadata instance does not list "
                "and no library catalog recipe defines"
            )
        new = copy.deepcopy(dict(u.definition))
        new["enabled"] = True
        new["showInHome"] = home_rows == "all"
        out.append(new)
    for e in missing_extras:
        assert e.definition is not None
        new = copy.deepcopy(dict(e.definition))
        new["enabled"] = e.enabled
        new["showInHome"] = e.show_in_home
        out.append(new)
    if home_rows == "extras":
        order = {(e.catalog_id, e.type): n for n, e in enumerate(extras) if e.show_in_home}
        home = sorted((c for c in out if c.get("showInHome")), key=lambda c: order.get((str(c["id"]), str(c["type"])), 10**6))
        rest = [c for c in out if not c.get("showInHome")]
        out = home + rest
    return Ok(out)
