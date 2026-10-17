"""Stream Weaver CLI (`sw`) — Pure Functional GitOps Control Plane for AIOStreams, AIOMetadata, and AIOManager."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from stream_weaver.core.coercions import normalize_service_config
from stream_weaver.core.crypto import decrypt_envelope
from stream_weaver.core.errors import StreamWeaverError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets
from stream_weaver.core.types import (
    ALL_SERVICES,
    JsonDict,
    ServiceId,
    ServicePushOutcome,
)
from stream_weaver.effects.http import HttpxClient
from stream_weaver.effects.storage import (
    load_env_file,
    read_json_file,
    save_env_updates,
    write_json_file,
)
from stream_weaver.providers.protocols import ConfigServiceProtocol
from stream_weaver.providers.services import build_config_services

app = typer.Typer(
    name="sw",
    help="Stream Weaver — Pure Functional GitOps Control Plane for AIOStreams, AIOMetadata, AIOManager, Nuvio & Stremio.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Manage AIOStreams, AIOMetadata, and AIOManager GitOps configurations.")
app.add_typer(config_app, name="config")

console = Console(markup=False, highlight=False)
err_console = Console(stderr=True, markup=False, highlight=False)


def _resolve_paths() -> tuple[Path, Path, Path, dict[str, str]]:
    root_dir = Path(__file__).resolve().parents[3]
    env_path = root_dir / ".env"
    configs_dir = root_dir / "configs"
    return root_dir, env_path, configs_dir, load_env_file(env_path)


def _resolve_service_targets(target: str) -> tuple[ServiceId, ...]:
    cleaned = target.strip().lower()
    if cleaned == "all":
        return ALL_SERVICES
    if cleaned in ("aiostreams", "aiometadata", "aiomanager"):
        return (cleaned,)  # type: ignore[return-value]
    err_console.print(
        f"❌ Unknown service target '{target}'. Choose from: all, aiostreams, aiometadata, aiomanager"
    )
    raise typer.Exit(code=1)


def _persist_scrubbed_config(
    service: ServiceId,
    raw_config: JsonDict,
    env: dict[str, str],
    *,
    env_path: Path,
    configs_dir: Path,
) -> Result[tuple[Path, dict[str, str]], StreamWeaverError]:
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
) -> Result[tuple[Path | None, dict[str, str]], StreamWeaverError]:
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


def _push_service(
    service_id: ServiceId,
    env: dict[str, str],
    *,
    env_path: Path,
    configs_dir: Path,
    services: Mapping[ServiceId, ConfigServiceProtocol],
    create: bool = False,
) -> Result[tuple[ServicePushOutcome | None, dict[str, str]], StreamWeaverError]:
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
) -> Result[tuple[Path | None, dict[str, str]], StreamWeaverError]:
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


# ============================================================================
# CLI Commands (`sw pull`, `sw push`, `sw sanitize`)
# ============================================================================


@config_app.command("pull")
@app.command("pull")
def cmd_config_pull(
    target: Annotated[
        str,
        typer.Argument(help="Service to pull: all, aiostreams, aiometadata, or aiomanager"),
    ] = "all",
) -> None:
    """Pull live configs from remote instances, harvest secrets to .env, and scrub JSON files."""
    root_dir, env_path, configs_dir, env = _resolve_paths()
    services = build_config_services(HttpxClient())
    working_env = dict(env)

    for svc_id in _resolve_service_targets(target):
        match _pull_service(
            svc_id, working_env, env_path=env_path, configs_dir=configs_dir, services=services
        ):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok((None, working_env)):
                console.print(f"⏭️  [{svc_id}] Skipping pull: missing URL, UUID, or password in .env")
            case Ok((saved_path, working_env)):
                assert saved_path is not None
                uid = working_env.get(f"{svc_id.upper()}_UUID", "")
                console.print(
                    f"✅ [{svc_id}] Saved scrubbed config for UUID {uid} to {saved_path.relative_to(root_dir)}"
                )


@config_app.command("push")
@app.command("push")
def cmd_config_push(
    target: Annotated[
        str,
        typer.Argument(help="Service to push: all, aiostreams, aiometadata, or aiomanager"),
    ] = "all",
    create: Annotated[
        bool,
        typer.Option("--create", help="Create a brand-new UUID on the target server"),
    ] = False,
) -> None:
    """Inject secrets from .env and push local configs/ to remote servers."""
    _, env_path, configs_dir, env = _resolve_paths()
    services = build_config_services(HttpxClient())
    working_env = dict(env)

    for svc_id in _resolve_service_targets(target):
        match _push_service(
            svc_id,
            working_env,
            env_path=env_path,
            configs_dir=configs_dir,
            services=services,
            create=create,
        ):
            case Err(err):
                err_console.print(f"❌ {err}")
                raise typer.Exit(code=1)
            case Ok((None, working_env)):
                console.print(f"⏭️  [{svc_id}] Skipping push: missing config file or credentials in .env")
            case Ok((outcome, working_env)):
                assert outcome is not None
                if outcome.created:
                    console.print(f"✅ [{svc_id}] Created! New UUID: {outcome.uuid}")
                    if outcome.install_url:
                        console.print(f"🔗 [{svc_id}] Install URL: {outcome.install_url}")
                else:
                    console.print(f"✅ [{svc_id}] Successfully updated UUID {outcome.uuid} in-place")
                for client_msg in outcome.synced_clients:
                    console.print(f"✅ {client_msg}")


@config_app.command("sanitize")
@app.command("sanitize")
def cmd_config_sanitize(
    target: Annotated[
        str,
        typer.Argument(help="Service file to sanitize: all, aiostreams, aiometadata, or aiomanager"),
    ] = "all",
) -> None:
    """Normalize, dehydrate, and scrub secrets from local JSON files in configs/."""
    root_dir, env_path, configs_dir, env = _resolve_paths()
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

    if not found_any:
        console.print("ℹ️  No config files found in configs/ to sanitize.")


def main() -> None:
    """Main CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
    sys.exit(0)
