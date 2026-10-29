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
from stream_weaver.core.nuvio_badges import normalize_nuvio_badges_doc
from stream_weaver.core.nuvio_meta import normalize_nuvio_meta_doc
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets
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

WorkspaceDirOption = Annotated[
    Path | None,
    typer.Option(
        "--dir",
        "-C",
        help="Workspace directory containing .env and configs/ (defaults to CWD if initialized, else repo root)",
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
) -> None:
    """Global options for the Stream Weaver CLI."""
    global _CLI_WORKSPACE_DIR
    _CLI_WORKSPACE_DIR = workspace_dir


def resolve_workspace_root(
    explicit_dir: Path | None = None,
    *,
    cwd: Path | None = None,
    env_override: str | None = None,
) -> Path:
    """
    Resolves the active Stream Weaver state directory using priority:
    1. Explicit `--dir` / `-C` CLI option (either on subcommand or global callback)
    2. `STREAM_WEAVER_DIR` environment variable
    3. Current working directory if it contains `configs/` or `.stream-weaver`
    4. Package repository root fallback (for in-repo personal GitOps)
    """
    chosen = explicit_dir or _CLI_WORKSPACE_DIR
    if chosen is not None:
        return chosen.expanduser().resolve()

    raw_env_dir = env_override if env_override is not None else os.environ.get("STREAM_WEAVER_DIR", "")
    if raw_env_dir.strip():
        return Path(raw_env_dir.strip()).expanduser().resolve()

    effective_cwd = (cwd or Path.cwd()).resolve()
    if (effective_cwd / "configs").is_dir() or (effective_cwd / ".stream-weaver").exists():
        return effective_cwd

    return Path(__file__).resolve().parents[3]


def _resolve_paths(
    workspace_dir: Path | None = None,
) -> tuple[Path, Path, Path, dict[str, str]]:
    root_dir = resolve_workspace_root(workspace_dir)
    env_path = root_dir / ".env"
    configs_dir = root_dir / "configs"
    return root_dir, env_path, configs_dir, load_env_file(env_path)


def _resolve_service_targets(target: str) -> tuple[ServiceId, ...]:
    cleaned = target.strip().lower()
    if cleaned == "all":
        return ALL_SERVICES
    if cleaned in ("nuvio_collections", "nuvio_badges", "nuvio_meta", "nuvio"):
        return ()
    if cleaned in ("aiostreams", "aiometadata", "aiomanager"):
        return (cleaned,)  # type: ignore[return-value]
    err_console.print(
        f"❌ Unknown service target '{target}'. Choose from: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, nuvio_meta"
    )
    raise typer.Exit(code=1)


def _should_include_nuvio_collections(target: str) -> bool:
    return target.strip().lower() in ("all", "aiomanager", "nuvio_collections", "nuvio")


def _should_include_nuvio_badges(target: str) -> bool:
    return target.strip().lower() in ("all", "aiomanager", "nuvio_badges", "nuvio")


def _should_include_nuvio_meta(target: str) -> bool:
    return target.strip().lower() in ("all", "aiomanager", "nuvio_meta", "nuvio")


def _resolve_nuvio_connection(
    configs_dir: Path,
    env: Mapping[str, str],
) -> Result[tuple[Mapping[str, Any], str, str, list[Any]] | None]:
    """Resolves the active Nuvio connection, master password, salt, and addons from `configs/aiomanager.json`."""
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


def _persist_scrubbed_config(
    service: ServiceId,
    raw_config: JsonDict,
    env: dict[str, str],
    *,
    env_path: Path,
    configs_dir: Path,
) -> Result[tuple[Path, dict[str, str]]]:
    scrubbed_cfg, updated_env = harvest_and_scrub(service, raw_config, env)
    match save_env_updates(env_path, updated_env):
        case Err() as err:
            return err
        case Ok():
            pass

    target_path = configs_dir / f"{service}.json"
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
) -> Result[tuple[Path | None, dict[str, str]]]:
    svc = services.get(service_id)
    if svc is None or not svc.is_configured_for_pull(env):
        return Ok((None, env))

    match svc.pull_remote(env):
        case Err() as err:
            return err
        case Ok(normalized_cfg):
            return _persist_scrubbed_config(
                service_id, normalized_cfg, env, env_path=env_path, configs_dir=configs_dir
            )


def _diff_service(
    service_id: ServiceId,
    env: Mapping[str, str],
    *,
    configs_dir: Path,
    services: Mapping[ServiceId, ConfigServiceProtocol],
) -> Result[ConfigDiffOutcome | None]:
    """Pulls remote service config in memory, scrubs secrets, and diffs against `configs/{service_id}.json`."""
    cfg_path = configs_dir / f"{service_id}.json"
    svc = services.get(service_id)
    if not cfg_path.exists() or svc is None or not svc.is_configured_for_pull(env):
        return Ok(None)

    match read_json_file(cfg_path):
        case Err() as err:
            return err
        case Ok(local_scrubbed):
            pass

    match svc.pull_remote(env):
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
    create: bool = False,
) -> Result[tuple[ServicePushOutcome | None, dict[str, str]]]:
    cfg_path = configs_dir / f"{service_id}.json"
    svc = services.get(service_id)
    if not cfg_path.exists() or svc is None or not svc.is_configured_for_push(env, create=create):
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

    match svc.push_remote(injected_cfg, env, create=create):
        case Err() as err:
            return err
        case Ok((outcome, updated_cfg, discovered_secrets)):
            pass

    working_env = {**env, **discovered_secrets}
    if service_id == "aiomanager":
        match _persist_scrubbed_config(
            service_id, updated_cfg, working_env, env_path=env_path, configs_dir=configs_dir
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
) -> Result[tuple[Path | None, dict[str, str]]]:
    cfg_path = configs_dir / f"{service_id}.json"
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
                service_id, normalized, env, env_path=env_path, configs_dir=configs_dir
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
    """Compiles LikeC4 (`architecture/topology.c4`) and `ARCHITECTURE.md` from `configs/*.json`."""
    bundle = compile_architecture_bundle(
        aiomanager=_load_optional_json(configs_dir / "aiomanager.json"),
        aiostreams=_load_optional_json(configs_dir / "aiostreams.json"),
        aiometadata=_load_optional_json(configs_dir / "aiometadata.json"),
        nuvio_collections=_load_optional_json(configs_dir / "nuvio_collections.json"),
        nuvio_badges=_load_optional_json(configs_dir / "nuvio_badges.json"),
        nuvio_meta=_load_optional_json(configs_dir / "nuvio_meta.json"),
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


@config_app.command("init")
@app.command("init")
def cmd_config_init(
    directory: Annotated[
        Path | None,
        typer.Argument(help="Target directory to initialize as a Stream Weaver state repo"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite .env.example and .gitignore templates if present"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
) -> None:
    """Scaffold a new Stream Weaver GitOps state directory (configs/, .env, .gitignore)."""
    target_dir = (directory or workspace_dir or _CLI_WORKSPACE_DIR or Path.cwd()).expanduser().resolve()
    configs_dir = target_dir / "configs"
    arch_dir = target_dir / "architecture"
    configs_dir.mkdir(parents=True, exist_ok=True)
    arch_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    preserved: list[str] = []

    for rel_name, template_text, allow_force in (
        (".gitignore", _INIT_GITIGNORE_TEMPLATE, force),
        (".env.example", _INIT_ENV_TEMPLATE, force),
        (".env", _INIT_ENV_TEMPLATE, False),
    ):
        dest = target_dir / rel_name
        if dest.exists() and not allow_force:
            preserved.append(rel_name)
            continue
        match write_text_file(dest, template_text):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok():
                created.append(rel_name)

    console.print(f"🧶 Initialized Stream Weaver workspace at {target_dir}")
    if created:
        console.print(f"   ✅ Created:   {', '.join(created)}, configs/, architecture/")
    if preserved:
        console.print(f"   ℹ️  Preserved: {', '.join(preserved)}")
    console.print("   💡 Next: Fill in your instance URLs & credentials in .env, then run `sw pull`.")


@config_app.command("pull")
@app.command("pull")
def cmd_config_pull(
    target: Annotated[
        str,
        typer.Argument(help="Service to pull: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, or nuvio_meta"),
    ] = "all",
    workspace_dir: WorkspaceDirOption = None,
) -> None:
    """Pull live configs from remote instances, harvest secrets to .env, and scrub JSON files."""
    root_dir, env_path, configs_dir, env = _resolve_paths(workspace_dir)
    working_env = dict(env)
    svc_targets = _resolve_service_targets(target)
    include_nuvio = _should_include_nuvio_collections(target)
    include_nuvio_badges = _should_include_nuvio_badges(target)
    include_nuvio_meta = _should_include_nuvio_meta(target)

    with HttpxClient() as http_client:
        services = build_config_services(http_client)
        configured_svcs: list[ServiceId] = []
        skipped_svcs: list[ServiceId] = []
        for svc_id in svc_targets:
            svc = services.get(svc_id)
            if svc is None or not svc.is_configured_for_pull(working_env):
                skipped_svcs.append(svc_id)
            else:
                configured_svcs.append(svc_id)

        for svc_id in skipped_svcs:
            console.print(f"⏭️  [{svc_id}] Skipping pull: missing URL, UUID, or password in .env")

        pulled_raw: dict[ServiceId, Result[JsonDict]] = {}
        if configured_svcs:
            def _fetch_remote_svc(sid: ServiceId) -> tuple[ServiceId, Result[JsonDict]]:
                return sid, services[sid].pull_remote(working_env)

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
                            uid = working_env.get(f"{svc_id.upper()}_UUID", "")
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
        typer.Argument(help="Service to push: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, or nuvio_meta"),
    ] = "all",
    create: Annotated[
        bool,
        typer.Option("--create", help="Create a brand-new UUID on the target server"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview config diff against remote without pushing"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
) -> None:
    """Inject secrets from .env and push local configs/ to remote servers."""
    if dry_run:
        cmd_config_diff(target=target, stat_only=False, exit_code=False, workspace_dir=workspace_dir)
        return

    root_dir, env_path, configs_dir, env = _resolve_paths(workspace_dir)
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
        if cleaned_target in ("nuvio_badges", "nuvio_meta", "nuvio_collections", "nuvio"):
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
                            err_console.print("❌ Missing configs/nuvio_badges.json")
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
                            err_console.print("❌ Missing configs/nuvio_meta.json")
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
        typer.Argument(help="Service to diff: all, aiostreams, aiometadata, aiomanager, nuvio, nuvio_collections, nuvio_badges, or nuvio_meta"),
    ] = "all",
    stat_only: Annotated[
        bool,
        typer.Option("--stat", help="Show only changed keys and line counts without full unified diff"),
    ] = False,
    exit_code: Annotated[
        bool,
        typer.Option("--exit-code", help="Exit with code 1 if any drift is detected"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
) -> None:
    """Compare local configs/*.json against live remote instances without modifying any files."""
    _, _, configs_dir, env = _resolve_paths(workspace_dir)
    has_drift = False
    svc_targets = _resolve_service_targets(target)
    include_nuvio = _should_include_nuvio_collections(target)
    include_nuvio_badges = _should_include_nuvio_badges(target)
    include_nuvio_meta = _should_include_nuvio_meta(target)

    with HttpxClient() as http_client:
        services = build_config_services(http_client)

        def _diff_one(sid: ServiceId) -> tuple[ServiceId, Result[ConfigDiffOutcome | None]]:
            return sid, _diff_service(sid, env, configs_dir=configs_dir, services=services)

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
        cols_path = configs_dir / "nuvio_collections.json"
        badges_path = configs_dir / "nuvio_badges.json"
        meta_path = configs_dir / "nuvio_meta.json"

        max_workers = max(
            1,
            len(svc_targets)
            + (1 if include_nuvio else 0)
            + (1 if include_nuvio_badges else 0)
            + (1 if include_nuvio_meta else 0),
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
    workspace_dir: WorkspaceDirOption = None,
) -> None:
    """Normalize, dehydrate, and scrub secrets from local JSON files in configs/."""
    root_dir, env_path, configs_dir, env = _resolve_paths(workspace_dir)
    working_env = dict(env)
    found_any = False

    for svc_id in _resolve_service_targets(target):
        match _sanitize_service(
            svc_id, working_env, env_path=env_path, configs_dir=configs_dir
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

    if not found_any:
        console.print("ℹ️  No config files found in configs/ to sanitize.")
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
) -> None:
    """Compile LikeC4 (architecture/topology.c4) & ARCHITECTURE.md from configs/*.json."""
    root_dir, _, configs_dir, _ = _resolve_paths(workspace_dir)
    match _sync_architecture_diagrams(root_dir, configs_dir, check_only=check):
        case Err(err):
            err_console.print(f"❌ {err}")
            raise typer.Exit(code=1)
        case Ok(paths):
            if check:
                if paths:
                    rel_paths = ", ".join(str(p.relative_to(root_dir)) for p in paths)
                    err_console.print(f"❌ [diagram] Out of sync with configs/*.json: {rel_paths}")
                    raise typer.Exit(code=1)
                console.print("✅ [diagram] LikeC4 topology & ARCHITECTURE.md are in sync with configs/*.json")
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
) -> None:
    """Concurrently pre-warm all Home catalog endpoints across enabled addons."""
    _, _, configs_dir, env = _resolve_paths(workspace_dir)
    cfg_path = configs_dir / "aiomanager.json"
    if not cfg_path.exists():
        err_console.print("❌ Missing configs/aiomanager.json")
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
