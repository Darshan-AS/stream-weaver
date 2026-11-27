"""`sw packs` — modular Debrid provider & AIOStreams addon pack toggles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.table import Table

from stream_weaver.core.aiostreams_packs import (
    DEBRID_PROVIDERS,
    STRELINGO_BY_CODE,
    active_debrid_provider,
    inspect_aiostreams_packs,
    inspect_dual_subtitle_pairs,
    inspect_stream_preferences,
)
from stream_weaver.core.result import Err, Ok
from stream_weaver.core.tenant import TENANT_FILE
from stream_weaver.effects.storage import read_json_file
from stream_weaver.engine.workspace import apply_debrid_and_packs

packs_app = typer.Typer(
    help="Inspect and toggle AIOStreams Debrid providers and modular addon packs.",
    no_args_is_help=True,
)

WorkspaceDirOption = Annotated[
    Path | None,
    typer.Option(
        "--dir",
        "-C",
        help=f"Tenant directory containing {TENANT_FILE} and .env (default: CWD if it is a tenant)",
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


@packs_app.command("list")
def cmd_packs_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit structured JSON output for scripts and agents"),
    ] = False,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """List the active Debrid provider and every toggleable AIOStreams addon pack."""
    from stream_weaver.cli.app import _resolve_context, console, err_console

    ctx = _resolve_context(workspace_dir, tenant)
    streams_path = ctx.paths.doc("aiostreams")
    match read_json_file(streams_path):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(streams_doc):
            pass

    debrid_id = active_debrid_provider(streams_doc)
    debrid_spec = DEBRID_PROVIDERS.get(debrid_id)
    debrid_label = f"{debrid_spec.label} ({debrid_spec.secret_key})" if debrid_spec else debrid_id
    prefs = inspect_stream_preferences(streams_doc)
    pairs = inspect_dual_subtitle_pairs(streams_doc)
    pack_statuses = inspect_aiostreams_packs(streams_doc)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "tenant": ctx.manifest.name,
                    "debrid": {
                        "id": debrid_id,
                        "label": debrid_spec.label if debrid_spec else debrid_id,
                        "secretKey": debrid_spec.secret_key if debrid_spec else None,
                    },
                    "streamPolicy": {
                        "maxResolution": prefs.max_resolution,
                        "cachedOnly": prefs.cached_only,
                        "audioLanguages": list(prefs.audio_languages),
                        "dualSubtitlePairs": [{"main": m, "translation": t} for m, t in pairs],
                    },
                    "packs": [
                        {
                            "id": st.spec.id,
                            "title": st.spec.title,
                            "enabled": st.enabled,
                            "presentPresets": list(st.present_presets),
                            "requiredSecrets": list(st.spec.required_secrets),
                        }
                        for st in pack_statuses
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    pairs_label = (
        ", ".join(
            f"{STRELINGO_BY_CODE[m].code2 if m in STRELINGO_BY_CODE else m}→{STRELINGO_BY_CODE[t].code2 if t in STRELINGO_BY_CODE else t}"
            for m, t in pairs
        )
        or "none"
    )
    console.print(f"⚡ Active Debrid Provider: {debrid_label}")
    console.print(
        f"🎞️  Stream Policy: Max {prefs.max_resolution} · {'Cached Only' if prefs.cached_only else 'Cached + Uncached'} · Audio: {', '.join(prefs.audio_languages)} · Dual-Subs: {pairs_label}"
    )

    table = Table(
        title=f"AIOStreams Addon Packs — tenant `{ctx.manifest.name}`",
        box=box.ROUNDED,
        header_style="bold",
    )
    for col in ("Pack", "Title", "Enabled", "Presets", "Extra Secrets"):
        table.add_column(col, overflow="fold")
    for st in pack_statuses:
        table.add_row(
            st.spec.id,
            st.spec.title,
            "✅" if st.enabled else "—",
            ", ".join(st.present_presets) or "—",
            ", ".join(st.spec.required_secrets) or "—",
        )
    console.print(table)


@packs_app.command("set")
def cmd_packs_set(
    debrid: Annotated[
        str | None,
        typer.Option("--debrid", help="Switch Debrid provider (torbox | realdebrid | alldebrid | premiumize)"),
    ] = None,
    enable: Annotated[
        list[str] | None,
        typer.Option("--enable", "-e", help="Enable an AIOStreams pack (repeatable)"),
    ] = None,
    disable: Annotated[
        list[str] | None,
        typer.Option("--disable", "-d", help="Disable an AIOStreams pack (repeatable)"),
    ] = None,
    max_resolution: Annotated[
        str | None,
        typer.Option("--max-resolution", help="Cap stream resolution (2160p | 1080p | 720p)"),
    ] = None,
    cached_only: Annotated[
        bool | None,
        typer.Option(
            "--cached-only/--allow-uncached",
            help="Require instant-play cached Debrid streams only, or allow uncached torrents",
        ),
    ] = None,
    audio_langs: Annotated[
        str | None,
        typer.Option(
            "--audio-langs",
            help="Comma-separated stream audio languages (e.g. 'English,Spanish,Japanese')",
        ),
    ] = None,
    dual_subs: Annotated[
        str | None,
        typer.Option(
            "--dual-subs",
            help="Comma-separated Strelingo pairs as MAIN:TRANS (e.g. 'jpn:eng,kor:eng,spa:eng')",
        ),
    ] = None,
    workspace_dir: WorkspaceDirOption = None,
    tenant: TenantNameOption = None,
) -> None:
    """Switch Debrid provider, toggle packs, set stream filters, or configure Strelingo dual-sub pairs."""
    from stream_weaver.cli.app import (
        _resolve_context,
        _resolve_tenant,
        _sync_diagrams_for_ctx,
        console,
        err_console,
    )

    paths = _resolve_tenant(workspace_dir, tenant)
    match read_json_file(paths.doc("aiostreams")):
        case Err(read_err):
            err_console.print(f"❌ {read_err}")
            raise typer.Exit(code=1)
        case Ok(streams_doc):
            pass

    next_packs: list[str] | None = None
    if enable or disable:
        current = {st.spec.id for st in inspect_aiostreams_packs(streams_doc) if st.enabled}
        current |= {p.strip().lower() for p in (enable or []) if p.strip()}
        current -= {p.strip().lower() for p in (disable or []) if p.strip()}
        next_packs = sorted(current)

    parsed_pairs: list[tuple[str, str]] | None = None
    if dual_subs is not None:
        parsed_pairs = []
        for token in (t.strip() for t in dual_subs.split(",") if t.strip()):
            if ":" not in token:
                err_console.print(
                    f"❌ Invalid --dual-subs token `{token}` (expected MAIN:TRANS, e.g. jpn:eng)"
                )
                raise typer.Exit(code=1)
            m_code, _, t_code = token.partition(":")
            parsed_pairs.append((m_code.strip(), t_code.strip()))

    parsed_audio: list[str] | None = (
        [t.strip() for t in audio_langs.split(",") if t.strip()]
        if audio_langs is not None
        else None
    )

    match apply_debrid_and_packs(
        paths,
        debrid=debrid,
        enabled_packs=next_packs,
        max_resolution=max_resolution,
        cached_only=cached_only,
        audio_languages=parsed_audio,
        dual_subtitle_pairs=parsed_pairs,
    ):
        case Err(pack_err):
            err_console.print(f"❌ {pack_err}")
            raise typer.Exit(code=1)
        case Ok():
            pass

    ctx = _resolve_context(paths.root)
    match _sync_diagrams_for_ctx(ctx):
        case Ok():
            pass
        case Err(diag_err):
            err_console.print(f"⚠️  [diagram] {diag_err}")
    console.print("✅ Updated AIOStreams Debrid/packs configuration and regenerated architecture docs.")
