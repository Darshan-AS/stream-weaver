"""
Recipe library — identity-free, reusable fragments that profiles compose.

A library is an ordered list of root directories (tenant-local first, then shared). Each root
holds `<kind>/<id>.json`. The first root that defines an id wins, so a tenant can override or
add recipes without touching the shared library.

Recipe document:

    {
      "$schema": "…/recipe.<kind>.v1.json",
      "kind": "folder",                 // collection | folder | catalog | badges | meta | settings
      "id": "regional-movies",
      "params": { "language": {"type": "string"}, "decades": {"type": "list", "default": []} },
      "folder": { ...body with ${language} / for-loops... }
    }

The body key equals the kind. `render(kind, id, with)` binds params, expands the body and
returns plain JSON. Filesystem access is confined to `Library.load` (reads only the given roots).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import SECRET_PLACEHOLDER_RE
from stream_weaver.core.template import (
    ParamSchema,
    bind_params,
    expand,
    parse_param_schema,
    referenced_variables,
)
from stream_weaver.core.types import JsonDict, JsonValue, is_json_list, is_json_mapping

RECIPE_KINDS: tuple[str, ...] = ("collection", "folder", "catalog", "badges", "meta", "settings")
"""Body key == kind. Directory name is the plural-ish folder below."""

KIND_DIRS: Mapping[str, str] = {
    "collection": "collections",
    "folder": "folders",
    "catalog": "catalogs",
    "badges": "badges",
    "meta": "meta",
    "settings": "settings",
}
RECIPE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RecipePreset:
    label: str
    params: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RecipeUiMeta:
    title: str = ""
    description: str = ""
    category: str = ""
    presets: tuple[RecipePreset, ...] = ()


@dataclass(frozen=True, slots=True)
class Recipe:
    kind: str
    id: str
    params: ParamSchema
    body: JsonValue
    source: Path  # file it was loaded from (for diagnostics / `sw library show`)
    ui: RecipeUiMeta = field(default_factory=RecipeUiMeta)

    @property
    def is_parameterised(self) -> bool:
        return bool(self.params.params)


def _parse_ui_meta(
    raw: Any, schema: ParamSchema, *, source: Path
) -> Result[RecipeUiMeta, ConfigValidationError]:
    if raw is None:
        return Ok(RecipeUiMeta())
    if not is_json_mapping(raw):
        return Err(ConfigValidationError(f"{source}: `ui` must be an object"))
    title = raw.get("title", "")
    desc = raw.get("description", "")
    cat = raw.get("category", "")
    if not all(isinstance(v, str) for v in (title, desc, cat)):
        return Err(ConfigValidationError(f"{source}: `ui.title`, `ui.description`, `ui.category` must be strings"))
    raw_presets = raw.get("presets")
    presets: list[RecipePreset] = []
    if raw_presets is not None:
        if not is_json_list(raw_presets):
            return Err(ConfigValidationError(f"{source}: `ui.presets` must be a list"))
        for i, item in enumerate(raw_presets):
            if (
                not is_json_mapping(item)
                or not isinstance(item.get("label"), str)
                or not is_json_mapping(item.get("with"))
            ):
                return Err(
                    ConfigValidationError(f"{source}: `ui.presets[{i}]` must have string `label` and object `with`")
                )
            match bind_params(schema, item["with"], context=f"{source}: ui.presets[{i}]"):
                case Err() as err:
                    return err
                case Ok():
                    pass
            presets.append(RecipePreset(label=item["label"], params=dict(item["with"])))
    return Ok(RecipeUiMeta(title=title, description=desc, category=cat, presets=tuple(presets)))


def parse_recipe(doc: Mapping[str, Any], *, source: Path) -> Result[Recipe, ConfigValidationError]:
    kind = doc.get("kind")
    if kind not in RECIPE_KINDS:
        return Err(ConfigValidationError(f"{source}: `kind` must be one of {RECIPE_KINDS}, got {kind!r}"))
    rid = doc.get("id")
    if not isinstance(rid, str) or not rid or "/" in rid:
        return Err(ConfigValidationError(f"{source}: `id` must be a non-empty string without '/'"))
    if source.stem != rid:
        return Err(ConfigValidationError(f"{source}: file name must equal recipe id `{rid}`"))
    if doc.get("version", RECIPE_SCHEMA_VERSION) != RECIPE_SCHEMA_VERSION:
        return Err(ConfigValidationError(f"{source}: unsupported recipe version {doc.get('version')!r}"))
    if kind not in doc:
        return Err(ConfigValidationError(f"{source}: missing body key `{kind}`"))
    match parse_param_schema(doc.get("params"), context=str(source)):
        case Err() as err:
            return err
        case Ok(schema):
            pass
    match _parse_ui_meta(doc.get("ui"), schema, source=source):
        case Err() as err:
            return err
        case Ok(ui_meta):
            pass
    body = doc[kind]
    unknown_vars = referenced_variables(body) - set(schema.by_name())
    # loop variables are declared inline; only flag names that are neither params nor `for` vars
    loop_vars = _declared_loop_vars(body)
    unknown_vars -= loop_vars
    if unknown_vars:
        return Err(
            ConfigValidationError(
                f"{source}: body references undeclared variable(s) {sorted(unknown_vars)}; declare them under `params`"
            )
        )
    return Ok(Recipe(kind=str(kind), id=rid, params=schema, body=body, source=source, ui=ui_meta))


def _declared_loop_vars(node: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(node, Mapping):
        if "for" in node and isinstance(node.get("for"), str):
            out.add(node["for"])
        for v in node.values():
            out |= _declared_loop_vars(v)
    elif isinstance(node, list):
        for x in node:
            out |= _declared_loop_vars(x)
    return out


def recipe_to_doc(recipe: Recipe) -> JsonDict:
    """Canonical JSON form of a recipe (inverse of `parse_recipe`)."""
    doc: JsonDict = {
        "$schema": f"https://stream-weaver.dev/schemas/recipe.{recipe.kind}.v{RECIPE_SCHEMA_VERSION}.json",
        "version": RECIPE_SCHEMA_VERSION,
        "kind": recipe.kind,
        "id": recipe.id,
    }
    if any((recipe.ui.title, recipe.ui.description, recipe.ui.category, recipe.ui.presets)):
        ui_doc: JsonDict = {}
        if recipe.ui.title:
            ui_doc["title"] = recipe.ui.title
        if recipe.ui.description:
            ui_doc["description"] = recipe.ui.description
        if recipe.ui.category:
            ui_doc["category"] = recipe.ui.category
        if recipe.ui.presets:
            ui_doc["presets"] = [{"label": p.label, "with": dict(p.params)} for p in recipe.ui.presets]
        doc["ui"] = ui_doc
    if recipe.params.params:
        params: JsonDict = {}
        for p in recipe.params.params:
            spec: JsonDict = {"type": p.type}
            if p.label:
                spec["label"] = p.label
            if p.description:
                spec["description"] = p.description
            if p.type == "list" and p.items != "string":
                spec["items"] = p.items
            if p.nullable:
                spec["nullable"] = True
            if p.has_default:
                spec["default"] = p.default
            if p.options:
                spec["options"] = [{"label": o.label, "value": o.value} for o in p.options]
            params[p.name] = spec
        doc["params"] = params
    doc[recipe.kind] = recipe.body
    return doc


@dataclass(frozen=True, slots=True)
class Library:
    """An immutable, fully-loaded recipe index across ordered roots (first root wins)."""

    roots: tuple[Path, ...]
    recipes: Mapping[tuple[str, str], Recipe] = field(default_factory=dict)
    errors: tuple[ConfigValidationError, ...] = ()

    @classmethod
    def load(cls, roots: Sequence[Path]) -> Library:
        found: dict[tuple[str, str], Recipe] = {}
        errors: list[ConfigValidationError] = []
        for root in roots:
            for kind, sub in KIND_DIRS.items():
                kind_dir = root / sub
                if not kind_dir.is_dir():
                    continue
                for path in sorted(kind_dir.glob("*.json")):
                    key = (kind, path.stem)
                    if key in found:
                        continue  # earlier root wins
                    try:
                        raw = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        errors.append(ConfigValidationError(f"{path}: {exc}"))
                        continue
                    if not is_json_mapping(raw):
                        errors.append(ConfigValidationError(f"{path}: recipe must be a JSON object"))
                        continue
                    match parse_recipe(raw, source=path):
                        case Err(err):
                            errors.append(err)
                        case Ok(recipe):
                            found[key] = recipe
        return cls(roots=tuple(roots), recipes=found, errors=tuple(errors))

    def get(self, kind: str, rid: str) -> Result[Recipe, ConfigValidationError]:
        recipe = self.recipes.get((kind, rid))
        if recipe is None:
            known = sorted(i for k, i in self.recipes if k == kind)
            return Err(
                ConfigValidationError(
                    f"no {kind} recipe named `{rid}` in library roots {[str(r) for r in self.roots]} "
                    f"(known: {', '.join(known) or 'none'})"
                )
            )
        return Ok(recipe)

    def ids(self, kind: str) -> tuple[str, ...]:
        return tuple(sorted(i for k, i in self.recipes if k == kind))

    def render(self, kind: str, rid: str, params: Mapping[str, Any] | None = None) -> Result[Any, ConfigValidationError]:
        """Binds params and expands the recipe body to plain JSON."""
        match self.get(kind, rid):
            case Err() as err:
                return err
            case Ok(recipe):
                pass
        ctx = f"{kind}/{rid}"
        match bind_params(recipe.params, params, context=ctx):
            case Err() as err:
                return err
            case Ok(bound):
                pass
        return expand(recipe.body, bound, context=ctx)

    def root_of(self, kind: str, rid: str) -> Path | None:
        recipe = self.recipes.get((kind, rid))
        if recipe is None:
            return None
        for root in self.roots:
            try:
                recipe.source.relative_to(root)
                return root
            except ValueError:
                continue
        return None


@dataclass(frozen=True, slots=True)
class FolderPreview:
    id: str
    title: str
    cover_url: str
    row_titles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecipeUiSummary:
    kind: str
    id: str
    title: str
    description: str
    category: str
    backdrop_url: str
    folders: tuple[FolderPreview, ...]
    required_secrets: tuple[str, ...]
    params: ParamSchema
    presets: tuple[RecipePreset, ...]
    is_local: bool = False


_SYMBOLIC_SECRET_HINTS: Mapping[tuple[str, str], str] = {
    ("addon", "bingecat"): "SECRET_BINGECAT_TOKEN",
    ("preset", "ito"): "SECRET_ITSOUT_TOKEN",
}


def _infer_secrets(node: Any, library: Library, seen_refs: set[tuple[str, str]]) -> set[str]:
    secrets: set[str] = set()
    if isinstance(node, str):
        secrets.update(m.group(1) for m in SECRET_PLACEHOLDER_RE.finditer(node))
    elif isinstance(node, list):
        for item in node:
            secrets |= _infer_secrets(item, library, seen_refs)
    elif isinstance(node, Mapping):
        for field_name, val in _SYMBOLIC_SECRET_HINTS:
            if node.get(field_name) == val:
                secrets.add(_SYMBOLIC_SECRET_HINTS[(field_name, val)])
        cat_id = node.get("catalogId")
        if isinstance(cat_id, str) and cat_id.startswith("trakt."):
            secrets.add("SECRET_TRAKT_TOKEN_ID")
        ref = node.get("ref")
        if isinstance(ref, str) and "${" not in ref:
            for k in ("folder", "catalog"):
                if (k, ref) not in seen_refs and (k, ref) in library.recipes:
                    seen_refs.add((k, ref))
                    secrets |= _infer_secrets(library.recipes[(k, ref)].body, library, seen_refs)
        for v in node.values():
            secrets |= _infer_secrets(v, library, seen_refs)
    return secrets


def describe_recipe(
    recipe: Recipe,
    library: Library,
    *,
    with_params: Mapping[str, Any] | None = None,
    is_local: bool = False,
) -> RecipeUiSummary:
    """Purely derives a UI-ready summary (title, folder cover previews, required secrets, params) for a recipe."""
    sample_params = dict(with_params) if with_params else None
    if sample_params is None and recipe.ui.presets:
        sample_params = dict(recipe.ui.presets[0].params)

    rendered_body: Any = recipe.body
    match bind_params(recipe.params, sample_params, context=f"{recipe.kind}/{recipe.id}"):
        case Ok(bound):
            match expand(recipe.body, bound, context=f"{recipe.kind}/{recipe.id}"):
                case Ok(exp):
                    rendered_body = exp
                case Err():
                    pass
        case Err():
            pass

    title = recipe.ui.title
    backdrop_url = ""
    folders: list[FolderPreview] = []
    if isinstance(rendered_body, Mapping):
        if not title and isinstance(rendered_body.get("title"), str):
            title = rendered_body["title"]
        if isinstance(rendered_body.get("backdropImageUrl"), str):
            backdrop_url = rendered_body["backdropImageUrl"]
        raw_folders = rendered_body.get("folders")
        if isinstance(raw_folders, list):
            for f_item in raw_folders:
                if not isinstance(f_item, Mapping):
                    continue
                f_doc: Mapping[str, Any] | None = None
                if isinstance(f_item.get("ref"), str):
                    match library.render("folder", f_item["ref"], f_item.get("with")):
                        case Ok(rf) if isinstance(rf, Mapping):
                            f_doc = rf
                        case _:
                            pass
                else:
                    f_doc = f_item
                if f_doc is not None:
                    sources = f_doc.get("sources")
                    rows = tuple(
                        str(s.get("title"))
                        for s in (sources if isinstance(sources, list) else [])
                        if isinstance(s, Mapping) and s.get("title")
                    )
                    folders.append(
                        FolderPreview(
                            id=str(f_doc.get("id") or ""),
                            title=str(f_doc.get("title") or ""),
                            cover_url=str(f_doc.get("coverImageUrl") or ""),
                            row_titles=rows,
                        )
                    )

    if not title:
        title = recipe.id.removeprefix("col-").removeprefix("fld-").replace("-", " ").title()

    inferred = _infer_secrets(rendered_body, library, set())
    return RecipeUiSummary(
        kind=recipe.kind,
        id=recipe.id,
        title=title,
        description=recipe.ui.description,
        category=recipe.ui.category or recipe.kind,
        backdrop_url=backdrop_url,
        folders=tuple(folders),
        required_secrets=tuple(sorted(inferred)),
        params=recipe.params,
        presets=recipe.ui.presets,
        is_local=is_local,
    )
