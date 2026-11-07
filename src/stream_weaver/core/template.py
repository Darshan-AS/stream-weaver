"""
Recipe template engine — deliberately tiny.

A recipe body may contain exactly three constructs, nothing else:

* `"${name}"` inside strings — parameter substitution. A string that is *exactly* `${name}`
  yields the parameter's typed value (list, bool, number); otherwise the value is interpolated.
  `${name.key}` reads a key of an object-valued variable (one level of member access, used by
  `for` loops over lists of objects); there is no other navigation.
* `{"for": "x", "in": "${list}", "item": <node>}` inside a list — expands to one `item` per
  element, with `x` bound; nested `for`s may reference outer variables.
* `{"if": "${flag}", "item": <node>}` inside a list — included only when the (bool) value is true.

There are no expressions, filters, includes, or arithmetic. Keeping the surface this small is
what lets recipes stay data and lets `sw pull` compare compiled output against refs losslessly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from stream_weaver.core.errors import ConfigValidationError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonValue, is_json_list, is_json_mapping

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\}")
_EXACT_VAR_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\}$")


def _lookup(scope: Mapping[str, JsonValue], ref: str) -> tuple[bool, JsonValue]:
    """Resolves `name` or `name.key` against the scope. Returns (found, value)."""
    name, _, key = ref.partition(".")
    if name not in scope:
        return False, None
    val = scope[name]
    if not key:
        return True, val
    if isinstance(val, Mapping) and key in val:
        return True, val[key]
    return False, None

PARAM_TYPES: tuple[str, ...] = ("string", "bool", "int", "list")


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    type: str
    default: JsonValue = None
    has_default: bool = False
    items: str = "string"  # element type for lists (informational)
    nullable: bool = False  # accepts / defaults to null (rendered as JSON null when used as an exact `${x}`)


@dataclass(frozen=True, slots=True)
class ParamSchema:
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)

    def by_name(self) -> dict[str, ParamSpec]:
        return {p.name: p for p in self.params}


def _err(msg: str) -> Err[ConfigValidationError]:
    return Err(ConfigValidationError(msg))


def parse_param_schema(raw: Any, *, context: str = "recipe") -> Result[ParamSchema, ConfigValidationError]:
    if raw is None:
        return Ok(ParamSchema())
    if not is_json_mapping(raw):
        return _err(f"{context}: `params` must be an object")
    specs: list[ParamSpec] = []
    for name, spec in raw.items():
        if not isinstance(name, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            return _err(f"{context}: invalid param name {name!r}")
        if not is_json_mapping(spec):
            return _err(f"{context}: param `{name}` must be an object")
        ptype = spec.get("type", "string")
        if ptype not in PARAM_TYPES:
            return _err(f"{context}: param `{name}` has unsupported type {ptype!r} (allowed: {', '.join(PARAM_TYPES)})")
        has_default = "default" in spec
        default = spec.get("default")
        nullable = bool(spec.get("nullable", False))
        if has_default and not (nullable and default is None):
            match _check_type(default, ptype):
                case Err(e):
                    return _err(f"{context}: param `{name}` default {e.message}")
                case Ok():
                    pass
        specs.append(
            ParamSpec(
                name=name,
                type=str(ptype),
                default=default,
                has_default=has_default,
                items=str(spec.get("items", "string")),
                nullable=nullable,
            )
        )
    return Ok(ParamSchema(tuple(specs)))


def _check_type(value: Any, ptype: str) -> Result[None, ConfigValidationError]:
    ok = {
        "string": isinstance(value, str),
        "bool": isinstance(value, bool),
        "int": isinstance(value, int) and not isinstance(value, bool),
        "list": isinstance(value, list),
    }[ptype]
    return Ok(None) if ok else _err(f"must be of type {ptype}, got {type(value).__name__}")


def bind_params(
    schema: ParamSchema, given: Mapping[str, Any] | None, *, context: str = "recipe"
) -> Result[dict[str, JsonValue], ConfigValidationError]:
    """Validates `given` against `schema` and fills defaults. Unknown or missing params are errors."""
    given = given or {}
    specs = schema.by_name()
    unknown = set(given) - set(specs)
    if unknown:
        return _err(f"{context}: unknown param(s) {sorted(unknown)}")
    bound: dict[str, JsonValue] = {}
    for name, spec in specs.items():
        if name in given:
            if not (spec.nullable and given[name] is None):
                match _check_type(given[name], spec.type):
                    case Err(e):
                        return _err(f"{context}: param `{name}` {e.message}")
                    case Ok():
                        pass
            bound[name] = given[name]
        elif spec.has_default:
            bound[name] = spec.default
        else:
            return _err(f"{context}: missing required param `{name}`")
    return Ok(bound)


def _subst_string(text: str, scope: Mapping[str, JsonValue], *, context: str) -> Result[JsonValue, ConfigValidationError]:
    exact = _EXACT_VAR_RE.match(text)
    if exact:
        found, value = _lookup(scope, exact.group(1))
        if not found:
            return _err(f"{context}: unknown variable `${{{exact.group(1)}}}`")
        return Ok(value)
    missing: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        found, val = _lookup(scope, m.group(1))
        if not found:
            missing.append(m.group(1))
            return m.group(0)
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, int | float | str):
            return str(val)
        return str(val)

    out = _VAR_RE.sub(_repl, text)
    if missing:
        return _err(f"{context}: unknown variable(s) {sorted(set(missing))}")
    return Ok(out)


def expand(node: Any, scope: Mapping[str, JsonValue], *, context: str = "recipe") -> Result[Any, ConfigValidationError]:
    """Recursively expands a recipe body under `scope` (bound params + loop variables)."""
    if isinstance(node, str):
        return _subst_string(node, scope, context=context)
    if is_json_list(node):
        out: list[Any] = []
        for idx, item in enumerate(node):
            if is_json_mapping(item) and "for" in item and "in" in item and "item" in item:
                match _expand_for(item, scope, context=f"{context}[{idx}]"):
                    case Err() as err:
                        return err
                    case Ok(items):
                        out.extend(items)
                continue
            if is_json_mapping(item) and "if" in item and "item" in item and len(item) == 2:
                match expand(item["if"], scope, context=f"{context}[{idx}].if"):
                    case Err() as err:
                        return err
                    case Ok(cond):
                        pass
                if not isinstance(cond, bool):
                    return _err(f"{context}[{idx}]: `if` must evaluate to a bool, got {type(cond).__name__}")
                if cond:
                    match expand(item["item"], scope, context=f"{context}[{idx}]"):
                        case Err() as err:
                            return err
                        case Ok(val):
                            out.append(val)
                continue
            match expand(item, scope, context=f"{context}[{idx}]"):
                case Err() as err:
                    return err
                case Ok(val):
                    out.append(val)
        return Ok(out)
    if is_json_mapping(node):
        out_map: dict[str, Any] = {}
        for k, v in node.items():
            match expand(v, scope, context=f"{context}.{k}"):
                case Err() as err:
                    return err
                case Ok(val):
                    out_map[str(k)] = val
        return Ok(out_map)
    return Ok(node)


def _expand_for(
    item: Mapping[str, Any], scope: Mapping[str, JsonValue], *, context: str
) -> Result[list[Any], ConfigValidationError]:
    var = item["for"]
    if not isinstance(var, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", var):
        return _err(f"{context}: `for` must name a variable")
    if set(item) - {"for", "in", "item"}:
        return _err(f"{context}: `for` blocks accept only `for`, `in`, `item`")
    match expand(item["in"], scope, context=f"{context}.in"):
        case Err() as err:
            return err
        case Ok(seq):
            pass
    if not isinstance(seq, list):
        return _err(f"{context}: `in` must evaluate to a list, got {type(seq).__name__}")
    out: list[Any] = []
    for i, element in enumerate(seq):
        inner = {**scope, var: element}
        match expand(item["item"], inner, context=f"{context}[{var}={i}]"):
            case Err() as err:
                return err
            case Ok(val):
                out.append(val)
    return Ok(out)


def referenced_variables(node: Any) -> frozenset[str]:
    """All `${name}` root variables mentioned anywhere in a body (for linting recipes against their params)."""
    found: set[str] = set()

    def _walk(n: Any) -> None:
        if isinstance(n, str):
            found.update(ref.split(".", 1)[0] for ref in _VAR_RE.findall(n))
        elif isinstance(n, list):
            for x in n:
                _walk(x)
        elif isinstance(n, Mapping):
            for v in n.values():
                _walk(v)

    _walk(node)
    return frozenset(found)
