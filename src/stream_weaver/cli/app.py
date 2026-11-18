"""Stream Weaver CLI (`sw`) — Pure Functional GitOps Control Plane for AIOStreams, AIOMetadata, and AIOManager."""

import concurrent.futures
import json
import os
import sys
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from stream_weaver.cli.nuvio import NUVIO_TARGET_ALIASES
from stream_weaver.cli.packs import packs_app
from stream_weaver.core.addon_index import build_addon_resolver
from stream_weaver.core.aiomanager_accounts import (
    ResolvedProfile,
    apply_managed_accounts,
    derive_profile_from_account,
)
from stream_weaver.core.architecture import compile_architecture_bundle
from stream_weaver.core.coercions import normalize_service_config
from stream_weaver.core.compile import CompiledProfile, compile_profile, derive_instance_catalogs
from stream_weaver.core.composition import config_catalog_keys
from stream_weaver.core.crypto import decrypt_envelope
from stream_weaver.core.errors import StreamWeaverError
from stream_weaver.core.instances import (
    DEFAULT_INSTANCE,
    InstanceBinding,
    instance_env_keys,
    resolve_instance_binding,
)
from stream_weaver.core.library import Library, Recipe, recipe_to_doc
from stream_weaver.core.logins import (
    SUPPORTED_PLATFORMS,
    LoginBinding,
    login_env_key,
    login_env_keys,
    resolve_login_binding,
)
from stream_weaver.core.migrate import custom_preset_ids, extract_composition
from stream_weaver.core.profiles import (
    ExtraAddon,
    NuvioClientSpec,
    Profile,
    StremioClientSpec,
    parse_profile,
    profile_file,
    profile_to_doc,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import harvest_and_scrub
from stream_weaver.core.tenant import (
    ENV_DIR_OVERRIDE,
    TENANT_FILE,
    TenantManifest,
    TenantPaths,
    default_tenant_manifest,
    discover_tenants,
    parse_tenant_manifest,
    resolve_tenant_root,
)
from stream_weaver.core.types import (
    ALL_SERVICES,
    ConfigDiffOutcome,
    JsonDict,
    ServiceId,
)
from stream_weaver.effects.http import HttpxClient as HttpxClient
from stream_weaver.effects.storage import (
    load_env_file,
    read_json_file,
    read_text_file,
    save_env_updates,
    scaffold_env_keys,
    write_json_file,
    write_text_file,
)
from stream_weaver.engine import verify_workspace_health
from stream_weaver.engine.workspace import (
    apply_debrid_and_packs,
    diff_workspace,
    pull_workspace,
    push_workspace,
    warm_workspace_catalogs,
)
from stream_weaver.providers.services.nuvio_session import (
    NuvioSessionPool,
    password_grant,
    resolve_nuvio_anon_key,
)
from stream_weaver.resources import bundled_shared_library_root, bundled_starter_template_root
from stream_weaver.studio import StudioDispatcher, serve_studio

app = typer.Typer(
    name="sw",
    help="Stream Weaver — Pure Functional GitOps Control Plane for AIOStreams, AIOMetadata, AIOManager, Nuvio & Stremio.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Manage AIOStreams, AIOMetadata, and AIOManager GitOps configurations.")
app.add_typer(config_app, name="config")
app.add_typer(packs_app, name="packs")

console = Console(markup=False, highlight=False)
err_console = Console(stderr=True, markup=False, highlight=False)

_CLI_WORKSPACE_DIR: Path | None = None
_CLI_TENANT_NAME: str | None = None

WorkspaceDirOption = Annotated[
    Path | None,
    typer.Option(
        "--dir",
        "-C",
        help=f"Tenant directory containing {TENANT_FILE} and .env (default: CWD if it is a tenant)",
    ),
]

InstanceNameOption = Annotated[
    str,
    typer.Option(
        "--instance",
        "-i",
        help="Named service instance from tenant.json to address (default: `default`)",
    ),
]

ProfileNameOption = Annotated[
    str | None,
    typer.Option(
        "--profile",
        "-p",
        help="Scope to one profile: its Nuvio profile and its AIOMetadata/AIOStreams instances (default: all profiles)",
    ),
]

TenantNameOption = Annotated[
    str | None,
    typer.Option(
        "--tenant",
        "-t",
        help="Tenant name → resolves to ./tenants/<name> relative to the current directory",
    ),
]

_INIT_ENV_TEMPLATE = """\
# ==============================================================================
# 1. INSTANCE URLS (No trailing slashes)
# ==============================================================================
AIOSTREAMS_URL=https://aiostreams.elfhosted.com
AIOMETADATA_URL=https://aiometadata.elfhosted.com
AIOMANAGER_URL=https://aiomanager.elfhosted.com

# ==============================================================================
# 2. INSTANCE CREDENTIALS (UUID + Password for each service)
# ==============================================================================
AIOSTREAMS_UUID=
AIOSTREAMS_PASSWORD=
AIOSTREAMS_ADDON_PASSWORD=

AIOMETADATA_UUID=
AIOMETADATA_PASSWORD=
AIOMETADATA_ADDON_PASSWORD=

AIOMANAGER_UUID=
AIOMANAGER_SYNC_PASSWORD=

# ==============================================================================
# 3. SECRETS FOR GIT SCRUBBING (Prefix with SECRET_)
# ==============================================================================
SECRET_RD_API_KEY=
SECRET_TORBOX_API_KEY=
SECRET_ALLDEBRID_API_KEY=
SECRET_PREMIUMIZE_API_KEY=
SECRET_TMDB_API_KEY=
SECRET_TMDB_ACCESS_TOKEN=
SECRET_TVDB_API_KEY=
SECRET_RPDB_API_KEY=
SECRET_MDBLIST_API_KEY=
SECRET_ANIMESKIP_CLIENT_ID=
SECRET_INTRODB_API_KEY=
SECRET_TRAKT_TOKEN_ID=
SECRET_GEMINI_API_KEY=
SECRET_BINGECAT_TOKEN=
SECRET_WATCHLY_TOKEN=
SECRET_ITSOUT_TOKEN=
SECRET_SUBSOURCE_API_KEY=
SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD=

NUVIO_SUPABASE_ANON_KEY=
"""

_INIT_GITIGNORE_TEMPLATE = """\
# Environment & Secrets (NEVER commit these)
.env
.env.*
!.env.example

# Unscrubbed / backup config files & magnet dumps
*.raw.json
*.bak
*.tmp
*magnets*.md
*magnets*.txt
*.torrent

.DS_Store
"""


@app.callback()
def _main_callback(
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Global options for the Stream Weaver CLI."""
    global _CLI_WORKSPACE_DIR, _CLI_TENANT_NAME
    _CLI_WORKSPACE_DIR = workspace_dir
    _CLI_TENANT_NAME = tenant


def _resolve_tenant(
    workspace_dir: Path | None = None,
    tenant_name: str | None = None,
    *,
    require_marker: bool = True,
) -> TenantPaths:
    """Resolves the active tenant directory or exits with a actionable error."""
    match resolve_tenant_root(
        workspace_dir or _CLI_WORKSPACE_DIR,
        tenant_name=tenant_name or _CLI_TENANT_NAME,
        env_override=os.environ.get(ENV_DIR_OVERRIDE),
        require_marker=require_marker,
    ):
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok(root):
            return TenantPaths(root)


def _load_tenant_manifest(paths: TenantPaths) -> TenantManifest:
    """Reads and validates `tenant.json` or exits."""
    match read_json_file(paths.manifest_file):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(doc):
            pass
    match parse_tenant_manifest(doc):
        case Err(schema_err):
            err_console.print(f"❌ {schema_err}")
            raise typer.Exit(code=1)
        case Ok(manifest):
            return manifest


def _resolve_paths(
    workspace_dir: Path | None = None,
    tenant_name: str | None = None,
) -> tuple[Path, Path, Path, dict[str, str]]:
    """Returns `(tenant_root, env_file, docs_dir, env)` for the active tenant."""
    paths = _resolve_tenant(workspace_dir, tenant_name)
    return paths.root, paths.env_file, paths.docs_dir, load_env_file(paths.env_file)


@dataclass(frozen=True, slots=True)
class _Context:
    """Everything a tenant-scoped command needs, resolved once."""

    paths: TenantPaths
    manifest: TenantManifest
    env: dict[str, str]
    bindings: dict[ServiceId, InstanceBinding]
    profiles: list[ResolvedProfile]
    library: Library


def _try_context(
    paths: TenantPaths, *, instance: str = DEFAULT_INSTANCE
) -> Result[_Context]:
    """Pure-ish context build that reports instead of exiting (used by `sw validate`)."""
    match read_json_file(paths.manifest_file):
        case Err(read_err):
            return Err(StreamWeaverError(f"{TENANT_FILE}: {read_err}"))
        case Ok(doc):
            pass
    match parse_tenant_manifest(doc):
        case Err(schema_err):
            return Err(StreamWeaverError(str(schema_err)))
        case Ok(manifest):
            pass
    env = load_env_file(paths.env_file)
    bindings: dict[ServiceId, InstanceBinding] = {}
    for svc in ALL_SERVICES:
        match resolve_instance_binding(manifest, env, svc, instance):
            case Ok(binding):
                bindings[svc] = binding
            case Err():
                continue
    library = Library.load(manifest.library_roots(paths.root))
    if library.errors:
        return Err(StreamWeaverError("library: " + "; ".join(str(e) for e in library.errors)))
    match _load_profiles(paths, manifest, env):
        case Err(prof_err):
            return Err(StreamWeaverError(str(prof_err)))
        case Ok(profiles):
            pass
    return Ok(_Context(paths=paths, manifest=manifest, env=env, bindings=bindings, profiles=profiles, library=library))


def _resolve_context(
    workspace_dir: Path | None = None,
    tenant_name: str | None = None,
    instance: str = DEFAULT_INSTANCE,
) -> _Context:
    """Active tenant paths, validated manifest, `.env`, per-service bindings for `instance`, profiles, library."""
    paths = _resolve_tenant(workspace_dir, tenant_name)
    match _try_context(paths, instance=instance):
        case Err(ctx_err):
            err_console.print(f"❌ {ctx_err}")
            raise typer.Exit(code=1)
        case Ok(ctx):
            return ctx


def _service_doc_path(configs_dir: Path, service: ServiceId, instance: str = DEFAULT_INSTANCE) -> Path:
    """`<service>.json` for the default instance, `<service>.<instance>.json` otherwise."""
    return configs_dir / (f"{service}.json" if instance == DEFAULT_INSTANCE else f"{service}.{instance}.json")


def _resolve_profile(
    paths: TenantPaths, manifest: TenantManifest, env: Mapping[str, str], profile: Profile
) -> Result[ResolvedProfile]:
    """Binds a parsed profile's instance and login references against the tenant (pure lookups)."""
    inst_bindings: dict[ServiceId, InstanceBinding] = {}
    for svc, inst_name in profile.instances.items():
        match resolve_instance_binding(manifest, env, svc, inst_name):
            case Err(bind_err):
                return Err(
                    StreamWeaverError(f"profile `{profile.name}`: {bind_err}")
                )
            case Ok(binding):
                inst_bindings[svc] = binding
    login_bindings: dict[str, LoginBinding] = {}
    for platform, login_name in (
        ("nuvio", profile.nuvio.login if profile.nuvio else None),
        ("stremio", profile.stremio.login if profile.stremio else None),
    ):
        if not login_name:
            continue
        match resolve_login_binding(manifest, env, platform, login_name):
            case Err(login_err):
                return Err(StreamWeaverError(f"profile `{profile.name}`: {login_err}"))
            case Ok(lb):
                login_bindings[platform] = lb
    _ = paths
    return Ok(ResolvedProfile(profile=profile, instances=inst_bindings, logins=login_bindings))


def _load_profiles(
    paths: TenantPaths, manifest: TenantManifest, env: Mapping[str, str]
) -> Result[list[ResolvedProfile]]:
    """Reads every profile listed in tenant.json and resolves it; fails on the first problem."""
    out: list[ResolvedProfile] = []
    for name in manifest.profiles:
        pf = profile_file(paths, name)
        match read_json_file(pf):
            case Err(read_err):
                return Err(StreamWeaverError(f"profile `{name}`: {read_err}"))
            case Ok(doc):
                pass
        match parse_profile(doc):
            case Err(schema_err):
                return Err(StreamWeaverError(f"profile `{name}` ({paths.relative(pf)}): {schema_err}"))
            case Ok(profile):
                pass
        if profile.name != name:
            return Err(
                StreamWeaverError(
                    f"profile directory `{name}` holds a profile named `{profile.name}`; names must match"
                )
            )
        match _resolve_profile(paths, manifest, env, profile):
            case Err() as err:
                return err
            case Ok(resolved):
                out.append(resolved)
    return Ok(out)


def _resolve_service_targets(target: str) -> tuple[ServiceId, ...]:
    cleaned = target.strip().lower()
    if cleaned == "all":
        return ALL_SERVICES
    if cleaned in NUVIO_TARGET_ALIASES:
        return ()
    if cleaned in ("aiostreams", "aiometadata", "aiomanager"):
        return (cleaned,)  # type: ignore[return-value]
    err_console.print(
        f"❌ Unknown service target '{target}'. Choose from: all, aiostreams, aiometadata, aiomanager, "
        + ", ".join(NUVIO_TARGET_ALIASES)
    )
    raise typer.Exit(code=1)


def _read_scrubbed_aiomanager(paths: TenantPaths) -> JsonDict:
    return _load_optional_json(paths.doc("aiomanager"))


def _compile_all(ctx: _Context) -> list[CompiledProfile]:
    """Compiles every profile of the tenant (exits on the first compile error)."""
    match _compile_all_result(ctx):
        case Err(comp_err):
            err_console.print(f"❌ {comp_err}")
            raise typer.Exit(code=1)
        case Ok(compiled):
            return compiled


def _compile_all_result(ctx: _Context) -> Result[list[CompiledProfile]]:
    mgr_doc = _read_scrubbed_aiomanager(ctx.paths)
    out: list[CompiledProfile] = []
    for rp in ctx.profiles:
        match compile_profile(rp, ctx.library, mgr_doc):
            case Err(comp_err):
                return Err(StreamWeaverError(str(comp_err)))
            case Ok(cp):
                out.append(cp)
    return Ok(out)


def _scope_to_profile(
    ctx: _Context, compiled: list[CompiledProfile], profile: str | None
) -> tuple[_Context, list[CompiledProfile]]:
    """
    `--profile NAME`: narrows the compiled set to that profile and re-binds the aiometadata /
    aiostreams service bindings to the instances that profile uses (so `sw push aiometadata
    --profile dad` addresses Dad's instance without a separate `--instance`).
    """
    if profile is None:
        return ctx, compiled
    chosen = [cp for cp in compiled if cp.name == profile]
    if not chosen:
        known = ", ".join(cp.name for cp in compiled) or "none"
        err_console.print(f"❌ No profile named `{profile}` (known: {known})")
        raise typer.Exit(code=1)
    bindings = dict(ctx.bindings)
    for svc, binding in chosen[0].resolved.instances.items():
        bindings[svc] = binding
    scoped = _Context(
        paths=ctx.paths,
        manifest=ctx.manifest,
        env=ctx.env,
        bindings=bindings,
        profiles=ctx.profiles,  # managed accounts always consider every profile
        library=ctx.library,
    )
    return scoped, chosen


def _profiles_summary(ctx: _Context) -> list[JsonDict]:
    """Plain rows for the architecture `## Profiles` table."""
    rows: list[JsonDict] = []
    for rp in ctx.profiles:
        pr = rp.profile
        nuvio = None
        if pr.nuvio:
            target = (
                pr.nuvio.profile_id[:8] + "…"
                if pr.nuvio.profile_id
                else (f"name={pr.nuvio.profile_name}" if pr.nuvio.profile_name else "index 1")
            )
            nuvio = f"`{pr.nuvio.login}` → {target}"
        rows.append(
            {
                "name": pr.name,
                "displayName": pr.display_name,
                "aiostreams": pr.instances.get("aiostreams", "default"),
                "aiometadata": pr.instances.get("aiometadata", "default"),
                "nuvio": nuvio,
                "stremio": f"`{pr.stremio.login}`" if pr.stremio else None,
                "collections": [
                    c.get("ref")
                    if isinstance(c, dict) and "ref" in c
                    else (c.get("id") if isinstance(c, dict) else "?")
                    for c in pr.collections
                ],
                "extras": [a.name for a in pr.extra_addons],
            }
        )
    return rows


def _save_env_or_exit(env_path: Path, env: Mapping[str, str]) -> None:
    match save_env_updates(env_path, env):
        case Err(env_err):
            err_console.print(f"❌ {env_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass


def _persist_scrubbed_config(
    service: ServiceId,
    raw_config: JsonDict,
    env: dict[str, str],
    *,
    env_path: Path,
    configs_dir: Path,
    instance: str = DEFAULT_INSTANCE,
) -> Result[tuple[Path, dict[str, str]]]:
    scrubbed_cfg, updated_env = harvest_and_scrub(service, raw_config, env)
    match save_env_updates(env_path, updated_env):
        case Err() as err:
            return err
        case Ok():
            pass

    target_path = _service_doc_path(configs_dir, service, instance)
    match write_json_file(target_path, scrubbed_cfg):
        case Err() as err:
            return err
        case Ok(saved_path):
            return Ok((saved_path, updated_env))


def _render_diff_summary_table(outcomes: Sequence[ConfigDiffOutcome]) -> None:
    """Renders a compact Rich summary table when diffing multiple targets."""
    if len(outcomes) <= 1:
        return
    table = Table(
        title="Stream Weaver Drift Summary",
        box=box.ROUNDED,
        show_edge=True,
        header_style="bold",
    )
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("+Lines", justify="right")
    table.add_column("-Lines", justify="right")
    table.add_column("Changed Keys")
    for out in outcomes:
        status = "✅ In Sync" if out.in_sync else "⚠️  Drift"
        keys = ", ".join(out.changed_keys) if out.changed_keys else "—"
        table.add_row(
            out.target,
            status,
            f"+{out.added_lines}",
            f"-{out.removed_lines}",
            keys,
        )
    console.print(table)


def _print_diff_outcome(outcome: ConfigDiffOutcome, *, stat_only: bool = False) -> None:
    if outcome.in_sync:
        console.print(f"✅ [{outcome.target}] In sync with remote")
        return
    keys_str = ", ".join(outcome.changed_keys) or "root"
    console.print(
        f"⚠️  [{outcome.target}] Drift detected: +{outcome.added_lines} / -{outcome.removed_lines} lines "
        f"(changed keys: {keys_str})"
    )
    if not stat_only and outcome.unified_diff:
        console.print(outcome.unified_diff.rstrip("\n"))


def _extra_library_roots() -> tuple[Path, ...]:
    shared = bundled_shared_library_root()
    return (shared,) if shared is not None else ()


def _print_engine_step(step: str) -> None:
    if "Skipped" in step or "nothing to pull" in step or "nothing to push" in step:
        console.print(f"⏭️  {step}")
    elif "skipped (" in step.lower():
        console.print(f"⚠️  {step}")
    elif step.startswith("[warm]"):
        console.print(f"🔥 {step}")
    else:
        console.print(f"✅ {step}")


def _sanitize_service(
    service_id: ServiceId,
    env: dict[str, str],
    *,
    env_path: Path,
    configs_dir: Path,
    instance: str = DEFAULT_INSTANCE,
) -> Result[tuple[Path | None, dict[str, str]]]:
    cfg_path = _service_doc_path(configs_dir, service_id, instance)
    if not cfg_path.exists():
        return Ok((None, env))

    match read_json_file(cfg_path):
        case Err() as err:
            return err
        case Ok(raw_data):
            pass

    if (
        service_id == "aiomanager"
        and raw_data.get("isEncrypted")
        and raw_data.get("data")
        and (pw := env.get("AIOMANAGER_SYNC_PASSWORD", ""))
    ):
        match decrypt_envelope(raw_data, pw):
            case Err() as err:
                return err
            case Ok(decrypted):
                raw_data = decrypted

    match normalize_service_config(service_id, raw_data):
        case Err() as err:
            return err
        case Ok(normalized):
            return _persist_scrubbed_config(
                service_id, normalized, env, env_path=env_path, configs_dir=configs_dir, instance=instance
            )


def _load_optional_json(path: Path) -> JsonDict:
    if not path.exists():
        return {}
    match read_json_file(path):
        case Ok(data):
            return data
        case Err():
            return {}


def _sync_architecture_diagrams(
    paths: TenantPaths,
    compiled: Sequence[CompiledProfile] = (),
    *,
    check_only: bool = False,
    profiles_summary: Sequence[JsonDict] = (),
) -> Result[tuple[Path, ...]]:
    """Compiles LikeC4 (`architecture/topology.c4`) and `ARCHITECTURE.md` from the tenant's documents + compiled profiles."""
    merged_cols: list[JsonDict] = []
    seen: set[str] = set()
    badges: JsonDict = {}
    meta: JsonDict = {}
    settings: JsonDict = {}
    for cp in compiled:
        for col in cp.collections_doc:
            if str(col.get("id")) not in seen:
                seen.add(str(col.get("id")))
                merged_cols.append(col)
        badges = badges or (cp.badges or {})
        meta = meta or (cp.meta or {})
        settings = settings or (cp.settings or {})
    bundle = compile_architecture_bundle(
        aiomanager=_load_optional_json(paths.doc("aiomanager")),
        aiostreams=_load_optional_json(paths.doc("aiostreams")),
        aiometadata=_load_optional_json(paths.doc("aiometadata")),
        nuvio_collections={"version": "1.0.0", "collections": merged_cols} if merged_cols else {},
        nuvio_badges=badges,
        nuvio_meta=meta,
        nuvio_settings=settings,
        profiles=profiles_summary,
    )
    targets: tuple[tuple[Path, str], ...] = (
        (paths.architecture_dir / "topology.c4", bundle.likec4_dsl),
        (paths.architecture_dir / "likec4.config.json", bundle.likec4_config_json),
        (paths.architecture_md, bundle.architecture_markdown),
    )

    if check_only:
        stale: list[Path] = []
        for path, expected in targets:
            match read_text_file(path):
                case Ok(actual) if actual == expected:
                    pass
                case _:
                    stale.append(path)
        return Ok(tuple(stale))

    written: list[Path] = []
    for path, content in targets:
        match write_text_file(path, content):
            case Err() as err:
                return err
            case Ok(saved):
                written.append(saved)
    return Ok(tuple(written))


def _sync_diagrams_for_ctx(ctx: _Context, *, check_only: bool = False) -> Result[tuple[Path, ...]]:
    match _compile_all_result(ctx):
        case Err() as err:
            return err
        case Ok(compiled):
            return _sync_architecture_diagrams(
                ctx.paths, compiled, check_only=check_only, profiles_summary=_profiles_summary(ctx)
            )


# ============================================================================
# CLI Commands (`sw pull`, `sw push`, `sw sanitize`, `sw diagram`)
# ============================================================================


_TENANT_DOC_NAMES: tuple[str, ...] = ("aiostreams", "aiometadata", "aiomanager")


@config_app.command("init")
@app.command("init")
def cmd_config_init(
    directory: Annotated[
        Path | None,
        typer.Argument(help="Directory to scaffold as a tenant (default: CWD)"),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Tenant name (default: directory basename)"),
    ] = None,
    template_dir: Annotated[
        Path | None,
        typer.Option(
            "--from",
            help="Template tenant directory whose JSON documents are copied (or pass --starter for bundled starter)",
        ),
    ] = None,
    starter: Annotated[
        bool,
        typer.Option("--starter", help="Seed from the bundled starter template (works even in standalone wheel installs)"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite tenant.json, .env.example, .gitignore and template documents if present"),
    ] = False,
    debrid: Annotated[
        str | None,
        typer.Option("--debrid", help="Primary Debrid provider for aiostreams.json (torbox | realdebrid | alldebrid | premiumize)"),
    ] = None,
    packs: Annotated[
        str | None,
        typer.Option("--packs", help="Comma-separated AIOStreams addon packs to enable (e.g. core_debrid,core_subtitles)"),
    ] = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Scaffold a new tenant directory (tenant.json, .env, .gitignore, optional template documents)."""
    if directory is not None:
        target_dir = directory.expanduser().resolve()
    else:
        target_dir = _resolve_tenant(workspace_dir, tenant, require_marker=False).root
    paths = TenantPaths(target_dir)
    tenant_name = (name or target_dir.name).strip().lower()
    paths.architecture_dir.mkdir(parents=True, exist_ok=True)

    if starter and template_dir is None:
        template_dir = bundled_starter_template_root()
        if template_dir is None:
            err_console.print("❌ Bundled starter template not found.")
            raise typer.Exit(code=1)

    manifest_doc = default_tenant_manifest(tenant_name)
    if template_dir is not None and (template_dir.expanduser().resolve() / TENANT_FILE).is_file():
        tpl_root = template_dir.expanduser().resolve()
        match read_json_file(tpl_root / TENANT_FILE):
            case Ok(tpl_manifest):
                manifest_doc = {**tpl_manifest, "name": tenant_name}
                # Re-anchor the template's library roots relative to the new tenant so recipes stay reachable.
                tpl_libs = tpl_manifest.get("libraries") or []
                roots: list[str] = ["library"]
                for rel in tpl_libs if isinstance(tpl_libs, list) else []:
                    abs_root = (tpl_root / str(rel)).resolve()
                    if abs_root == (tpl_root / "library").resolve():
                        continue
                    if not abs_root.is_dir():
                        fallback_lib = bundled_shared_library_root()
                        if fallback_lib is not None:
                            abs_root = fallback_lib.resolve()
                    if abs_root.is_dir():
                        roots.append(os.path.relpath(abs_root, target_dir))
                if len(roots) == 1 and (fallback_lib := bundled_shared_library_root()) is not None:
                    roots.append(os.path.relpath(fallback_lib.resolve(), target_dir))
                manifest_doc["libraries"] = roots
            case Err():
                pass
    match parse_tenant_manifest(manifest_doc):
        case Err(schema_err):
            err_console.print(f"❌ {schema_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass

    created: list[str] = []
    preserved: list[str] = []

    def _emit(rel_name: str, writer: Any, allow_force: bool) -> None:
        dest = target_dir / rel_name
        if dest.exists() and not allow_force:
            preserved.append(rel_name)
            return
        match writer(dest):
            case Err(write_err):
                err_console.print(f"❌ {write_err}")
                raise typer.Exit(code=1)
            case Ok():
                created.append(rel_name)

    env_seed_text = _INIT_ENV_TEMPLATE
    if template_dir is not None:
        tpl_env_example = template_dir.expanduser().resolve() / ".env.example"
        if tpl_env_example.is_file():
            match read_text_file(tpl_env_example):
                case Ok(tpl_env_text):
                    env_seed_text = tpl_env_text
                case Err():
                    pass

    _emit(TENANT_FILE, lambda d: write_json_file(d, manifest_doc), force)
    _emit(".gitignore", lambda d: write_text_file(d, _INIT_GITIGNORE_TEMPLATE), force)
    _emit(".env.example", lambda d: write_text_file(d, env_seed_text), force)
    _emit(".env", lambda d: write_text_file(d, env_seed_text), False)

    if template_dir is not None:
        src_root = template_dir.expanduser().resolve()
        if not src_root.is_dir():
            err_console.print(f"❌ Template directory not found: {src_root}")
            raise typer.Exit(code=1)
        for doc_name in _TENANT_DOC_NAMES:
            src_doc = src_root / f"{doc_name}.json"
            if not src_doc.is_file():
                continue
            match read_text_file(src_doc):
                case Err(read_err):
                    err_console.print(f"❌ {read_err}")
                    raise typer.Exit(code=1)
                case Ok(text):
                    _emit(f"{doc_name}.json", lambda d, t=text: write_text_file(d, t), force)
        for sub in ("profiles", "library"):
            src_sub = src_root / sub
            if not src_sub.is_dir():
                continue
            for src_file in sorted(p for p in src_sub.rglob("*.json") if p.is_file()):
                rel = src_file.relative_to(src_root)
                match read_text_file(src_file):
                    case Err(read_err):
                        err_console.print(f"❌ {read_err}")
                        raise typer.Exit(code=1)
                    case Ok(text):
                        _emit(str(rel), lambda d, t=text: write_text_file(d, t), force)

    if debrid is not None or packs is not None:
        pack_list = [p.strip() for p in packs.split(",") if p.strip()] if packs is not None else None
        match apply_debrid_and_packs(paths, debrid=debrid, enabled_packs=pack_list):
            case Err(pack_err):
                err_console.print(f"❌ {pack_err}")
                raise typer.Exit(code=1)
            case Ok():
                pass

    match _try_context(paths):
        case Ok(init_ctx):
            diag_res = _sync_diagrams_for_ctx(init_ctx)
        case Err():
            diag_res = _sync_architecture_diagrams(paths, ())
    match diag_res:
        case Err(diag_err):
            err_console.print(f"❌ {diag_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass

    console.print(f"🧶 Initialized tenant `{tenant_name}` at {target_dir}")
    if created:
        console.print(f"   ✅ Created:   {', '.join(created)}, architecture/, ARCHITECTURE.md")
    if preserved:
        console.print(f"   ℹ️  Preserved: {', '.join(preserved)}")
    console.print("   💡 Next: fill in instance URLs & credentials in .env, then run `sw pull` or `sw push --create`.")


def _validate_tenant(paths: TenantPaths) -> list[str]:
    """Offline checks for one tenant. Returns a list of human-readable failures (empty = OK)."""
    failures: list[str] = []

    match _try_context(paths):
        case Err(ctx_err):
            failures.append(str(ctx_err))
            # still try the raw document checks below with a minimal manifest view
            manifest: TenantManifest | None = None
            compiled: list[CompiledProfile] = []
            ctx: _Context | None = None
        case Ok(ok_ctx):
            ctx = ok_ctx
            manifest = ok_ctx.manifest
            match _compile_all_result(ok_ctx):
                case Err(comp_err):
                    failures.append(str(comp_err))
                    compiled = []
                case Ok(cps):
                    compiled = cps

    for svc_id in ALL_SERVICES:
        instance_names = sorted(manifest.instances.get(svc_id, {})) if manifest else [DEFAULT_INSTANCE]
        for inst in instance_names or [DEFAULT_INSTANCE]:
            doc_path = _service_doc_path(paths.docs_dir, svc_id, inst)
            label = paths.relative(doc_path)
            if not doc_path.exists():
                continue
            match read_json_file(doc_path):
                case Err(read_err):
                    failures.append(f"{label}: {read_err}")
                    continue
                case Ok(raw_doc):
                    pass
            if svc_id == "aiomanager" and raw_doc.get("isEncrypted"):
                failures.append(f"{label}: still an encrypted envelope; run `sw sanitize aiomanager`")
                continue
            match normalize_service_config(svc_id, raw_doc):
                case Err(norm_err):
                    failures.append(f"{label}: {norm_err}")
                case Ok():
                    pass
            if svc_id == "aiometadata" and compiled:
                match derive_instance_catalogs(inst, raw_doc, compiled):
                    case Err(derive_err):
                        failures.append(str(derive_err))
                    case Ok(plan) if plan.changed:
                        failures.append(
                            f"{label}: catalogs differ from the form derived from profile(s) {', '.join(plan.profiles)} "
                            "(run `sw push aiometadata` to apply, or adjust collections / aiometadata.extraCatalogs)"
                        )
                    case Ok():
                        pass

    legacy = [n for n in ("nuvio_collections", "nuvio_badges", "nuvio_meta", "nuvio_settings") if paths.doc(n).exists()]
    if legacy:
        failures.append(
            f"legacy root document(s) {', '.join(n + '.json' for n in legacy)} found; "
            "run `sw migrate compose` to lift them into library recipes + profile refs"
        )

    if ctx is not None and ctx.profiles:
        mgr_path = paths.doc("aiomanager")
        if mgr_path.exists():
            match read_json_file(mgr_path):
                case Ok(mgr_doc) if not mgr_doc.get("isEncrypted"):
                    outcome = apply_managed_accounts(mgr_doc, ctx.manifest.name, ctx.profiles)
                    if outcome.changed:
                        failures.append(
                            "aiomanager.json: accounts[] differs from the form generated by profiles/ "
                            "(edit profile.json, or run `sw push aiomanager` to regenerate)"
                        )
                case _:
                    pass

    match _sync_architecture_diagrams(
        paths, compiled, check_only=True, profiles_summary=_profiles_summary(ctx) if ctx else ()
    ):
        case Err(diag_err):
            failures.append(f"diagram: {diag_err}")
        case Ok(stale) if stale:
            failures.append(
                "diagram out of sync: " + ", ".join(paths.relative(p) for p in stale) + " (run `sw diagram`)"
            )
        case Ok():
            pass

    return failures


# ============================================================================
# `sw profile` — people (identity bindings) of the active tenant
# ============================================================================

profile_app = typer.Typer(help="Manage profiles: one person's instances, logins, Nuvio profile and extra addons.")
app.add_typer(profile_app, name="profile")


def _add_profile_to_manifest(paths: TenantPaths, name: str) -> None:
    match read_json_file(paths.manifest_file):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(manifest_doc):
            pass
    profiles = manifest_doc.setdefault("profiles", [])
    if not isinstance(profiles, list):
        err_console.print("❌ tenant.json: `profiles` must be a list")
        raise typer.Exit(code=1)
    if name not in profiles:
        profiles.append(name)
        _write_tenant_manifest(paths, manifest_doc)


def _write_profile(paths: TenantPaths, profile: Profile, *, force: bool) -> Path:
    pf = profile_file(paths, profile.name)
    if pf.exists() and not force:
        err_console.print(f"❌ {paths.relative(pf)} already exists (use --force to overwrite)")
        raise typer.Exit(code=1)
    match write_json_file(pf, profile_to_doc(profile)):
        case Err(write_err):
            err_console.print(f"❌ {write_err}")
            raise typer.Exit(code=1)
        case Ok(saved):
            return saved


@profile_app.command("list")
def cmd_profile_list(
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Show every profile with its instances, logins, Nuvio profile and extra addons."""
    ctx = _resolve_context(workspace_dir, tenant)
    table = Table(title=f"Profiles — tenant `{ctx.manifest.name}`", box=box.ROUNDED, header_style="bold")
    for col in ("Profile", "Display", "AIOStreams", "AIOMetadata", "Nuvio", "Stremio", "Extras", "Account id"):
        table.add_column(col, overflow="fold")
    for rp in ctx.profiles:
        p = rp.profile
        nuvio = "—"
        if p.nuvio:
            target = p.nuvio.profile_id or (f"name={p.nuvio.profile_name}" if p.nuvio.profile_name else "index 1")
            nuvio = f"{p.nuvio.login} · {target} · {','.join(p.nuvio.platforms)}"
        table.add_row(
            p.name,
            p.display_name,
            p.instances.get("aiostreams", "default"),
            p.instances.get("aiometadata", "default"),
            nuvio,
            p.stremio.login if p.stremio else "—",
            ", ".join(a.name for a in p.extra_addons) or "—",
            p.account_id or "(stable id)",
        )
    console.print(table)
    if not ctx.profiles:
        console.print("ℹ️  No profiles yet — `sw profile import <account-name> --name <profile>` or `sw profile new`.")


@profile_app.command("import")
def cmd_profile_import(
    account: Annotated[str, typer.Argument(help="Existing AIOManager account name (as shown in aiomanager.json)")],
    name: Annotated[str, typer.Option("--name", "-n", help="Profile name to create (default: slug of the account name)")] = "",
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing profile.json")] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Derive a profile from an existing AIOManager account (migration path for already-running setups)."""
    paths = _resolve_tenant(workspace_dir, tenant)
    manifest = _load_tenant_manifest(paths)
    env = load_env_file(paths.env_file)
    match read_json_file(paths.doc("aiomanager")):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(mgr_doc):
            pass
    accounts = [a for a in (mgr_doc.get("accounts") or []) if isinstance(a, dict)]
    target = next((a for a in accounts if str(a.get("name") or "").lower() == account.strip().lower()), None)
    if target is None:
        known = ", ".join(str(a.get("name")) for a in accounts) or "none"
        err_console.print(f"❌ No AIOManager account named `{account}` (known: {known})")
        raise typer.Exit(code=1)

    profile_name = (name or account).strip().lower().replace(" ", "-")
    inst_bindings: dict[ServiceId, list[InstanceBinding]] = {}
    for svc in ALL_SERVICES:
        for inst_name in manifest.instances.get(svc, {}):
            match resolve_instance_binding(manifest, env, svc, inst_name):
                case Ok(b):
                    inst_bindings.setdefault(svc, []).append(b)
                case Err():
                    continue
    login_bindings: dict[str, list[LoginBinding]] = {}
    for platform in SUPPORTED_PLATFORMS:
        for login_name in manifest.logins.get(platform, {}):
            match resolve_login_binding(manifest, env, platform, login_name):
                case Ok(lb):
                    login_bindings.setdefault(platform, []).append(lb)
                case Err():
                    continue

    profile = derive_profile_from_account(
        target, profile_name=profile_name, instance_bindings=inst_bindings, login_bindings=login_bindings
    )
    match parse_profile(profile_to_doc(profile)):
        case Err(schema_err):
            err_console.print(f"❌ derived profile is invalid: {schema_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass
    saved = _write_profile(paths, profile, force=force)
    _add_profile_to_manifest(paths, profile_name)
    console.print(f"✅ Imported account `{target.get('name')}` → {paths.relative(saved)}")
    console.print(
        f"   instances: {dict(profile.instances)} · nuvio: {profile.nuvio.login if profile.nuvio else '—'} · "
        f"stremio: {profile.stremio.login if profile.stremio else '—'} · extras: {[a.name for a in profile.extra_addons]}"
    )
    unmatched = [
        c.get("platform") for c in (target.get("connections") or []) if isinstance(c, dict)
    ]
    for plat in unmatched:
        if (plat == "nuvio" and profile.nuvio is None) or (plat == "stremio" and profile.stremio is None):
            console.print(
                f"   ⚠️  {plat} connection could not be matched to a registered login — "
                f"run `sw login adopt {plat} --login <name> --prefix <PREFIX>` and re-import"
            )


@profile_app.command("nuvio-list")
def cmd_profile_nuvio_list(
    login: Annotated[str, typer.Option("--login", "-l", help="Nuvio login name")],
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """List the Nuvio profiles that exist on a login (names + indexes) — use with `sw profile new --nuvio-profile`."""
    ctx = _resolve_context(workspace_dir, tenant)
    match resolve_login_binding(ctx.manifest, ctx.env, "nuvio", login):
        case Err(login_err):
            err_console.print(f"❌ {login_err}")
            raise typer.Exit(code=1)
        case Ok(lb):
            pass
    with HttpxClient() as http_client:
        pool = NuvioSessionPool(http_client, resolve_nuvio_anon_key(ctx.env))
        match pool.acquire(
            access_token=lb.credentials.get("accessToken", ""),
            refresh_token=lb.credentials.get("refreshToken", ""),
            env=ctx.env,
        ):
            case Err(sess_err):
                err_console.print(f"❌ {sess_err}")
                raise typer.Exit(code=1)
            case Ok(session):
                pass
    if session.refreshed_secrets:
        _save_env_or_exit(ctx.paths.env_file, {**ctx.env, **session.refreshed_secrets})
    table = Table(title=f"Nuvio profiles — login `{login}`", box=box.ROUNDED, header_style="bold")
    for col in ("Index", "Name", "Id"):
        table.add_column(col, overflow="fold")
    for p in session.profiles:
        table.add_row(
            str(p.get("profile_index") or p.get("profileIndex") or "?"),
            str(p.get("name") or p.get("profile_name") or "?"),
            str(p.get("id") or ""),
        )
    console.print(table)


@profile_app.command("new")
def cmd_profile_new(
    name: Annotated[str, typer.Argument(help="Profile name (e.g. dad)")],
    display_name: Annotated[str | None, typer.Option("--display-name", help="Account / Nuvio display name (default: capitalised name)")] = None,
    aiometadata: Annotated[str, typer.Option("--aiometadata", help="AIOMetadata instance name")] = DEFAULT_INSTANCE,
    aiostreams: Annotated[str, typer.Option("--aiostreams", help="AIOStreams instance name")] = DEFAULT_INSTANCE,
    nuvio_login: Annotated[str | None, typer.Option("--nuvio-login", help="Nuvio login name this person signs in with")] = None,
    nuvio_profile: Annotated[
        str | None,
        typer.Option("--nuvio-profile", help="Nuvio profile *name* on that login (resolved at push); default: display name"),
    ] = None,
    nuvio_profile_id: Annotated[str | None, typer.Option("--nuvio-profile-id", help="Nuvio profile UUID (skips name matching)")] = None,
    platforms: Annotated[str, typer.Option("--platforms", help="Comma-separated Nuvio platforms")] = "tv,desktop,mobile",
    stremio_login: Annotated[str | None, typer.Option("--stremio-login", help="Stremio login name, if this person uses Stremio")] = None,
    extra_from: Annotated[
        str | None,
        typer.Option("--extras-from", help="Copy `addons.extra` from this existing profile"),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing profile.json")] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Create a profile: who they are, which instances they use, how they sign in."""
    ctx = _resolve_context(workspace_dir, tenant)
    prof_name = name.strip().lower()
    shown = (display_name or prof_name.capitalize()).strip()
    extras: tuple[ExtraAddon, ...] = ()
    if extra_from:
        src = next((rp.profile for rp in ctx.profiles if rp.profile.name == extra_from), None)
        if src is None:
            err_console.print(f"❌ --extras-from: no profile named `{extra_from}`")
            raise typer.Exit(code=1)
        extras = src.extra_addons
    nuvio_spec = (
        NuvioClientSpec(
            login=nuvio_login,
            profile_id=nuvio_profile_id,
            profile_name=None if nuvio_profile_id else (nuvio_profile or shown),
            platforms=tuple(p.strip() for p in platforms.split(",") if p.strip()),
        )
        if nuvio_login
        else None
    )
    profile = Profile(
        name=prof_name,
        display_name=shown,
        instances={"aiostreams": aiostreams, "aiometadata": aiometadata},
        nuvio=nuvio_spec,
        stremio=StremioClientSpec(login=stremio_login) if stremio_login else None,
        extra_addons=extras,
    )
    match parse_profile(profile_to_doc(profile)):
        case Err(schema_err):
            err_console.print(f"❌ {schema_err}")
            raise typer.Exit(code=1)
        case Ok(parsed):
            pass
    match _resolve_profile(ctx.paths, ctx.manifest, ctx.env, parsed):
        case Err(res_err):
            err_console.print(f"❌ {res_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass
    saved = _write_profile(ctx.paths, parsed, force=force)
    _add_profile_to_manifest(ctx.paths, prof_name)
    console.print(f"✅ Created profile `{prof_name}` → {ctx.paths.relative(saved)}")
    console.print("   💡 Next: `sw push aiomanager` creates/updates its AIOManager account and syncs its Nuvio profile.")




# ============================================================================
# `sw library` — recipes available to the active tenant
# ============================================================================

library_app = typer.Typer(help="Browse and render the recipe library (tenant-local roots first, then shared).")
app.add_typer(library_app, name="library")


@library_app.command("list")
def cmd_library_list(
    kind: Annotated[str | None, typer.Argument(help="collection | folder | catalog | badges | meta | settings")] = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """List recipes with their source root and parameters."""
    ctx = _resolve_context(workspace_dir, tenant)
    table = Table(title=f"Library — tenant `{ctx.manifest.name}`", box=box.ROUNDED, header_style="bold")
    for col in ("Kind", "Id", "Params", "Root"):
        table.add_column(col, overflow="fold")
    for (k, rid), recipe in sorted(ctx.library.recipes.items()):
        if kind and k != kind:
            continue
        root = ctx.library.root_of(k, rid)
        params = ", ".join(
            f"{p.name}:{p.type}" + (f"={p.default!r}" if p.has_default else "") for p in recipe.params.params
        )
        table.add_row(k, rid, params or "—", str(root) if root else "?")
    console.print(table)
    if not ctx.library.roots:
        console.print("ℹ️  No library roots exist yet (tenant.json `libraries`). `sw migrate compose` creates them.")


@library_app.command("show")
def cmd_library_show(
    kind: Annotated[str, typer.Argument(help="collection | folder | catalog | badges | meta | settings")],
    rid: Annotated[str, typer.Argument(help="Recipe id")],
    params: Annotated[
        list[str] | None,
        typer.Option("--with", "-w", help="Param binding key=value (JSON values allowed); repeatable"),
    ] = None,
    raw: Annotated[bool, typer.Option("--raw", help="Print the recipe file instead of rendering it")] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Render a recipe with the given params (or print its source)."""
    ctx = _resolve_context(workspace_dir, tenant)
    if raw:
        match ctx.library.get(kind, rid):
            case Err(lib_err):
                err_console.print(f"❌ {lib_err}")
                raise typer.Exit(code=1)
            case Ok(recipe):
                console.print(json.dumps(recipe_to_doc(recipe), indent=2, ensure_ascii=False))
                return
    bound: dict[str, Any] = {}
    for item in params or []:
        key, sep, val = item.partition("=")
        if not sep:
            err_console.print(f"❌ --with expects key=value, got {item!r}")
            raise typer.Exit(code=1)
        try:
            bound[key.strip()] = json.loads(val)
        except ValueError:
            bound[key.strip()] = val
    match ctx.library.render(kind, rid, bound):
        case Err(render_err):
            err_console.print(f"❌ {render_err}")
            raise typer.Exit(code=1)
        case Ok(rendered):
            console.print(json.dumps(rendered, indent=2, ensure_ascii=False))


# ============================================================================
# `sw migrate` — schema / layout upgrades
# ============================================================================

migrate_app = typer.Typer(help="One-shot migrations between stream-weaver layouts and schema versions.")
app.add_typer(migrate_app, name="migrate")


def _write_recipe(recipe: Recipe, *, force: bool) -> Result[Path]:
    if recipe.source.exists() and not force:
        match read_json_file(recipe.source):
            case Ok(existing) if existing == recipe_to_doc(recipe):
                return Ok(recipe.source)
            case _:
                return Err(
                    StreamWeaverError(
                        f"{recipe.source} already exists with different content (use --force to overwrite)"
                    )
                )
    return write_json_file(recipe.source, recipe_to_doc(recipe))


@migrate_app.command("compose")
def cmd_migrate_compose(
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-p", help="Profile that owns the root Nuvio documents (default: the only profile)"),
    ] = None,
    badges_id: Annotated[str, typer.Option("--badges-id", help="Recipe id for the badges document")] = "default",
    meta_id: Annotated[str, typer.Option("--meta-id", help="Recipe id for the meta document")] = "default",
    settings_id: Annotated[str, typer.Option("--settings-id", help="Recipe id for the settings document")] = "default",
    force: Annotated[bool, typer.Option("--force", help="Overwrite recipes that already exist with different content")] = False,
    keep_root_docs: Annotated[bool, typer.Option("--keep-root-docs", help="Do not delete the lifted nuvio_*.json files")] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """
    Lift root `nuvio_*.json` documents into library recipes + profile refs, 1:1 and lossless.

    Writes nothing unless compiling the resulting refs reproduces the original documents exactly.
    """
    ctx = _resolve_context(workspace_dir, tenant)
    paths = ctx.paths
    if not ctx.profiles:
        err_console.print("❌ No profiles; run `sw profile import <account>` first")
        raise typer.Exit(code=1)
    if profile is None and len(ctx.profiles) != 1:
        err_console.print("❌ Several profiles exist; pass --profile <name>")
        raise typer.Exit(code=1)
    owner = next((rp for rp in ctx.profiles if profile is None or rp.profile.name == profile), None)
    if owner is None:
        err_console.print(f"❌ No profile named `{profile}`")
        raise typer.Exit(code=1)
    if owner.profile.has_taste:
        err_console.print(f"❌ profile `{owner.profile.name}` already has collections/badges/meta/settings; nothing to lift")
        raise typer.Exit(code=1)

    def _opt(name: str) -> JsonDict | None:
        p = paths.doc(name)
        return _load_optional_json(p) if p.exists() else None

    cols_doc = _opt("nuvio_collections")
    badges_doc, meta_doc, settings_doc = _opt("nuvio_badges"), _opt("nuvio_meta"), _opt("nuvio_settings")
    if cols_doc is None and badges_doc is None and meta_doc is None and settings_doc is None:
        console.print("ℹ️  No root nuvio_*.json documents to lift.")
        return

    mgr_doc = _read_scrubbed_aiomanager(paths)
    meta_binding = owner.instances.get("aiometadata")
    meta_doc_path = _service_doc_path(paths.docs_dir, "aiometadata", meta_binding.name if meta_binding else DEFAULT_INSTANCE)
    aiometadata_doc = _load_optional_json(meta_doc_path)
    aiostreams_doc = _load_optional_json(
        _service_doc_path(paths.docs_dir, "aiostreams", owner.instances["aiostreams"].name if owner.instances.get("aiostreams") else DEFAULT_INSTANCE)
    )
    resolver = build_addon_resolver(owner, mgr_doc)

    roots = ctx.manifest.libraries
    tenant_root = (paths.root / roots[0]).resolve()
    shared_root = (paths.root / roots[-1]).resolve() if len(roots) > 1 else tenant_root
    outcome = extract_composition(
        collections=[c for c in (cols_doc or {}).get("collections", []) if isinstance(c, dict)],
        badges=badges_doc,
        meta=meta_doc,
        settings=settings_doc,
        aiometadata_catalogs=[c for c in aiometadata_doc.get("catalogs", []) if isinstance(c, dict)],
        resolver=resolver,
        custom_presets=custom_preset_ids(aiostreams_doc),
        shared_root=shared_root,
        tenant_root=tenant_root,
        doc_recipe_ids={"badges": badges_id, "meta": meta_id, "settings": settings_id},
    )

    # --- golden gate: compile the would-be profile against an in-memory library and compare
    in_memory = Library(
        roots=(tenant_root, shared_root),
        recipes={(r.kind, r.id): r for r in (*outcome.tenant_private, *outcome.shared)},
    )
    candidate = replace(
        owner.profile,
        collections=tuple(outcome.collections),
        badges=outcome.badges,
        meta=outcome.meta,
        settings=outcome.settings,
        extra_catalogs=outcome.extra_catalogs,
    )
    match compile_profile(replace(owner, profile=candidate), in_memory, mgr_doc):
        case Err(comp_err):
            err_console.print(f"❌ extraction does not compile: {comp_err}")
            raise typer.Exit(code=1)
        case Ok(cp):
            pass
    problems: list[str] = []
    if cols_doc is not None and cp.collections_doc != list(cols_doc.get("collections", [])):
        problems.append("collections")
    for kind, original, compiled_doc in (("badges", badges_doc, cp.badges), ("meta", meta_doc, cp.meta), ("settings", settings_doc, cp.settings)):
        if original is not None and compiled_doc != original:
            problems.append(kind)
    match derive_instance_catalogs(meta_binding.name if meta_binding else DEFAULT_INSTANCE, aiometadata_doc, [cp]):
        case Ok(plan) if plan.changed:
            problems.append("aiometadata catalogs")
        case Err(derive_err):
            problems.append(f"aiometadata catalogs ({derive_err})")
        case Ok():
            pass
    if problems:
        err_console.print(f"❌ Golden check failed — compiling the extracted recipes does not reproduce: {', '.join(problems)}")
        raise typer.Exit(code=1)

    # --- write
    written = 0
    for recipe in (*outcome.shared, *outcome.tenant_private):
        match _write_recipe(recipe, force=force):
            case Err(w_err):
                err_console.print(f"❌ {w_err}")
                raise typer.Exit(code=1)
            case Ok():
                written += 1
    match write_json_file(profile_file(paths, candidate.name), profile_to_doc(candidate)):
        case Err(w_err):
            err_console.print(f"❌ {w_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass
    removed: list[str] = []
    if not keep_root_docs:
        for name in ("nuvio_collections", "nuvio_badges", "nuvio_meta", "nuvio_settings"):
            p = paths.doc(name)
            if p.exists():
                p.unlink()
                removed.append(p.name)

    console.print(
        f"✅ Lifted {len(outcome.shared)} shared + {len(outcome.tenant_private)} tenant-private recipe(s) "
        f"({written} written) → profile `{candidate.name}` now composes {len(outcome.collections)} collection ref(s)"
    )
    for note in outcome.notes:
        console.print(f"   • {note}")
    if removed:
        console.print(f"   🗑️  removed root documents: {', '.join(removed)}")
    match _try_context(paths):
        case Ok(new_ctx):
            match _sync_diagrams_for_ctx(new_ctx):
                case Ok():
                    console.print("🗺️  [diagram] Updated architecture/topology.c4 & ARCHITECTURE.md")
                case Err(diag_err):
                    err_console.print(f"⚠️  [diagram] {diag_err}")
        case Err(ctx_err):
            err_console.print(f"⚠️  {ctx_err}")


# ============================================================================
# `sw login` — named client logins (tenant.json `logins` registry)
# ============================================================================

login_app = typer.Typer(help="Sign in to client platforms (Nuvio, Stremio) and store tokens per named login.")
app.add_typer(login_app, name="login")


def _register_login(paths: TenantPaths, platform: str, name: str, prefix: str) -> None:
    """Idempotently records `logins[platform][name] = prefix` in tenant.json."""
    match read_json_file(paths.manifest_file):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(manifest_doc):
            pass
    logins = manifest_doc.setdefault("logins", {})
    if not isinstance(logins, dict):
        err_console.print("❌ tenant.json: `logins` must be an object")
        raise typer.Exit(code=1)
    registry = logins.setdefault(platform, {})
    existing = registry.get(name)
    if existing is not None and existing != prefix:
        err_console.print(f"❌ login `{platform}/{name}` is already registered with prefix {existing}")
        raise typer.Exit(code=1)
    if existing is None:
        registry[name] = prefix
        _write_tenant_manifest(paths, manifest_doc)


def _store_login_secrets(paths: TenantPaths, updates: Mapping[str, str]) -> None:
    match save_env_updates(paths.env_file, updates):
        case Err(write_err):
            err_console.print(f"❌ {write_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass
    match scaffold_env_keys(paths.env_example_file, list(updates)):
        case Err(write_err):
            err_console.print(f"❌ {write_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass


@login_app.command("list")
def cmd_login_list(
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Show registered logins and whether .env holds credentials for them."""
    paths = _resolve_tenant(workspace_dir, tenant)
    manifest = _load_tenant_manifest(paths)
    env = load_env_file(paths.env_file)
    table = Table(title=f"Logins — tenant `{manifest.name}`", box=box.ROUNDED, header_style="bold")
    for col in ("Platform", "Login", "Env prefix", "Email", "Ready"):
        table.add_column(col, overflow="fold")
    for platform in SUPPORTED_PLATFORMS:
        for name in sorted(manifest.logins.get(platform, {})):
            match resolve_login_binding(manifest, env, platform, name):
                case Err(bind_err):
                    err_console.print(f"❌ {bind_err}")
                    raise typer.Exit(code=1)
                case Ok(b):
                    pass
            table.add_row(platform, name, b.prefix, b.credentials.get("email") or "—", "✅" if b.configured() else "❌")
    console.print(table)


@login_app.command("nuvio")
def cmd_login_nuvio(
    login: Annotated[str, typer.Option("--login", "-l", help="Login name (e.g. don, gf)")],
    email: Annotated[str | None, typer.Option("--email", help="Nuvio account email (prompted if omitted)")] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Nuvio account password (prompted, hidden, if omitted)"),
    ] = None,
    prefix: Annotated[
        str | None,
        typer.Option("--prefix", help="Env prefix (default: NUVIO_<LOGIN>; use NUVIO to adopt legacy SECRET_NUVIO_* keys)"),
    ] = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Sign in to Nuvio (Supabase password grant) and store tokens as SECRET_<PREFIX>_* for this login."""
    paths = _resolve_tenant(workspace_dir, tenant)
    manifest = _load_tenant_manifest(paths)
    env = load_env_file(paths.env_file)
    anon_key = resolve_nuvio_anon_key(env)

    login_name = login.strip().lower()
    registered_prefix = manifest.logins.get("nuvio", {}).get(login_name)
    env_prefix = (prefix or registered_prefix or f"NUVIO_{login_name.upper().replace('-', '_')}").strip()
    email_val = (email or typer.prompt("Nuvio email")).strip()
    password_val = password or typer.prompt("Nuvio password", hide_input=True)

    with HttpxClient() as http_client:
        match password_grant(http_client, anon_key, email_val, password_val):
            case Err(auth_err):
                err_console.print(f"❌ {auth_err}")
                raise typer.Exit(code=1)
            case Ok((access_token, refresh_token)):
                pass

    _register_login(paths, "nuvio", login_name, env_prefix)
    _store_login_secrets(
        paths,
        {
            login_env_key(env_prefix, "ACCESS_TOKEN"): access_token,
            login_env_key(env_prefix, "REFRESH_TOKEN"): refresh_token,
            login_env_key(env_prefix, "EMAIL"): email_val,
        },
    )
    console.print(f"✅ Signed in to Nuvio as {email_val}; stored under SECRET_{env_prefix}_* for login `{login_name}`")


@login_app.command("stremio")
def cmd_login_stremio(
    login: Annotated[str, typer.Option("--login", "-l", help="Login name (e.g. don)")],
    email: Annotated[str | None, typer.Option("--email", help="Stremio account email (prompted if omitted)")] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Stremio account password (prompted, hidden, if omitted)"),
    ] = None,
    prefix: Annotated[
        str | None,
        typer.Option("--prefix", help="Env prefix (default: STREMIO_<LOGIN>; use STREMIO to adopt legacy SECRET_STREMIO_* keys)"),
    ] = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Sign in to Stremio and store its authKey as SECRET_<PREFIX>_AUTH_KEY for this login."""
    paths = _resolve_tenant(workspace_dir, tenant)
    manifest = _load_tenant_manifest(paths)
    login_name = login.strip().lower()
    registered_prefix = manifest.logins.get("stremio", {}).get(login_name)
    env_prefix = (prefix or registered_prefix or f"STREMIO_{login_name.upper().replace('-', '_')}").strip()
    email_val = (email or typer.prompt("Stremio email")).strip()
    password_val = password or typer.prompt("Stremio password", hide_input=True)

    with HttpxClient() as http_client:
        match http_client.request_json(
            "POST",
            "https://api.strem.io/api/login",
            json_body={"type": "Login", "email": email_val, "password": password_val, "facebook": False},
        ):
            case Err(net_err):
                err_console.print(f"❌ {net_err}")
                raise typer.Exit(code=1)
            case Ok(resp):
                pass
    result = resp.data.get("result") if isinstance(resp.data, dict) else None
    auth_key = str(result.get("authKey") or "") if isinstance(result, dict) else ""
    if resp.status_code != 200 or not auth_key:
        detail = resp.data.get("error") if isinstance(resp.data, dict) else resp.text
        err_console.print(f"❌ Stremio sign-in failed (HTTP {resp.status_code}): {str(detail)[:200]}")
        raise typer.Exit(code=1)

    _register_login(paths, "stremio", login_name, env_prefix)
    _store_login_secrets(
        paths,
        {login_env_key(env_prefix, "AUTH_KEY"): auth_key, login_env_key(env_prefix, "EMAIL"): email_val},
    )
    console.print(f"✅ Signed in to Stremio as {email_val}; stored under SECRET_{env_prefix}_* for login `{login_name}`")


@login_app.command("adopt")
def cmd_login_adopt(
    platform: Annotated[str, typer.Argument(help="nuvio | stremio")],
    login: Annotated[str, typer.Option("--login", "-l", help="Login name to register")],
    prefix: Annotated[str, typer.Option("--prefix", help="Existing env prefix whose SECRET_<PREFIX>_* keys hold the tokens")],
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Register an existing set of SECRET_<PREFIX>_* credentials (e.g. legacy SECRET_NUVIO_*) as a named login."""
    plat = platform.strip().lower()
    if plat not in SUPPORTED_PLATFORMS:
        err_console.print(f"❌ Unsupported platform `{platform}`; choose from {', '.join(SUPPORTED_PLATFORMS)}")
        raise typer.Exit(code=1)
    paths = _resolve_tenant(workspace_dir, tenant)
    env = load_env_file(paths.env_file)
    keys = login_env_keys(plat, prefix.strip())
    missing = [k for k in keys if not env.get(k) and not k.endswith("_EMAIL")]
    if missing:
        err_console.print(f"❌ .env is missing {', '.join(missing)}")
        raise typer.Exit(code=1)
    _register_login(paths, plat, login.strip().lower(), prefix.strip())
    console.print(f"✅ Registered `{plat}/{login.strip().lower()}` → SECRET_{prefix.strip()}_*")



# ============================================================================
# `sw instance` — named service instances (tenant.json `instances` registry)
# ============================================================================

instance_app = typer.Typer(help="Register and inspect named service instances of the active tenant.")
app.add_typer(instance_app, name="instance")


def _write_tenant_manifest(paths: TenantPaths, doc: JsonDict) -> None:
    match parse_tenant_manifest(doc):
        case Err(schema_err):
            err_console.print(f"❌ {schema_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass
    match write_json_file(paths.manifest_file, doc):
        case Err(write_err):
            err_console.print(f"❌ {write_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass


@instance_app.command("list")
def cmd_instance_list(
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Show every registered instance, its env prefix, and whether .env has credentials for it."""
    paths = _resolve_tenant(workspace_dir, tenant)
    manifest = _load_tenant_manifest(paths)
    env = load_env_file(paths.env_file)

    table = Table(
        title=f"Instances — tenant `{manifest.name}`", box=box.ROUNDED, header_style="bold", expand=False
    )
    for col in ("Service", "Instance", "Env prefix", "Document", "URL", "UUID", "Ready"):
        table.add_column(col, overflow="fold")
    for svc in ALL_SERVICES:
        for name in sorted(manifest.instances.get(svc, {})):
            match resolve_instance_binding(manifest, env, svc, name):
                case Err(bind_err):
                    err_console.print(f"❌ {bind_err}")
                    raise typer.Exit(code=1)
                case Ok(b):
                    pass
            doc_path = _service_doc_path(paths.docs_dir, svc, name)
            table.add_row(
                svc,
                name,
                b.prefix,
                paths.relative(doc_path) + ("" if doc_path.exists() else " (missing)"),
                b.url or "—",
                b.uuid or "—",
                "✅ pull+push" if b.configured_for_pull() else ("🆕 push --create" if b.configured_for_push(create=True) else "❌"),
            )
    console.print(table)


@instance_app.command("new")
def cmd_instance_new(
    service: Annotated[str, typer.Argument(help="aiostreams | aiometadata | aiomanager")],
    name: Annotated[str, typer.Argument(help="Instance name (e.g. dad)")],
    prefix: Annotated[
        str | None,
        typer.Option("--prefix", help="Env key prefix (default: <SERVICE>_<NAME> upper-cased)"),
    ] = None,
    template: Annotated[
        Path | None,
        typer.Option("--from", help="Existing document to copy as this instance's starting point (any tenant)"),
    ] = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Register a named instance: tenant.json entry, .env/.env.example keys, optional starter document."""
    svc = service.strip().lower()
    if svc not in ALL_SERVICES:
        err_console.print(f"❌ Unknown service `{service}`; choose from {', '.join(ALL_SERVICES)}")
        raise typer.Exit(code=1)
    svc_id: ServiceId = svc
    inst = name.strip().lower()
    env_prefix = (prefix or f"{svc_id.upper()}_{inst.upper().replace('-', '_')}").strip()

    paths = _resolve_tenant(workspace_dir, tenant)
    match read_json_file(paths.manifest_file):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(manifest_doc):
            pass
    instances = manifest_doc.setdefault("instances", {})
    if not isinstance(instances, dict):
        err_console.print("❌ tenant.json: `instances` must be an object")
        raise typer.Exit(code=1)
    svc_registry = instances.setdefault(svc_id, {})
    if inst in svc_registry:
        err_console.print(f"❌ `{svc_id}/{inst}` already registered (prefix {svc_registry[inst]})")
        raise typer.Exit(code=1)
    svc_registry[inst] = env_prefix
    _write_tenant_manifest(paths, manifest_doc)

    keys = instance_env_keys(svc_id, env_prefix)
    url_key = keys[0]
    # Non-default instances inherit the default URL unless their own is set — scaffold the others only.
    scaffold = [k for k in keys if k != url_key]
    for target_path in (paths.env_file, paths.env_example_file):
        match scaffold_env_keys(target_path, scaffold, header=f"{svc_id}/{inst} instance ({url_key} optional)"):
            case Err(write_err):
                err_console.print(f"❌ {write_err}")
                raise typer.Exit(code=1)
            case Ok():
                pass

    doc_path = _service_doc_path(paths.docs_dir, svc_id, inst)
    if template is not None:
        src_doc = template.expanduser().resolve()
        match read_text_file(src_doc):
            case Err(read_err):
                err_console.print(f"❌ {read_err}")
                raise typer.Exit(code=1)
            case Ok(text):
                pass
        match write_text_file(doc_path, text):
            case Err(write_err):
                err_console.print(f"❌ {write_err}")
                raise typer.Exit(code=1)
            case Ok():
                pass

    console.print(f"✅ Registered `{svc_id}/{inst}` → env prefix {env_prefix}")
    console.print(f"   • keys scaffolded in .env: {', '.join(scaffold)} ({url_key} optional; inherits default URL)")
    console.print(f"   • document: {paths.relative(doc_path)}" + ("" if doc_path.exists() else " (create it or pass --from)"))
    console.print(
        f"   💡 Next: set {env_prefix}_{'SYNC_PASSWORD' if svc_id == 'aiomanager' else 'PASSWORD'} in .env, then "
        f"`sw push {svc_id} --instance {inst} --create` to provision it (UUID is written back automatically)."
    )



@config_app.command("validate")
@app.command("validate")
def cmd_config_validate(
    all_tenants: Annotated[
        bool,
        typer.Option("--all-tenants", help="Validate every ./tenants/*/ directory instead of the active tenant"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Offline validation: tenant.json schema, document parse/normalize, and architecture sync."""
    if all_tenants:
        repo_root = (workspace_dir or _CLI_WORKSPACE_DIR or Path.cwd()).expanduser().resolve()
        targets = [TenantPaths(p) for p in discover_tenants(repo_root)]
        if not targets:
            err_console.print(f"❌ No tenants found under {repo_root / 'tenants'}")
            raise typer.Exit(code=1)
    else:
        targets = [_resolve_tenant(workspace_dir, tenant)]

    any_failed = False
    for paths in targets:
        failures = _validate_tenant(paths)
        label = paths.root.name
        if failures:
            any_failed = True
            err_console.print(f"❌ [{label}] {len(failures)} problem(s):")
            for f in failures:
                err_console.print(f"   • {f}")
        else:
            console.print(f"✅ [{label}] tenant is valid")
    if any_failed:
        raise typer.Exit(code=1)


@config_app.command("pull")
@app.command("pull")
def cmd_config_pull(
    target: Annotated[
        str,
        typer.Argument(help="Service to pull: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, nuvio_meta, or nuvio_settings"),
    ] = "all",
    instance: InstanceNameOption = DEFAULT_INSTANCE,
    profile: ProfileNameOption = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Pull live configs from remote instances, harvest secrets to .env, and scrub JSON files."""
    paths = _resolve_tenant(workspace_dir, tenant)
    with HttpxClient() as http_client:
        res = pull_workspace(
            paths,
            target=target,
            instance=instance,
            profile=profile,
            on_step=_print_engine_step,
            extra_library_roots=_extra_library_roots(),
            http_client=http_client,
        )
    match res:
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok():
            console.print("🗺️  [diagram] Updated architecture/topology.c4 & ARCHITECTURE.md")


@config_app.command("push")
@app.command("push")
def cmd_config_push(
    target: Annotated[
        str,
        typer.Argument(help="Service to push: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, nuvio_meta, or nuvio_settings"),
    ] = "all",
    create: Annotated[
        bool,
        typer.Option("--create", help="Create a brand-new UUID on the target server"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview config diff against remote without pushing"),
    ] = False,
    warm: Annotated[
        bool,
        typer.Option("--warm", help="Pre-warm Home catalog rows after pushing"),
    ] = False,
    instance: InstanceNameOption = DEFAULT_INSTANCE,
    profile: ProfileNameOption = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Inject secrets from .env and push the tenant's documents to remote servers."""
    if dry_run:
        cmd_config_diff(
            target=target,
            stat_only=False,
            exit_code=False,
            instance=instance,
            profile=profile,
            workspace_dir=workspace_dir,
            tenant=tenant,
        )
        return

    paths = _resolve_tenant(workspace_dir, tenant)
    with HttpxClient() as http_client:
        res = push_workspace(
            paths,
            target=target,
            instance=instance,
            profile=profile,
            create=create,
            warm_catalogs=warm,
            on_step=_print_engine_step,
            extra_library_roots=_extra_library_roots(),
            http_client=http_client,
        )
    match res:
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok():
            console.print("🗺️  [diagram] Updated architecture/topology.c4 & ARCHITECTURE.md")


@config_app.command("diff")
@app.command("diff")
def cmd_config_diff(
    target: Annotated[
        str,
        typer.Argument(help="Service to diff: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, nuvio_meta, or nuvio_settings"),
    ] = "all",
    stat_only: Annotated[
        bool,
        typer.Option("--stat", help="Show only changed keys and line counts without full unified diff"),
    ] = False,
    exit_code: Annotated[
        bool,
        typer.Option("--exit-code", help="Exit with code 1 if any drift is detected"),
    ] = False,
    instance: InstanceNameOption = DEFAULT_INSTANCE,
    profile: ProfileNameOption = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Compare the tenant's documents against live remote instances without modifying any files."""
    paths = _resolve_tenant(workspace_dir, tenant)
    with (
        HttpxClient() as http_client,
        console.status("🔍 Comparing local configs against live remote instances in parallel..."),
    ):
        res = diff_workspace(
            paths,
            target=target,
            instance=instance,
            profile=profile,
            extra_library_roots=_extra_library_roots(),
            http_client=http_client,
        )
    match res:
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok(report):
            for skip_msg in report.skipped:
                console.print(f"⏭️  {skip_msg}")
            _render_diff_summary_table(report.outcomes)
            for outcome in report.outcomes:
                _print_diff_outcome(outcome, stat_only=stat_only)
            if exit_code and any(not o.in_sync for o in report.outcomes):
                raise typer.Exit(code=1)


@config_app.command("doctor")
@app.command("doctor")
def cmd_doctor(
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Check .env keys and config requirements without live network probes"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the raw JSON health report for automation"),
    ] = False,
    exit_code: Annotated[
        bool,
        typer.Option("--exit-code", help="Exit with code 1 if any health check fails with an error"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Run live workspace health checks across .env keys, AIO servers, Debrid, and metadata APIs."""
    paths = _resolve_tenant(workspace_dir, tenant)
    with HttpxClient() as http_client:
        res = verify_workspace_health(
            paths,
            probe_network=not offline,
            extra_library_roots=_extra_library_roots(),
            http_client=http_client,
        )
    match res:
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok(report):
            pass

    overall = str(report.get("overall") or "unknown")
    raw_summary = report.get("summary")
    summary: Mapping[str, Any] = raw_summary if isinstance(raw_summary, Mapping) else {}
    raw_checks = report.get("checks")
    checks: list[Any] = raw_checks if isinstance(raw_checks, list) else []

    if json_output:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        status_icons = {
            "ok": "✅ ok",
            "warn": "⚠️  warn",
            "error": "❌ error",
            "skipped": "⏭️  skipped",
        }
        overall_icons = {
            "healthy": "✅ HEALTHY",
            "warn": "⚠️  WARNING",
            "error": "❌ ERROR",
        }
        table = Table(
            title=f"Workspace Health Doctor — tenant `{paths.root.name}`",
            box=box.ROUNDED,
            header_style="bold",
        )
        for col in ("Category", "Check", "Status", "Detail"):
            table.add_column(col, overflow="fold")
        for chk in checks:
            if not isinstance(chk, Mapping):
                continue
            st = str(chk.get("status") or "skipped")
            table.add_row(
                str(chk.get("category") or ""),
                str(chk.get("label") or chk.get("id") or ""),
                status_icons.get(st, st),
                str(chk.get("detail") or ""),
            )
        console.print(table)
        console.print(
            f"🩺 Overall: {overall_icons.get(overall, overall)}  "
            f"(ok={summary.get('ok', 0)}, warn={summary.get('warn', 0)}, "
            f"error={summary.get('error', 0)}, skipped={summary.get('skipped', 0)})"
        )

    if exit_code and overall == "error":
        raise typer.Exit(code=1)


@config_app.command("sanitize")
@app.command("sanitize")
def cmd_config_sanitize(
    target: Annotated[
        str,
        typer.Argument(help="Service file to sanitize: all, aiostreams, aiometadata, or aiomanager"),
    ] = "all",
    instance: InstanceNameOption = DEFAULT_INSTANCE,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Normalize, dehydrate, and scrub secrets from the tenant's service documents."""
    ctx = _resolve_context(workspace_dir, tenant, instance)
    root_dir, env_path, configs_dir = ctx.paths.root, ctx.paths.env_file, ctx.paths.docs_dir
    working_env = dict(ctx.env)
    found_any = False

    for svc_id in _resolve_service_targets(target):
        match _sanitize_service(svc_id, working_env, env_path=env_path, configs_dir=configs_dir, instance=instance):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok((saved_path, working_env)) if saved_path is not None:
                found_any = True
                console.print(
                    f"🧹 [{svc_id}] Auto-harvested secrets to .env & scrubbed {saved_path.relative_to(root_dir)}"
                )
            case Ok():
                pass

    if not found_any:
        console.print("ℹ️  No service documents found in the tenant to sanitize.")
    else:
        match _sync_diagrams_for_ctx(ctx):
            case Ok():
                console.print("🗺️  [diagram] Updated architecture/topology.c4 & ARCHITECTURE.md")
            case Err(diag_err):
                err_console.print(f"⚠️  [diagram] {diag_err}")


@config_app.command("diagram")
@app.command("diagram")
def cmd_config_diagram(
    check: Annotated[
        bool,
        typer.Option("--check", help="Verify that architecture diagrams are up-to-date without writing"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Compile LikeC4 (architecture/topology.c4) & ARCHITECTURE.md from the tenant's JSON documents."""
    ctx = _resolve_context(workspace_dir, tenant)
    root_dir = ctx.paths.root
    match _sync_diagrams_for_ctx(ctx, check_only=check):
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok(paths):
            if check:
                if paths:
                    rel_paths = ", ".join(str(p.relative_to(root_dir)) for p in paths)
                    err_console.print(f"❌ [diagram] Out of sync with the tenant's JSON documents: {rel_paths}")
                    raise typer.Exit(code=1)
                console.print("✅ [diagram] LikeC4 topology & ARCHITECTURE.md are in sync with the tenant's JSON documents")
            else:
                rel_paths = ", ".join(str(p.relative_to(root_dir)) for p in paths)
                console.print(f"🗺️  [diagram] Compiled LikeC4 model & docs: {rel_paths}")


@config_app.command("probe")
@app.command("probe")
def cmd_config_probe(
    profile: ProfileNameOption = None,
    min_items: Annotated[int, typer.Option("--min-items", help="Rows with fewer items are flagged")] = 6,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Concurrent catalog requests")] = 6,
    exit_code: Annotated[bool, typer.Option("--exit-code", help="Exit 1 if any row is empty or thin")] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Fetch every Home row of each profile's AIOMetadata instance and report how many items it returns."""
    ctx = _resolve_context(workspace_dir, tenant)
    all_compiled = _compile_all(ctx)
    _, chosen = _scope_to_profile(ctx, all_compiled, profile)

    seen_instances: set[str] = set()
    any_bad = False
    with HttpxClient() as http_client:
        for cp in chosen:
            binding = cp.resolved.instances.get("aiometadata")
            if binding is None or not binding.configured_for_pull() or binding.name in seen_instances:
                continue
            seen_instances.add(binding.name)
            doc_path = _service_doc_path(ctx.paths.docs_dir, "aiometadata", binding.name)
            match derive_instance_catalogs(binding.name, _load_optional_json(doc_path), all_compiled):
                case Err(derive_err):
                    err_console.print(f"❌ {derive_err}")
                    raise typer.Exit(code=1)
                case Ok(plan):
                    pass
            home = [c for c in plan.catalogs if c.get("showInHome")]
            base = f"{binding.base_url}/stremio/{binding.uuid}"
            owners = ", ".join(plan.profiles)

            def _fetch(entry: JsonDict, base: str = base) -> tuple[JsonDict, int | None, str]:
                # manifest-side id: streaming.x → streaming.x_<type>; anime → displayType
                keys = config_catalog_keys(entry)
                cid, ctype = keys[1] if str(entry.get("source")) == "streaming" or entry.get("type") == "anime" else keys[0]
                url = f"{base}/catalog/{urllib.parse.quote(ctype, safe='.:_-')}/{urllib.parse.quote(cid, safe='.:_-')}.json"
                match http_client.request_json("GET", url, timeout=20.0):
                    case Ok(resp) if resp.status_code == 200 and isinstance(resp.data, dict):
                        metas = resp.data.get("metas")
                        return entry, (len(metas) if isinstance(metas, list) else 0), ""
                    case Ok(resp):
                        return entry, None, f"HTTP {resp.status_code}"
                    case Err(net_err):
                        return entry, None, str(net_err)[:80]

            table = Table(
                title=f"Home rows — aiometadata/{binding.name} (profiles: {owners})", box=box.ROUNDED, header_style="bold"
            )
            for col in ("#", "Row", "Catalog", "Items", "Status"):
                table.add_column(col, overflow="fold")
            with (
                console.status(f"🔎 Probing {len(home)} Home rows on aiometadata/{binding.name}..."),
                concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool,
            ):
                results = list(pool.map(_fetch, home))
            for n, (entry, count, note) in enumerate(results, 1):
                if count is None:
                    status, flag = f"❌ {note}", True
                elif count == 0:
                    status, flag = "❌ empty", True
                elif count < min_items:
                    status, flag = f"⚠️  thin (<{min_items})", True
                else:
                    status, flag = "✅", False
                any_bad = any_bad or flag
                table.add_row(str(n), str(entry.get("name")), f"{entry.get('id')} ({entry.get('type')})", "—" if count is None else str(count), status)
            console.print(table)
    if exit_code and any_bad:
        raise typer.Exit(code=1)


@config_app.command("warm")
@app.command("warm")
def cmd_config_warm(
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", help="Number of concurrent requests to warm Home catalogs"),
    ] = 8,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Concurrently pre-warm all Home catalog endpoints across enabled addons."""
    paths = _resolve_tenant(workspace_dir, tenant)
    with (
        HttpxClient() as http_client,
        console.status(f"🔥 Pre-warming Home catalog rows ({workers} workers)..."),
    ):
        res = warm_workspace_catalogs(
            paths,
            workers=workers,
            extra_library_roots=_extra_library_roots(),
            http_client=http_client,
        )
    match res:
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok(report):
            console.print(f"🔥 {report['message']}")


@app.command("studio")
def cmd_studio(
    host: Annotated[str, typer.Option("--host", help="Bind address (use 0.0.0.0 for LAN/hosted)")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="HTTP port to listen on")] = 8787,
    stateless: Annotated[
        bool,
        typer.Option(
            "--stateless",
            help="Stateless browser mode: never reads/writes a local tenant; keys stay in browser localStorage",
        ),
    ] = False,
    template_dir: Annotated[
        Path | None,
        typer.Option("--template", help="Starter template directory for --stateless mode"),
    ] = None,
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Do not automatically open a browser tab on launch"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Launch the visual Stream Weaver Studio Web UI (customize collections, packs, keys & click Push)."""
    shared_lib = bundled_shared_library_root()
    if stateless:
        tpl = template_dir.expanduser().resolve() if template_dir else bundled_starter_template_root()
        if tpl is None or not tpl.is_dir():
            err_console.print("❌ Starter template directory not found (pass --template <dir>)")
            raise typer.Exit(code=1)
        dispatcher = StudioDispatcher(
            tenant_paths=None,
            template_root=tpl,
            shared_library_root=shared_lib,
        )
        mode_desc = f"Stateless Browser Mode (template: {tpl.name})"
    else:
        paths = _resolve_tenant(workspace_dir, tenant)
        dispatcher = StudioDispatcher(
            tenant_paths=paths,
            shared_library_root=shared_lib,
        )
        mode_desc = f"Local GitOps Mode (tenant: {paths.root})"

    serve_studio(
        dispatcher,
        host=host,
        port=port,
        open_browser=not no_open,
        on_listen=lambda url: console.print(
            f"🧶 Stream Weaver Studio running at {url}  [{mode_desc}]  (Ctrl+C to stop)"
        ),
    )


def main() -> None:
    """Main CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
    sys.exit(0)
