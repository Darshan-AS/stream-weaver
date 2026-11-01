"""
Tenant model: the unit of identity + infrastructure that `sw` operates on.

A *tenant* is a directory containing `tenant.json`, a `.env` with all secret values,
and the service / profile documents the tool compiles and pushes. Nothing in this
module touches the filesystem except `resolve_tenant_root` (existence checks only);
everything else is pure.

Layout (v1):

    <tenant>/
    ├── tenant.json          ← marker + identity registry (instances, logins, profiles)
    ├── .env                 ← all values (never committed)
    ├── aiostreams.json      ← raw instance document(s)
    ├── aiomanager.json
    ├── aiometadata.json     ← (moves under profiles/ in a later schema version)
    ├── nuvio_*.json
    ├── architecture/        ← generated LikeC4 model
    └── ARCHITECTURE.md      ← generated
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stream_weaver.core.errors import ConfigValidationError, StorageError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict, is_json_list, is_json_mapping

TENANT_FILE = "tenant.json"
TENANTS_DIR = "tenants"
TENANT_SCHEMA_VERSION = 1
ENV_DIR_OVERRIDE = "STREAM_WEAVER_DIR"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ENV_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

type InstanceRegistry = Mapping[str, Mapping[str, str]]
"""service → instance name → env key prefix (e.g. {"aiometadata": {"default": "AIOMETADATA"}})."""

type LoginRegistry = Mapping[str, Mapping[str, str]]
"""platform → login name → env key prefix (e.g. {"nuvio": {"don": "NUVIO_DON"}})."""


@dataclass(frozen=True, slots=True)
class TenantManifest:
    """Parsed, validated `tenant.json`."""

    name: str
    version: int = TENANT_SCHEMA_VERSION
    instances: InstanceRegistry = field(default_factory=dict)
    logins: LoginRegistry = field(default_factory=dict)
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TenantPaths:
    """Every filesystem location the tool is allowed to read or write for a tenant."""

    root: Path

    @property
    def manifest_file(self) -> Path:
        return self.root / TENANT_FILE

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @property
    def env_example_file(self) -> Path:
        return self.root / ".env.example"

    @property
    def docs_dir(self) -> Path:
        """Directory holding the service / client JSON documents (tenant root in v1)."""
        return self.root

    @property
    def architecture_dir(self) -> Path:
        return self.root / "architecture"

    @property
    def architecture_md(self) -> Path:
        return self.root / "ARCHITECTURE.md"

    @property
    def cache_dir(self) -> Path:
        return self.root / ".cache"

    def doc(self, name: str) -> Path:
        """Path of a JSON document by bare name (`aiostreams` → `<root>/aiostreams.json`)."""
        return self.docs_dir / f"{name}.json"

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)


def default_tenant_manifest(name: str) -> JsonDict:
    """Canonical `tenant.json` for a freshly initialised single-instance tenant."""
    return {
        "$schema": "https://stream-weaver.dev/schemas/tenant.v1.json",
        "version": TENANT_SCHEMA_VERSION,
        "name": name,
        "instances": {
            "aiostreams": {"default": "AIOSTREAMS"},
            "aiometadata": {"default": "AIOMETADATA"},
            "aiomanager": {"default": "AIOMANAGER"},
        },
        "logins": {},
        "profiles": [],
    }


def _parse_registry(
    raw: Any, *, label: str
) -> Result[dict[str, dict[str, str]], ConfigValidationError]:
    if raw is None:
        return Ok({})
    if not is_json_mapping(raw):
        return Err(ConfigValidationError(f"tenant.json: `{label}` must be an object"))
    out: dict[str, dict[str, str]] = {}
    for group, members in raw.items():
        if not is_json_mapping(members):
            return Err(ConfigValidationError(f"tenant.json: `{label}.{group}` must be an object"))
        inner: dict[str, str] = {}
        for member_name, prefix in members.items():
            if not isinstance(member_name, str) or not _NAME_RE.match(member_name):
                return Err(
                    ConfigValidationError(
                        f"tenant.json: `{label}.{group}` has invalid name {member_name!r} "
                        "(lowercase, digits, '-' or '_', max 32 chars)"
                    )
                )
            if not isinstance(prefix, str) or not _ENV_PREFIX_RE.match(prefix):
                return Err(
                    ConfigValidationError(
                        f"tenant.json: `{label}.{group}.{member_name}` must be an UPPER_SNAKE env prefix, got {prefix!r}"
                    )
                )
            inner[member_name] = prefix
        out[str(group)] = inner
    return Ok(out)


def parse_tenant_manifest(doc: Mapping[str, Any]) -> Result[TenantManifest, ConfigValidationError]:
    """Validates a `tenant.json` document into an immutable `TenantManifest`."""
    version = doc.get("version")
    if version != TENANT_SCHEMA_VERSION:
        return Err(
            ConfigValidationError(
                f"tenant.json: unsupported version {version!r} (expected {TENANT_SCHEMA_VERSION}); run `sw migrate`"
            )
        )
    name = doc.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return Err(
            ConfigValidationError(
                f"tenant.json: `name` must match {_NAME_RE.pattern}, got {name!r}"
            )
        )

    match _parse_registry(doc.get("instances"), label="instances"):
        case Err() as err:
            return err
        case Ok(instances):
            pass
    match _parse_registry(doc.get("logins"), label="logins"):
        case Err() as err:
            return err
        case Ok(logins):
            pass

    raw_profiles = doc.get("profiles", [])
    if not is_json_list(raw_profiles) or not all(
        isinstance(p, str) and _NAME_RE.match(p) for p in raw_profiles
    ):
        return Err(ConfigValidationError("tenant.json: `profiles` must be a list of profile names"))
    if len(set(raw_profiles)) != len(raw_profiles):
        return Err(ConfigValidationError("tenant.json: `profiles` contains duplicates"))

    return Ok(
        TenantManifest(
            name=name,
            version=TENANT_SCHEMA_VERSION,
            instances=instances,
            logins=logins,
            profiles=tuple(raw_profiles),
        )
    )


def is_tenant_dir(path: Path) -> bool:
    return (path / TENANT_FILE).is_file()


def resolve_tenant_root(
    explicit_dir: Path | None = None,
    *,
    tenant_name: str | None = None,
    cwd: Path | None = None,
    env_override: str | None = None,
    require_marker: bool = True,
) -> Result[Path, StorageError]:
    """
    Resolves the active tenant directory using priority:

    1. Explicit `--dir` / `-C`
    2. `--tenant NAME` → `<cwd>/tenants/NAME`
    3. `STREAM_WEAVER_DIR` environment variable
    4. Current working directory, if it contains `tenant.json`

    There is deliberately **no** fallback to the package / repository root: the tool
    never assumes where a tenant lives. With `require_marker=False` (used by `sw init`)
    the resolved directory is returned even if `tenant.json` does not exist yet.
    """
    effective_cwd = (cwd or Path.cwd()).resolve()

    candidate: Path | None = None
    source = ""
    if explicit_dir is not None:
        candidate, source = explicit_dir.expanduser().resolve(), "--dir"
    elif tenant_name:
        candidate, source = (effective_cwd / TENANTS_DIR / tenant_name).resolve(), "--tenant"
    elif env_override and env_override.strip():
        candidate, source = Path(env_override.strip()).expanduser().resolve(), ENV_DIR_OVERRIDE
    elif is_tenant_dir(effective_cwd) or not require_marker:
        candidate, source = effective_cwd, "cwd"

    if candidate is None:
        return Err(
            StorageError(
                "No tenant found. Run from a directory containing `tenant.json`, pass "
                "`--dir <tenant>` / `--tenant <name>`, set STREAM_WEAVER_DIR, or scaffold one with `sw init <dir>`.",
                path=str(effective_cwd),
            )
        )
    if require_marker and not is_tenant_dir(candidate):
        return Err(
            StorageError(
                f"`{candidate}` (from {source}) is not a tenant: missing `{TENANT_FILE}`. "
                "Scaffold one with `sw init <dir>`.",
                path=str(candidate),
            )
        )
    return Ok(candidate)


def discover_tenants(repo_root: Path) -> tuple[Path, ...]:
    """All `<repo_root>/tenants/*/tenant.json` directories, sorted by name."""
    base = repo_root / TENANTS_DIR
    if not base.is_dir():
        return ()
    return tuple(sorted(p for p in base.iterdir() if p.is_dir() and is_tenant_dir(p)))
