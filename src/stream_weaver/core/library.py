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
from stream_weaver.core.template import (
    ParamSchema,
    bind_params,
    expand,
    parse_param_schema,
    referenced_variables,
)
from stream_weaver.core.types import JsonDict, JsonValue, is_json_mapping

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
class Recipe:
    kind: str
    id: str
    params: ParamSchema
    body: JsonValue
    source: Path  # file it was loaded from (for diagnostics / `sw library show`)

    @property
    def is_parameterised(self) -> bool:
        return bool(self.params.params)


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
    return Ok(Recipe(kind=str(kind), id=rid, params=schema, body=body, source=source))


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
    if recipe.params.params:
        params: JsonDict = {}
        for p in recipe.params.params:
            spec: JsonDict = {"type": p.type}
            if p.type == "list" and p.items != "string":
                spec["items"] = p.items
            if p.nullable:
                spec["nullable"] = True
            if p.has_default:
                spec["default"] = p.default
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
