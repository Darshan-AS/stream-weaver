"""Stream Weaver CLI (`sw`) — Pure Functional GitOps Control Plane for AIOStreams, AIOMetadata, and AIOManager."""

import concurrent.futures
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from stream_weaver.core.architecture import compile_architecture_bundle
from stream_weaver.core.coercions import compute_config_diff, normalize_service_config
from stream_weaver.core.crypto import decrypt_envelope
from stream_weaver.core.instances import (
    DEFAULT_INSTANCE,
    InstanceBinding,
    instance_env_keys,
    resolve_instance_binding,
)
from stream_weaver.core.nuvio_badges import normalize_nuvio_badges_doc
from stream_weaver.core.nuvio_meta import normalize_nuvio_meta_doc
from stream_weaver.core.nuvio_settings import active_blocks, normalize_nuvio_settings_doc
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets
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
    ServicePushOutcome,
)
from stream_weaver.effects.http import HttpClientProtocol, HttpxClient
from stream_weaver.effects.storage import (
    load_env_file,
    read_json_file,
    read_text_file,
    save_env_updates,
    scaffold_env_keys,
    write_json_file,
    write_text_file,
)
from stream_weaver.providers.protocols import ConfigServiceProtocol
from stream_weaver.providers.services import build_config_services, warm_home_catalogs
from stream_weaver.providers.services.clients import NuvioClientSync, decompile_nuvio_collections

app = typer.Typer(
    name="sw",
    help="Stream Weaver — Pure Functional GitOps Control Plane for AIOStreams, AIOMetadata, AIOManager, Nuvio & Stremio.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Manage AIOStreams, AIOMetadata, and AIOManager GitOps configurations.")
app.add_typer(config_app, name="config")

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
SECRET_TRAKT_TOKEN_ID=
SECRET_GEMINI_API_KEY=
SECRET_BINGECAT_TOKEN=
SECRET_WATCHLY_TOKEN=
SECRET_ITSOUT_TOKEN=
SECRET_SUBSOURCE_API_KEY=
SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD=
SECRET_AIOMETADATA_TOKEN=

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


def _resolve_context(
    workspace_dir: Path | None = None,
    tenant_name: str | None = None,
    instance: str = DEFAULT_INSTANCE,
) -> tuple[TenantPaths, TenantManifest, dict[str, str], dict[ServiceId, InstanceBinding]]:
    """Active tenant paths, validated manifest, `.env` map, and per-service bindings for `instance` (where registered)."""
    paths = _resolve_tenant(workspace_dir, tenant_name)
    manifest = _load_tenant_manifest(paths)
    env = load_env_file(paths.env_file)
    bindings: dict[ServiceId, InstanceBinding] = {}
    for svc in ALL_SERVICES:
        match resolve_instance_binding(manifest, env, svc, instance):
            case Ok(binding):
                bindings[svc] = binding
            case Err():
                continue
    return paths, manifest, env, bindings


def _service_doc_path(configs_dir: Path, service: ServiceId, instance: str = DEFAULT_INSTANCE) -> Path:
    """`<service>.json` for the default instance, `<service>.<instance>.json` otherwise."""
    return configs_dir / (f"{service}.json" if instance == DEFAULT_INSTANCE else f"{service}.{instance}.json")


def _resolve_service_targets(target: str) -> tuple[ServiceId, ...]:
    cleaned = target.strip().lower()
    if cleaned == "all":
        return ALL_SERVICES
    if cleaned in ("nuvio_collections", "nuvio_badges", "nuvio_meta", "nuvio_settings", "nuvio"):
        return ()
    if cleaned in ("aiostreams", "aiometadata", "aiomanager"):
        return (cleaned,)  # type: ignore[return-value]
    err_console.print(
        f"❌ Unknown service target '{target}'. Choose from: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, nuvio_meta, nuvio_settings"
    )
    raise typer.Exit(code=1)


def _should_include_nuvio_collections(target: str) -> bool:
    return target.strip().lower() in ("all", "aiomanager", "nuvio_collections", "nuvio")


def _should_include_nuvio_badges(target: str) -> bool:
    return target.strip().lower() in ("all", "aiomanager", "nuvio_badges", "nuvio")


def _should_include_nuvio_meta(target: str) -> bool:
    return target.strip().lower() in ("all", "aiomanager", "nuvio_meta", "nuvio")


def _should_include_nuvio_settings(target: str) -> bool:
    return target.strip().lower() in ("all", "aiomanager", "nuvio_settings", "nuvio")


def _resolve_nuvio_connection(
    configs_dir: Path,
    env: Mapping[str, str],
) -> Result[tuple[Mapping[str, Any], str, str, list[Any]] | None]:
    """Resolves the active Nuvio connection, master password, salt, and addons from the tenant's `aiomanager.json`."""
    mgr_path = configs_dir / "aiomanager.json"
    password = env.get("AIOMANAGER_SYNC_PASSWORD", "")
    anon_key = env.get("NUVIO_SUPABASE_ANON_KEY", "")
    if not mgr_path.exists() or not password or not anon_key:
        return Ok(None)

    match read_json_file(mgr_path):
        case Err() as err:
            return err
        case Ok(mgr_scrubbed):
            pass

    match inject_secrets(mgr_scrubbed, env):
        case Err() as err:
            return err
        case Ok(mgr_injected):
            pass

    salt_b64 = str(mgr_injected.get("salt") or "")
    accounts = mgr_injected.get("accounts")
    if isinstance(accounts, list):
        for acc in accounts:
            if not isinstance(acc, Mapping):
                continue
            conns = acc.get("connections")
            if not isinstance(conns, list):
                continue
            for conn in conns:
                if (
                    isinstance(conn, Mapping)
                    and conn.get("enabled", True)
                    and str(conn.get("platform") or "").lower() == "nuvio"
                ):
                    raw_addons = acc.get("addons")
                    addons_list = raw_addons if isinstance(raw_addons, list) else []
                    return Ok((conn, password, salt_b64, addons_list))
    return Ok(None)


def _pull_remote_nuvio_collections_doc(
    configs_dir: Path,
    env: Mapping[str, str],
    http_client: HttpClientProtocol,
) -> Result[tuple[JsonDict | None, dict[str, str]]]:
    """Pulls live Nuvio Home Collections via `NuvioClientSync.pull_collections` and scrubs secrets."""
    match _resolve_nuvio_connection(configs_dir, env):
        case Err() as err:
            return err
        case Ok(None):
            return Ok((None, dict(env)))
        case Ok((nuvio_conn, password, salt_b64, _)):
            pass

    nuvio_syncer = NuvioClientSync(http_client)
    match nuvio_syncer.pull_collections(nuvio_conn, password, salt_b64, env):
        case Err() as err:
            return err
        case Ok((collections_list, refreshed_secrets)):
            pass

    existing_cols_path = configs_dir / "nuvio_collections.json"
    version = "1.0.0"
    if existing_cols_path.exists() and isinstance(existing_res := read_json_file(existing_cols_path), Ok):
        version = str(existing_res.value.get("version") or "1.0.0")

    merged_env = {**env, **refreshed_secrets}
    raw_doc: JsonDict = {"version": version, "collections": collections_list}
    scrubbed_doc, updated_env = harvest_and_scrub("aiomanager", raw_doc, merged_env)
    return Ok((scrubbed_doc, updated_env))


def _pull_remote_nuvio_badges_doc(
    configs_dir: Path,
    env: Mapping[str, str],
    http_client: HttpClientProtocol,
) -> Result[tuple[JsonDict | None, dict[str, str]]]:
    """Pulls live Nuvio Stream Badge settings across platforms and scrubs secrets."""
    match _resolve_nuvio_connection(configs_dir, env):
        case Err() as err:
            return err
        case Ok(None):
            return Ok((None, dict(env)))
        case Ok((nuvio_conn, password, salt_b64, _)):
            pass

    existing_badges_path = configs_dir / "nuvio_badges.json"
    version = "1.0.0"
    if existing_badges_path.exists() and isinstance(existing_res := read_json_file(existing_badges_path), Ok):
        version = str(existing_res.value.get("version") or "1.0.0")

    nuvio_syncer = NuvioClientSync(http_client)
    match nuvio_syncer.pull_badges(nuvio_conn, password, salt_b64, env, version=version):
        case Err() as err:
            return err
        case Ok((badges_doc, refreshed_secrets)):
            merged_env = {**env, **refreshed_secrets}
            scrubbed_doc, updated_env = harvest_and_scrub("aiomanager", badges_doc, merged_env)
            return Ok((scrubbed_doc, updated_env))


def _pull_remote_nuvio_meta_doc(
    configs_dir: Path,
    env: Mapping[str, str],
    http_client: HttpClientProtocol,
) -> Result[tuple[JsonDict | None, dict[str, str]]]:
    """Pulls live Nuvio Meta enrichment settings (MDBList/TMDB/Trakt/Meta screen) and scrubs secrets."""
    match _resolve_nuvio_connection(configs_dir, env):
        case Err() as err:
            return err
        case Ok(None):
            return Ok((None, dict(env)))
        case Ok((nuvio_conn, password, salt_b64, _)):
            pass

    existing_meta_path = configs_dir / "nuvio_meta.json"
    version = "1.0.0"
    if existing_meta_path.exists() and isinstance(existing_res := read_json_file(existing_meta_path), Ok):
        version = str(existing_res.value.get("version") or "1.0.0")

    nuvio_syncer = NuvioClientSync(http_client)
    match nuvio_syncer.pull_meta(nuvio_conn, password, salt_b64, env, version=version):
        case Err() as err:
            return err
        case Ok((meta_doc, refreshed_secrets)):
            merged_env = {**env, **refreshed_secrets}
            scrubbed_doc, updated_env = harvest_and_scrub("aiomanager", meta_doc, merged_env)
            return Ok((scrubbed_doc, updated_env))


def _pull_remote_nuvio_settings_doc(
    configs_dir: Path,
    env: Mapping[str, str],
    http_client: HttpClientProtocol,
) -> Result[tuple[JsonDict | None, dict[str, str]]]:
    """Pulls live Nuvio app settings (playback/subtitles/audio/theme/TV layout) and scrubs secrets."""
    match _resolve_nuvio_connection(configs_dir, env):
        case Err() as err:
            return err
        case Ok(None):
            return Ok((None, dict(env)))
        case Ok((nuvio_conn, password, salt_b64, _)):
            pass

    existing_settings_path = configs_dir / "nuvio_settings.json"
    version = "1.0.0"
    blocks: list[str] | None = None
    if existing_settings_path.exists() and isinstance(
        existing_res := read_json_file(existing_settings_path), Ok
    ):
        version = str(existing_res.value.get("version") or "1.0.0")
        if isinstance(norm_res := normalize_nuvio_settings_doc(existing_res.value), Ok):
            blocks = sorted(active_blocks(norm_res.value))

    nuvio_syncer = NuvioClientSync(http_client)
    match nuvio_syncer.pull_settings(
        nuvio_conn, password, salt_b64, env, version=version, blocks=blocks
    ):
        case Err() as err:
            return err
        case Ok((settings_doc, refreshed_secrets)):
            merged_env = {**env, **refreshed_secrets}
            scrubbed_doc, updated_env = harvest_and_scrub("aiomanager", settings_doc, merged_env)
            return Ok((scrubbed_doc, updated_env))


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


def _pull_service(
    service_id: ServiceId,
    env: dict[str, str],
    *,
    env_path: Path,
    configs_dir: Path,
    services: Mapping[ServiceId, ConfigServiceProtocol],
    bindings: Mapping[ServiceId, InstanceBinding],
) -> Result[tuple[Path | None, dict[str, str]]]:
    svc = services.get(service_id)
    binding = bindings.get(service_id)
    if svc is None or binding is None or not binding.configured_for_pull():
        return Ok((None, env))

    match svc.pull_remote(binding):
        case Err() as err:
            return err
        case Ok(normalized_cfg):
            return _persist_scrubbed_config(
                service_id,
                normalized_cfg,
                env,
                env_path=env_path,
                configs_dir=configs_dir,
                instance=binding.name,
            )


def _diff_service(
    service_id: ServiceId,
    env: Mapping[str, str],
    *,
    configs_dir: Path,
    services: Mapping[ServiceId, ConfigServiceProtocol],
    bindings: Mapping[ServiceId, InstanceBinding],
) -> Result[ConfigDiffOutcome | None]:
    """Pulls remote service config in memory, scrubs secrets, and diffs against the tenant's `{service_id}.json`."""
    svc = services.get(service_id)
    binding = bindings.get(service_id)
    if svc is None or binding is None or not binding.configured_for_pull():
        return Ok(None)
    cfg_path = _service_doc_path(configs_dir, service_id, binding.name)
    if not cfg_path.exists():
        return Ok(None)

    match read_json_file(cfg_path):
        case Err() as err:
            return err
        case Ok(local_scrubbed):
            pass

    match svc.pull_remote(binding):
        case Err() as err:
            return err
        case Ok(remote_normalized):
            remote_scrubbed, _ = harvest_and_scrub(service_id, remote_normalized, env)
            return Ok(compute_config_diff(local_scrubbed, remote_scrubbed, target=service_id))


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


def _push_service(
    service_id: ServiceId,
    env: dict[str, str],
    *,
    env_path: Path,
    configs_dir: Path,
    services: Mapping[ServiceId, ConfigServiceProtocol],
    bindings: Mapping[ServiceId, InstanceBinding],
    create: bool = False,
) -> Result[tuple[ServicePushOutcome | None, dict[str, str]]]:
    svc = services.get(service_id)
    binding = bindings.get(service_id)
    if svc is None or binding is None or not binding.configured_for_push(create=create):
        return Ok((None, env))
    cfg_path = _service_doc_path(configs_dir, service_id, binding.name)
    if not cfg_path.exists():
        return Ok((None, env))

    match read_json_file(cfg_path):
        case Err() as err:
            return err
        case Ok(scrubbed_cfg):
            pass

    match inject_secrets(scrubbed_cfg, env):
        case Err() as err:
            return err
        case Ok(injected_cfg):
            pass

    if service_id == "aiomanager":
        nuvio_cols_path = configs_dir / "nuvio_collections.json"
        if nuvio_cols_path.exists():
            match read_json_file(nuvio_cols_path):
                case Err() as err:
                    return err
                case Ok(raw_cols_doc):
                    match inject_secrets(raw_cols_doc, env):
                        case Err() as err:
                            return err
                        case Ok(injected_cols_doc):
                            cols_list = injected_cols_doc.get("collections")
                            if isinstance(cols_list, list) and cols_list:
                                injected_cfg["nuvioCollections"] = cols_list

        nuvio_badges_path = configs_dir / "nuvio_badges.json"
        if nuvio_badges_path.exists():
            match read_json_file(nuvio_badges_path):
                case Err() as err:
                    return err
                case Ok(raw_badges_doc):
                    match inject_secrets(raw_badges_doc, env):
                        case Err() as err:
                            return err
                        case Ok(injected_badges_doc):
                            match normalize_nuvio_badges_doc(injected_badges_doc):
                                case Err() as err:
                                    return err
                                case Ok(norm_badges_doc):
                                    injected_cfg["nuvioBadges"] = norm_badges_doc

        nuvio_meta_path = configs_dir / "nuvio_meta.json"
        if nuvio_meta_path.exists():
            match read_json_file(nuvio_meta_path):
                case Err() as err:
                    return err
                case Ok(raw_meta_doc):
                    match inject_secrets(raw_meta_doc, env):
                        case Err(sec_err):
                            # Implicit path (`sw push all|aiomanager`): don't block the whole push
                            # on an unset MDBList/TMDB key; `sw push nuvio_meta` stays strict.
                            err_console.print(
                                f"⚠️  [nuvio_meta] Skipping Nuvio meta enrichment sync: {sec_err}"
                            )
                        case Ok(injected_meta_doc):
                            match normalize_nuvio_meta_doc(injected_meta_doc):
                                case Err() as err:
                                    return err
                                case Ok(norm_meta_doc):
                                    injected_cfg["nuvioMeta"] = norm_meta_doc

        nuvio_settings_path = configs_dir / "nuvio_settings.json"
        if nuvio_settings_path.exists():
            match read_json_file(nuvio_settings_path):
                case Err() as err:
                    return err
                case Ok(raw_settings_doc):
                    match inject_secrets(raw_settings_doc, env):
                        case Err(sec_err):
                            err_console.print(
                                f"⚠️  [nuvio_settings] Skipping Nuvio app settings sync: {sec_err}"
                            )
                        case Ok(injected_settings_doc):
                            match normalize_nuvio_settings_doc(injected_settings_doc):
                                case Err() as err:
                                    return err
                                case Ok(norm_settings_doc):
                                    injected_cfg["nuvioSettings"] = norm_settings_doc

    match svc.push_remote(injected_cfg, binding, env, create=create):
        case Err() as err:
            return err
        case Ok((outcome, updated_cfg, discovered_secrets)):
            pass

    if outcome.created and outcome.uuid:
        # A freshly provisioned instance: persist its UUID under the binding's own env key.
        discovered_secrets = {**discovered_secrets, binding.uuid_env_key: outcome.uuid}

    working_env = {**env, **discovered_secrets}
    if service_id == "aiomanager":
        match _persist_scrubbed_config(
            service_id,
            updated_cfg,
            working_env,
            env_path=env_path,
            configs_dir=configs_dir,
            instance=binding.name,
        ):
            case Err() as err:
                return err
            case Ok((_, working_env)):
                pass
    elif discovered_secrets:
        match save_env_updates(env_path, working_env):
            case Err() as err:
                return err
            case Ok():
                pass

    return Ok((outcome, working_env))


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
    root_dir: Path,
    configs_dir: Path,
    *,
    check_only: bool = False,
) -> Result[tuple[Path, ...]]:
    """Compiles LikeC4 (`architecture/topology.c4`) and `ARCHITECTURE.md` from the tenant's JSON documents."""
    bundle = compile_architecture_bundle(
        aiomanager=_load_optional_json(configs_dir / "aiomanager.json"),
        aiostreams=_load_optional_json(configs_dir / "aiostreams.json"),
        aiometadata=_load_optional_json(configs_dir / "aiometadata.json"),
        nuvio_collections=_load_optional_json(configs_dir / "nuvio_collections.json"),
        nuvio_badges=_load_optional_json(configs_dir / "nuvio_badges.json"),
        nuvio_meta=_load_optional_json(configs_dir / "nuvio_meta.json"),
        nuvio_settings=_load_optional_json(configs_dir / "nuvio_settings.json"),
    )
    arch_dir = root_dir / "architecture"
    targets: tuple[tuple[Path, str], ...] = (
        (arch_dir / "topology.c4", bundle.likec4_dsl),
        (arch_dir / "likec4.config.json", bundle.likec4_config_json),
        (root_dir / "ARCHITECTURE.md", bundle.architecture_markdown),
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


# ============================================================================
# CLI Commands (`sw pull`, `sw push`, `sw sanitize`, `sw diagram`)
# ============================================================================


_TENANT_DOC_NAMES: tuple[str, ...] = (
    "aiostreams",
    "aiometadata",
    "aiomanager",
    "nuvio_collections",
    "nuvio_badges",
    "nuvio_meta",
    "nuvio_settings",
)


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
            help="Template tenant directory whose JSON documents are copied as a starting point (e.g. examples/home)",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite tenant.json, .env.example, .gitignore and template documents if present"),
    ] = False,
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

    manifest_doc = default_tenant_manifest(tenant_name)
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

    _emit(TENANT_FILE, lambda d: write_json_file(d, manifest_doc), force)
    _emit(".gitignore", lambda d: write_text_file(d, _INIT_GITIGNORE_TEMPLATE), force)
    _emit(".env.example", lambda d: write_text_file(d, _INIT_ENV_TEMPLATE), force)
    _emit(".env", lambda d: write_text_file(d, _INIT_ENV_TEMPLATE), False)

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

    match _sync_architecture_diagrams(paths.root, paths.docs_dir):
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

    match read_json_file(paths.manifest_file):
        case Err(read_err):
            return [f"{TENANT_FILE}: {read_err}"]
        case Ok(manifest_doc):
            pass
    manifest: TenantManifest | None = None
    match parse_tenant_manifest(manifest_doc):
        case Err(schema_err):
            failures.append(str(schema_err))
        case Ok(parsed_manifest):
            manifest = parsed_manifest

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

    nuvio_checks: tuple[tuple[str, Any], ...] = (
        ("nuvio_badges", normalize_nuvio_badges_doc),
        ("nuvio_meta", normalize_nuvio_meta_doc),
        ("nuvio_settings", normalize_nuvio_settings_doc),
    )
    for doc_name, normalizer in nuvio_checks:
        doc_path = paths.doc(doc_name)
        if not doc_path.exists():
            continue
        match read_json_file(doc_path):
            case Err(read_err):
                failures.append(f"{doc_name}.json: {read_err}")
                continue
            case Ok(raw_doc):
                pass
        match normalizer(raw_doc):
            case Err(nuvio_err):
                failures.append(f"{doc_name}.json: {nuvio_err}")
            case Ok():
                pass

    cols_path = paths.doc("nuvio_collections")
    if cols_path.exists():
        match read_json_file(cols_path):
            case Err(read_err):
                failures.append(f"nuvio_collections.json: {read_err}")
            case Ok(cols_doc) if not isinstance(cols_doc.get("collections"), list):
                failures.append("nuvio_collections.json: `collections` must be a list")
            case Ok():
                pass

    match _sync_architecture_diagrams(paths.root, paths.docs_dir, check_only=True):
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
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Pull live configs from remote instances, harvest secrets to .env, and scrub JSON files."""
    paths, _manifest, env, bindings = _resolve_context(workspace_dir, tenant, instance)
    root_dir, env_path, configs_dir = paths.root, paths.env_file, paths.docs_dir
    working_env = dict(env)
    svc_targets = _resolve_service_targets(target)
    include_nuvio = _should_include_nuvio_collections(target)
    include_nuvio_badges = _should_include_nuvio_badges(target)
    include_nuvio_meta = _should_include_nuvio_meta(target)
    include_nuvio_settings = _should_include_nuvio_settings(target)

    with HttpxClient() as http_client:
        services = build_config_services(http_client)
        configured_svcs: list[ServiceId] = []
        skipped_svcs: list[ServiceId] = []
        for svc_id in svc_targets:
            svc = services.get(svc_id)
            binding = bindings.get(svc_id)
            if svc is None or binding is None or not binding.configured_for_pull():
                skipped_svcs.append(svc_id)
            else:
                configured_svcs.append(svc_id)

        for svc_id in skipped_svcs:
            b = bindings.get(svc_id)
            hint = (
                f"missing {b.url_env_key}, {b.uuid_env_key}, or {b.password_env_key} in .env"
                if b is not None
                else "no `default` instance registered in tenant.json"
            )
            console.print(f"⏭️  [{svc_id}] Skipping pull: {hint}")

        pulled_raw: dict[ServiceId, Result[JsonDict]] = {}
        if configured_svcs:
            def _fetch_remote_svc(sid: ServiceId) -> tuple[ServiceId, Result[JsonDict]]:
                return sid, services[sid].pull_remote(bindings[sid])

            with (
                console.status(f"⏳ Pulling {', '.join(configured_svcs)} in parallel..."),
                concurrent.futures.ThreadPoolExecutor(max_workers=len(configured_svcs)) as pool,
            ):
                for sid, res in pool.map(_fetch_remote_svc, configured_svcs):
                    pulled_raw[sid] = res

        for svc_id in configured_svcs:
            match pulled_raw[svc_id]:
                case Err(err):
                    err_console.print(f"❌ {err}")
                    raise typer.Exit(code=1)
                case Ok(normalized_cfg):
                    match _persist_scrubbed_config(
                        svc_id,
                        normalized_cfg,
                        working_env,
                        env_path=env_path,
                        configs_dir=configs_dir,
                    ):
                        case Err(p_err):
                            err_console.print(f"❌ {p_err}")
                            raise typer.Exit(code=1)
                        case Ok((saved_path, working_env)):
                            uid = bindings[svc_id].uuid
                            console.print(
                                f"✅ [{svc_id}] Saved scrubbed config for UUID {uid} to {saved_path.relative_to(root_dir)}"
                            )

        if include_nuvio:
            with console.status("⏳ Pulling Nuvio Home Collections..."):
                nuvio_res = _pull_remote_nuvio_collections_doc(configs_dir, working_env, http_client)
            match nuvio_res:
                case Err(err):
                    if target.strip().lower() in ("nuvio_collections", "nuvio"):
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    err_console.print(f"⚠️  [nuvio_collections] {err}")
                case Ok((None, working_env)):
                    if target.strip().lower() in ("nuvio_collections", "nuvio"):
                        console.print(
                            "⏭️  [nuvio_collections] Skipping pull: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                        )
                case Ok((scrubbed_cols_doc, working_env)):
                    assert scrubbed_cols_doc is not None
                    if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                        err_console.print(f"❌ {env_err.error}")
                        raise typer.Exit(code=1)
                    cols_target = configs_dir / "nuvio_collections.json"
                    match write_json_file(cols_target, scrubbed_cols_doc):
                        case Err(write_err):
                            err_console.print(f"❌ {write_err}")
                            raise typer.Exit(code=1)
                        case Ok(saved_cols_path):
                            col_cnt = len(scrubbed_cols_doc.get("collections", []))
                            console.print(
                                f"✅ [nuvio_collections] Pulled {col_cnt} collections from Nuvio to {saved_cols_path.relative_to(root_dir)}"
                            )

        if include_nuvio_badges:
            badges_target = configs_dir / "nuvio_badges.json"
            explicit_badges = target.strip().lower() in ("nuvio_badges", "nuvio")
            with console.status("⏳ Pulling Nuvio Stream Badges..."):
                badges_res = _pull_remote_nuvio_badges_doc(configs_dir, working_env, http_client)
            match badges_res:
                case Err(err):
                    if explicit_badges:
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    err_console.print(f"⚠️  [nuvio_badges] {err}")
                case Ok((None, working_env)):
                    if explicit_badges:
                        console.print(
                            "⏭️  [nuvio_badges] Skipping pull: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                        )
                case Ok((scrubbed_badges_doc, working_env)):
                    assert scrubbed_badges_doc is not None
                    has_remote_badges = bool(scrubbed_badges_doc.get("imports"))
                    if has_remote_badges or badges_target.exists() or explicit_badges:
                        if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                            err_console.print(f"❌ {env_err.error}")
                            raise typer.Exit(code=1)
                        match write_json_file(badges_target, scrubbed_badges_doc):
                            case Err(write_err):
                                err_console.print(f"❌ {write_err}")
                                raise typer.Exit(code=1)
                            case Ok(saved_badges_path):
                                imp_cnt = len(scrubbed_badges_doc.get("imports", []))
                                plat_cnt = len(scrubbed_badges_doc.get("platforms", []))
                                console.print(
                                    f"✅ [nuvio_badges] Pulled {imp_cnt} badge pack(s) across {plat_cnt} platform(s) to {saved_badges_path.relative_to(root_dir)}"
                                )

        if include_nuvio_meta:
            meta_target = configs_dir / "nuvio_meta.json"
            explicit_meta = target.strip().lower() in ("nuvio_meta", "nuvio")
            with console.status("⏳ Pulling Nuvio Meta enrichment settings..."):
                meta_res = _pull_remote_nuvio_meta_doc(configs_dir, working_env, http_client)
            match meta_res:
                case Err(err):
                    if explicit_meta:
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    err_console.print(f"⚠️  [nuvio_meta] {err}")
                case Ok((None, working_env)):
                    if explicit_meta:
                        console.print(
                            "⏭️  [nuvio_meta] Skipping pull: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                        )
                case Ok((scrubbed_meta_doc, working_env)):
                    assert scrubbed_meta_doc is not None
                    has_remote_meta = bool(scrubbed_meta_doc.get("platforms"))
                    if has_remote_meta or meta_target.exists() or explicit_meta:
                        if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                            err_console.print(f"❌ {env_err.error}")
                            raise typer.Exit(code=1)
                        match write_json_file(meta_target, scrubbed_meta_doc):
                            case Err(write_err):
                                err_console.print(f"❌ {write_err}")
                                raise typer.Exit(code=1)
                            case Ok(saved_meta_path):
                                plat_cnt = len(scrubbed_meta_doc.get("platforms", []))
                                console.print(
                                    f"✅ [nuvio_meta] Pulled meta enrichment settings synced across {plat_cnt} platform(s) to {saved_meta_path.relative_to(root_dir)}"
                                )

        if include_nuvio_settings:
            settings_target = configs_dir / "nuvio_settings.json"
            explicit_settings = target.strip().lower() in ("nuvio_settings", "nuvio")
            with console.status("⏳ Pulling Nuvio app settings..."):
                settings_res = _pull_remote_nuvio_settings_doc(configs_dir, working_env, http_client)
            match settings_res:
                case Err(err):
                    if explicit_settings:
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    err_console.print(f"⚠️  [nuvio_settings] {err}")
                case Ok((None, working_env)):
                    if explicit_settings:
                        console.print(
                            "⏭️  [nuvio_settings] Skipping pull: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                        )
                case Ok((scrubbed_settings_doc, working_env)):
                    assert scrubbed_settings_doc is not None
                    has_remote_settings = bool(scrubbed_settings_doc.get("platforms"))
                    if has_remote_settings or settings_target.exists() or explicit_settings:
                        if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                            err_console.print(f"❌ {env_err.error}")
                            raise typer.Exit(code=1)
                        match write_json_file(settings_target, scrubbed_settings_doc):
                            case Err(write_err):
                                err_console.print(f"❌ {write_err}")
                                raise typer.Exit(code=1)
                            case Ok(saved_settings_path):
                                plat_cnt = len(scrubbed_settings_doc.get("platforms", []))
                                console.print(
                                    f"✅ [nuvio_settings] Pulled app settings synced across {plat_cnt} platform(s) to {saved_settings_path.relative_to(root_dir)}"
                                )

    match _sync_architecture_diagrams(root_dir, configs_dir):
        case Ok():
            console.print("🗺️  [diagram] Updated architecture/topology.c4 & ARCHITECTURE.md")
        case Err(diag_err):
            err_console.print(f"⚠️  [diagram] {diag_err}")


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
    instance: InstanceNameOption = DEFAULT_INSTANCE,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Inject secrets from .env and push the tenant's documents to remote servers."""
    if dry_run:
        cmd_config_diff(target=target, stat_only=False, exit_code=False, workspace_dir=workspace_dir)
        return

    paths, _manifest, env, bindings = _resolve_context(workspace_dir, tenant, instance)
    root_dir, env_path, configs_dir = paths.root, paths.env_file, paths.docs_dir
    working_env = dict(env)
    svc_targets = _resolve_service_targets(target)

    # Phase 1: upstream addon services (`aiostreams`, `aiometadata`) run concurrently.
    # Phase 2: `aiomanager` runs after Phase 1 so it refreshes the newly updated upstream manifests.
    phase1_targets = tuple(s for s in svc_targets if s != "aiomanager")
    phase2_targets = tuple(s for s in svc_targets if s == "aiomanager")

    with HttpxClient() as http_client:
        services = build_config_services(http_client)
        for phase in (phase1_targets, phase2_targets):
            if not phase:
                continue
            if len(phase) > 1:
                def _run_push(
                    sid: ServiceId,
                ) -> tuple[ServiceId, Result[tuple[ServicePushOutcome | None, dict[str, str]]]]:
                    return (
                        sid,
                        _push_service(
                            sid,
                            dict(working_env),
                            env_path=env_path,
                            configs_dir=configs_dir,
                            services=services,
                            bindings=bindings,
                            create=create,
                        ),
                    )

                with (
                    console.status(f"🚀 Pushing {', '.join(phase)} in parallel..."),
                    concurrent.futures.ThreadPoolExecutor(max_workers=len(phase)) as pool,
                ):
                    phase_results = dict(pool.map(_run_push, phase))
            else:
                only_sid = phase[0]
                with console.status(f"🚀 Pushing {only_sid}..."):
                    phase_results = {
                        only_sid: _push_service(
                            only_sid,
                            working_env,
                            env_path=env_path,
                            configs_dir=configs_dir,
                            services=services,
                            bindings=bindings,
                            create=create,
                        )
                    }

            for svc_id in phase:
                match phase_results[svc_id]:
                    case Err(err):
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    case Ok((None, new_env)):
                        working_env.update(new_env)
                        console.print(
                            f"⏭️  [{svc_id}] Skipping push: missing config file or credentials in .env"
                        )
                    case Ok((outcome, new_env)):
                        working_env.update(new_env)
                        assert outcome is not None
                        if outcome.created:
                            console.print(f"✅ [{svc_id}] Created! New UUID: {outcome.uuid}")
                            if outcome.install_url:
                                console.print(f"🔗 [{svc_id}] Install URL: {outcome.install_url}")
                        else:
                            console.print(
                                f"✅ [{svc_id}] Successfully updated UUID {outcome.uuid} in-place"
                            )
                        for client_msg in outcome.synced_clients:
                            console.print(f"✅ {client_msg}")

        cleaned_target = target.strip().lower()
        if cleaned_target in ("nuvio_badges", "nuvio_meta", "nuvio_settings", "nuvio_collections", "nuvio"):
            match _resolve_nuvio_connection(configs_dir, working_env):
                case Err(err):
                    err_console.print(f"❌ {err}")
                    raise typer.Exit(code=1)
                case Ok(None):
                    console.print(
                        f"⏭️  [{cleaned_target}] Skipping push: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                    )
                case Ok((nuvio_conn, password, salt_b64, addons_list)):
                    nuvio_syncer = NuvioClientSync(http_client)
                    if cleaned_target == "nuvio_badges":
                        badges_path = configs_dir / "nuvio_badges.json"
                        if not badges_path.exists():
                            err_console.print("❌ Missing nuvio_badges.json")
                            raise typer.Exit(code=1)
                        match read_json_file(badges_path):
                            case Err(err):
                                err_console.print(f"❌ {err}")
                                raise typer.Exit(code=1)
                            case Ok(raw_badges):
                                match inject_secrets(raw_badges, working_env):
                                    case Err(err):
                                        err_console.print(f"❌ {err}")
                                        raise typer.Exit(code=1)
                                    case Ok(inj_badges):
                                        with console.status("🚀 Pushing Nuvio Stream Badges..."):
                                            b_res = nuvio_syncer.push_badges(
                                                inj_badges, nuvio_conn, password, salt_b64, working_env
                                            )
                                        match b_res:
                                            case Err(err):
                                                err_console.print(f"❌ {err}")
                                                raise typer.Exit(code=1)
                                            case Ok((msg, refreshed_secrets)):
                                                if refreshed_secrets:
                                                    working_env.update(refreshed_secrets)
                                                    if isinstance(
                                                        s_err := save_env_updates(env_path, working_env),
                                                        Err,
                                                    ):
                                                        err_console.print(f"❌ {s_err.error}")
                                                        raise typer.Exit(code=1)
                                                console.print(f"✅ [nuvio_badges] {msg}")
                    elif cleaned_target == "nuvio_meta":
                        meta_path = configs_dir / "nuvio_meta.json"
                        if not meta_path.exists():
                            err_console.print("❌ Missing nuvio_meta.json")
                            raise typer.Exit(code=1)
                        match read_json_file(meta_path):
                            case Err(err):
                                err_console.print(f"❌ {err}")
                                raise typer.Exit(code=1)
                            case Ok(raw_meta):
                                match inject_secrets(raw_meta, working_env):
                                    case Err(err):
                                        err_console.print(f"❌ {err}")
                                        raise typer.Exit(code=1)
                                    case Ok(inj_meta):
                                        with console.status("🚀 Pushing Nuvio Meta enrichment settings..."):
                                            m_res = nuvio_syncer.push_meta(
                                                inj_meta, nuvio_conn, password, salt_b64, working_env
                                            )
                                        match m_res:
                                            case Err(err):
                                                err_console.print(f"❌ {err}")
                                                raise typer.Exit(code=1)
                                            case Ok((msg, refreshed_secrets)):
                                                if refreshed_secrets:
                                                    working_env.update(refreshed_secrets)
                                                    if isinstance(
                                                        s_err := save_env_updates(env_path, working_env),
                                                        Err,
                                                    ):
                                                        err_console.print(f"❌ {s_err.error}")
                                                        raise typer.Exit(code=1)
                                                console.print(f"✅ [nuvio_meta] {msg}")
                    elif cleaned_target == "nuvio_settings":
                        settings_path = configs_dir / "nuvio_settings.json"
                        if not settings_path.exists():
                            err_console.print("❌ Missing nuvio_settings.json")
                            raise typer.Exit(code=1)
                        match read_json_file(settings_path):
                            case Err(err):
                                err_console.print(f"❌ {err}")
                                raise typer.Exit(code=1)
                            case Ok(raw_settings):
                                match inject_secrets(raw_settings, working_env):
                                    case Err(err):
                                        err_console.print(f"❌ {err}")
                                        raise typer.Exit(code=1)
                                    case Ok(inj_settings):
                                        with console.status("🚀 Pushing Nuvio app settings..."):
                                            st_res = nuvio_syncer.push_settings(
                                                inj_settings, nuvio_conn, password, salt_b64, working_env
                                            )
                                        match st_res:
                                            case Err(err):
                                                err_console.print(f"❌ {err}")
                                                raise typer.Exit(code=1)
                                            case Ok((msg, refreshed_secrets)):
                                                if refreshed_secrets:
                                                    working_env.update(refreshed_secrets)
                                                    if isinstance(
                                                        s_err := save_env_updates(env_path, working_env),
                                                        Err,
                                                    ):
                                                        err_console.print(f"❌ {s_err.error}")
                                                        raise typer.Exit(code=1)
                                                console.print(f"✅ [nuvio_settings] {msg}")
                    else:
                        acc_payload: JsonDict = {"addons": addons_list}
                        cols_path = configs_dir / "nuvio_collections.json"
                        if cols_path.exists():
                            match read_json_file(cols_path):
                                case Err(err):
                                    err_console.print(f"❌ {err}")
                                    raise typer.Exit(code=1)
                                case Ok(raw_cols):
                                    match inject_secrets(raw_cols, working_env):
                                        case Err(err):
                                            err_console.print(f"❌ {err}")
                                            raise typer.Exit(code=1)
                                        case Ok(inj_cols):
                                            cols_list = inj_cols.get("collections")
                                            if isinstance(cols_list, list) and cols_list:
                                                acc_payload["nuvioCollections"] = cols_list
                        badges_path = configs_dir / "nuvio_badges.json"
                        if cleaned_target == "nuvio" and badges_path.exists():
                            match read_json_file(badges_path):
                                case Err(err):
                                    err_console.print(f"❌ {err}")
                                    raise typer.Exit(code=1)
                                case Ok(raw_badges):
                                    match inject_secrets(raw_badges, working_env):
                                        case Err(err):
                                            err_console.print(f"❌ {err}")
                                            raise typer.Exit(code=1)
                                        case Ok(inj_badges):
                                            acc_payload["nuvioBadges"] = inj_badges
                        meta_path = configs_dir / "nuvio_meta.json"
                        if cleaned_target == "nuvio" and meta_path.exists():
                            match read_json_file(meta_path):
                                case Err(err):
                                    err_console.print(f"❌ {err}")
                                    raise typer.Exit(code=1)
                                case Ok(raw_meta):
                                    match inject_secrets(raw_meta, working_env):
                                        case Err(err):
                                            err_console.print(f"❌ {err}")
                                            raise typer.Exit(code=1)
                                        case Ok(inj_meta):
                                            acc_payload["nuvioMeta"] = inj_meta
                        settings_path = configs_dir / "nuvio_settings.json"
                        if cleaned_target == "nuvio" and settings_path.exists():
                            match read_json_file(settings_path):
                                case Err(err):
                                    err_console.print(f"❌ {err}")
                                    raise typer.Exit(code=1)
                                case Ok(raw_settings):
                                    match inject_secrets(raw_settings, working_env):
                                        case Err(err):
                                            err_console.print(f"❌ {err}")
                                            raise typer.Exit(code=1)
                                        case Ok(inj_settings):
                                            acc_payload["nuvioSettings"] = inj_settings
                        with console.status(f"🚀 Pushing {cleaned_target}..."):
                            n_res = nuvio_syncer.sync_account(
                                acc_payload, nuvio_conn, password, salt_b64, working_env
                            )
                        match n_res:
                            case Err(err):
                                err_console.print(f"❌ {err}")
                                raise typer.Exit(code=1)
                            case Ok((msg, refreshed_secrets)):
                                if refreshed_secrets:
                                    working_env.update(refreshed_secrets)
                                    if isinstance(
                                        s_err := save_env_updates(env_path, working_env),
                                        Err,
                                    ):
                                        err_console.print(f"❌ {s_err.error}")
                                        raise typer.Exit(code=1)
                                console.print(f"✅ [{cleaned_target}] {msg}")

    match _sync_architecture_diagrams(root_dir, configs_dir):
        case Ok():
            console.print("🗺️  [diagram] Updated architecture/topology.c4 & ARCHITECTURE.md")
        case Err(diag_err):
            err_console.print(f"⚠️  [diagram] {diag_err}")


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
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Compare the tenant's documents against live remote instances without modifying any files."""
    paths, _manifest, env, bindings = _resolve_context(workspace_dir, tenant, instance)
    configs_dir = paths.docs_dir
    has_drift = False
    svc_targets = _resolve_service_targets(target)
    include_nuvio = _should_include_nuvio_collections(target)
    include_nuvio_badges = _should_include_nuvio_badges(target)
    include_nuvio_meta = _should_include_nuvio_meta(target)
    include_nuvio_settings = _should_include_nuvio_settings(target)

    with HttpxClient() as http_client:
        services = build_config_services(http_client)

        def _diff_one(sid: ServiceId) -> tuple[ServiceId, Result[ConfigDiffOutcome | None]]:
            return sid, _diff_service(
                sid, env, configs_dir=configs_dir, services=services, bindings=bindings
            )

        svc_diff_results: dict[ServiceId, Result[ConfigDiffOutcome | None]] = {}
        nuvio_future: concurrent.futures.Future[Result[tuple[JsonDict | None, dict[str, str]]]] | None = (
            None
        )
        badges_future: concurrent.futures.Future[Result[tuple[JsonDict | None, dict[str, str]]]] | None = (
            None
        )
        meta_future: concurrent.futures.Future[Result[tuple[JsonDict | None, dict[str, str]]]] | None = (
            None
        )
        settings_future: concurrent.futures.Future[Result[tuple[JsonDict | None, dict[str, str]]]] | None = (
            None
        )
        cols_path = configs_dir / "nuvio_collections.json"
        badges_path = configs_dir / "nuvio_badges.json"
        meta_path = configs_dir / "nuvio_meta.json"
        settings_path = configs_dir / "nuvio_settings.json"

        max_workers = max(
            1,
            len(svc_targets)
            + (1 if include_nuvio else 0)
            + (1 if include_nuvio_badges else 0)
            + (1 if include_nuvio_meta else 0)
            + (1 if include_nuvio_settings else 0),
        )
        with (
            console.status("🔍 Comparing local configs against live remote instances in parallel..."),
            concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool,
        ):
            futures = [pool.submit(_diff_one, sid) for sid in svc_targets]
            if include_nuvio and cols_path.exists():
                nuvio_future = pool.submit(
                    _pull_remote_nuvio_collections_doc, configs_dir, env, http_client
                )
            if include_nuvio_badges and badges_path.exists():
                badges_future = pool.submit(
                    _pull_remote_nuvio_badges_doc, configs_dir, env, http_client
                )
            if include_nuvio_meta and meta_path.exists():
                meta_future = pool.submit(
                    _pull_remote_nuvio_meta_doc, configs_dir, env, http_client
                )
            if include_nuvio_settings and settings_path.exists():
                settings_future = pool.submit(
                    _pull_remote_nuvio_settings_doc, configs_dir, env, http_client
                )
            for fut in futures:
                sid, res = fut.result()
                svc_diff_results[sid] = res

        collected_outcomes: list[ConfigDiffOutcome] = []
        skipped_msgs: list[str] = []

        for svc_id in svc_targets:
            match svc_diff_results[svc_id]:
                case Err(err):
                    err_console.print(f"❌ {err}")
                    raise typer.Exit(code=1)
                case Ok(None):
                    skipped_msgs.append(
                        f"⏭️  [{svc_id}] Skipping diff: missing local config or credentials in .env"
                    )
                case Ok(outcome):
                    assert outcome is not None
                    collected_outcomes.append(outcome)
                    if not outcome.in_sync:
                        has_drift = True

        if nuvio_future is not None:
            match read_json_file(cols_path):
                case Err(err):
                    err_console.print(f"❌ {err}")
                    raise typer.Exit(code=1)
                case Ok(local_cols_doc):
                    match nuvio_future.result():
                        case Err(err):
                            if target.strip().lower() in ("nuvio_collections", "nuvio"):
                                err_console.print(f"❌ {err}")
                                raise typer.Exit(code=1)
                        case Ok((remote_cols_doc, _)) if remote_cols_doc is not None:
                            col_diff = compute_config_diff(
                                local_cols_doc,
                                remote_cols_doc,
                                target="nuvio_collections",
                            )
                            collected_outcomes.append(col_diff)
                            if not col_diff.in_sync:
                                has_drift = True
                        case Ok():
                            if target.strip().lower() in ("nuvio_collections", "nuvio"):
                                skipped_msgs.append(
                                    "⏭️  [nuvio_collections] Skipping diff: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                                )

        if badges_future is not None:
            match read_json_file(badges_path):
                case Err(err):
                    err_console.print(f"❌ {err}")
                    raise typer.Exit(code=1)
                case Ok(local_badges_raw):
                    match normalize_nuvio_badges_doc(local_badges_raw):
                        case Err(err):
                            err_console.print(f"❌ {err}")
                            raise typer.Exit(code=1)
                        case Ok(local_badges_doc):
                            match badges_future.result():
                                case Err(err):
                                    if target.strip().lower() in ("nuvio_badges", "nuvio"):
                                        err_console.print(f"❌ {err}")
                                        raise typer.Exit(code=1)
                                case Ok((remote_badges_doc, _)) if remote_badges_doc is not None:
                                    badge_diff = compute_config_diff(
                                        local_badges_doc,
                                        remote_badges_doc,
                                        target="nuvio_badges",
                                    )
                                    collected_outcomes.append(badge_diff)
                                    if not badge_diff.in_sync:
                                        has_drift = True
                                case Ok():
                                    if target.strip().lower() in ("nuvio_badges", "nuvio"):
                                        skipped_msgs.append(
                                            "⏭️  [nuvio_badges] Skipping diff: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                                        )

        if meta_future is not None:
            match read_json_file(meta_path):
                case Err(err):
                    err_console.print(f"❌ {err}")
                    raise typer.Exit(code=1)
                case Ok(local_meta_raw):
                    match normalize_nuvio_meta_doc(local_meta_raw):
                        case Err(err):
                            err_console.print(f"❌ {err}")
                            raise typer.Exit(code=1)
                        case Ok(local_meta_doc):
                            match meta_future.result():
                                case Err(err):
                                    if target.strip().lower() in ("nuvio_meta", "nuvio"):
                                        err_console.print(f"❌ {err}")
                                        raise typer.Exit(code=1)
                                case Ok((remote_meta_doc, _)) if remote_meta_doc is not None:
                                    meta_diff = compute_config_diff(
                                        local_meta_doc,
                                        remote_meta_doc,
                                        target="nuvio_meta",
                                    )
                                    collected_outcomes.append(meta_diff)
                                    if not meta_diff.in_sync:
                                        has_drift = True
                                case Ok():
                                    if target.strip().lower() in ("nuvio_meta", "nuvio"):
                                        skipped_msgs.append(
                                            "⏭️  [nuvio_meta] Skipping diff: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                                        )

        if settings_future is not None:
            match read_json_file(settings_path):
                case Err(err):
                    err_console.print(f"❌ {err}")
                    raise typer.Exit(code=1)
                case Ok(local_settings_raw):
                    match normalize_nuvio_settings_doc(local_settings_raw):
                        case Err(err):
                            err_console.print(f"❌ {err}")
                            raise typer.Exit(code=1)
                        case Ok(local_settings_doc):
                            match settings_future.result():
                                case Err(err):
                                    if target.strip().lower() in ("nuvio_settings", "nuvio"):
                                        err_console.print(f"❌ {err}")
                                        raise typer.Exit(code=1)
                                case Ok((remote_settings_doc, _)) if remote_settings_doc is not None:
                                    settings_diff = compute_config_diff(
                                        local_settings_doc,
                                        remote_settings_doc,
                                        target="nuvio_settings",
                                    )
                                    collected_outcomes.append(settings_diff)
                                    if not settings_diff.in_sync:
                                        has_drift = True
                                case Ok():
                                    if target.strip().lower() in ("nuvio_settings", "nuvio"):
                                        skipped_msgs.append(
                                            "⏭️  [nuvio_settings] Skipping diff: missing Nuvio connection or NUVIO_SUPABASE_ANON_KEY in .env"
                                        )

        for msg in skipped_msgs:
            console.print(msg)
        _render_diff_summary_table(collected_outcomes)
        for outcome in collected_outcomes:
            _print_diff_outcome(outcome, stat_only=stat_only)

    if exit_code and has_drift:
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
    """Normalize, dehydrate, and scrub secrets from the tenant's JSON documents."""
    root_dir, env_path, configs_dir, env = _resolve_paths(workspace_dir, tenant)
    working_env = dict(env)
    found_any = False

    for svc_id in _resolve_service_targets(target):
        match _sanitize_service(
            svc_id, working_env, env_path=env_path, configs_dir=configs_dir, instance=instance
        ):
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

    cols_path = configs_dir / "nuvio_collections.json"
    if _should_include_nuvio_collections(target) and cols_path.exists():
        match read_json_file(cols_path):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok(raw_cols):
                decompiled_cols = decompile_nuvio_collections(raw_cols)
                normalized_doc: JsonDict = {
                    "version": str(raw_cols.get("version") or "1.0.0"),
                    "collections": decompiled_cols if decompiled_cols else raw_cols.get("collections", []),
                }
                scrubbed_cols, working_env = harvest_and_scrub("aiomanager", normalized_doc, working_env)
                if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                    err_console.print(f"❌ {env_err.error}")
                    raise typer.Exit(code=1)
                if isinstance(write_res := write_json_file(cols_path, scrubbed_cols), Ok):
                    found_any = True
                    console.print(
                        f"🧹 [nuvio_collections] Auto-harvested secrets to .env & scrubbed {write_res.value.relative_to(root_dir)}"
                    )

    badges_path = configs_dir / "nuvio_badges.json"
    if _should_include_nuvio_badges(target) and badges_path.exists():
        match read_json_file(badges_path):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok(raw_badges):
                match normalize_nuvio_badges_doc(raw_badges):
                    case Err(err):
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    case Ok(norm_badges):
                        scrubbed_badges, working_env = harvest_and_scrub(
                            "aiomanager", norm_badges, working_env
                        )
                        if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                            err_console.print(f"❌ {env_err.error}")
                            raise typer.Exit(code=1)
                        if isinstance(write_res := write_json_file(badges_path, scrubbed_badges), Ok):
                            found_any = True
                            console.print(
                                f"🧹 [nuvio_badges] Auto-harvested secrets to .env & scrubbed {write_res.value.relative_to(root_dir)}"
                            )

    meta_path = configs_dir / "nuvio_meta.json"
    if _should_include_nuvio_meta(target) and meta_path.exists():
        match read_json_file(meta_path):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok(raw_meta):
                match normalize_nuvio_meta_doc(raw_meta):
                    case Err(err):
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    case Ok(norm_meta):
                        scrubbed_meta, working_env = harvest_and_scrub(
                            "aiomanager", norm_meta, working_env
                        )
                        if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                            err_console.print(f"❌ {env_err.error}")
                            raise typer.Exit(code=1)
                        if isinstance(write_res := write_json_file(meta_path, scrubbed_meta), Ok):
                            found_any = True
                            console.print(
                                f"🧹 [nuvio_meta] Auto-harvested secrets to .env & scrubbed {write_res.value.relative_to(root_dir)}"
                            )

    settings_path = configs_dir / "nuvio_settings.json"
    if _should_include_nuvio_settings(target) and settings_path.exists():
        match read_json_file(settings_path):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok(raw_settings):
                match normalize_nuvio_settings_doc(raw_settings):
                    case Err(err):
                        err_console.print(f"❌ {err}")
                        raise typer.Exit(code=1)
                    case Ok(norm_settings):
                        scrubbed_settings, working_env = harvest_and_scrub(
                            "aiomanager", norm_settings, working_env
                        )
                        if isinstance(env_err := save_env_updates(env_path, working_env), Err):
                            err_console.print(f"❌ {env_err.error}")
                            raise typer.Exit(code=1)
                        if isinstance(write_res := write_json_file(settings_path, scrubbed_settings), Ok):
                            found_any = True
                            console.print(
                                f"🧹 [nuvio_settings] Auto-harvested secrets to .env & scrubbed {write_res.value.relative_to(root_dir)}"
                            )

    if not found_any:
        console.print("ℹ️  No documents found in the tenant to sanitize.")
    else:
        match _sync_architecture_diagrams(root_dir, configs_dir):
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
    root_dir, _, configs_dir, _ = _resolve_paths(workspace_dir, tenant)
    match _sync_architecture_diagrams(root_dir, configs_dir, check_only=check):
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
    _, _, configs_dir, env = _resolve_paths(workspace_dir, tenant)
    cfg_path = configs_dir / "aiomanager.json"
    if not cfg_path.exists():
        err_console.print("❌ Missing aiomanager.json")
        raise typer.Exit(code=1)

    match read_json_file(cfg_path):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(scrubbed_cfg):
            pass

    match inject_secrets(scrubbed_cfg, env):
        case Err(sec_err):
            err_console.print(f"❌ {sec_err}")
            raise typer.Exit(code=1)
        case Ok(injected_cfg):
            pass

    with (
        HttpxClient() as http_client,
        console.status(f"🔥 Pre-warming Home catalog rows ({workers} workers)..."),
    ):
        warmed, total = warm_home_catalogs(injected_cfg, http_client, max_workers=workers)
    console.print(f"🔥 [warm] Pre-warmed {warmed}/{total} Home catalog rows")


def main() -> None:
    """Main CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
    sys.exit(0)
