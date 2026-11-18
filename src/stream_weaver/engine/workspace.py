"""
UI-agnostic workspace engine — powers `sw studio` (both local GitOps mode and stateless web mode),
unified CLI operations (`push`, `pull`, `diff`, `packs`, `doctor`), and programmatic integrations
without any `typer` or `rich` terminal coupling.
"""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import io
import json
import re
import shutil
import threading
import urllib.parse
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from stream_weaver.cli.nuvio import (
    ALL_NUVIO_DOCS,
    NUVIO_TARGET_ALIASES,
    NuvioDoc,
    local_nuvio_docs,
    nuvio_targets,
    profile_doc_after_pull,
    pull_nuvio,
    push_nuvio,
)
from stream_weaver.core.aiomanager_accounts import (
    ResolvedProfile,
    apply_managed_accounts,
)
from stream_weaver.core.aiomanager_power import (
    DEFAULT_CINEMETA_CONFIG,
    apply_aiomanager_power,
    inspect_aiomanager_power,
    inspect_extra_addons,
    normalize_extra_addons,
)
from stream_weaver.core.aiometadata_power import (
    apply_aiometadata_power,
    inspect_aiometadata_power,
    required_aiometadata_power_secrets,
)
from stream_weaver.core.aiostreams_packs import (
    DEBRID_PROVIDERS,
    RESOLUTION_CAPS,
    STREAM_AUDIO_LANGUAGES,
    STRELINGO_LANGUAGES,
    active_debrid_provider,
    apply_aiostreams_packs,
    apply_debrid_provider,
    apply_dual_subtitle_pairs,
    apply_stream_preferences,
    inspect_aiostreams_packs,
    inspect_dual_subtitle_pairs,
    inspect_stream_preferences,
    prepare_aiometadata_for_push,
    prepare_aiostreams_for_push,
    sync_aiomanager_cached_debrid_catalog,
    sync_watchlist_folder_recipe,
)
from stream_weaver.core.aiostreams_power import (
    apply_advanced_stream_filters,
    apply_custom_stream_addons,
    apply_debrid_services,
    apply_stream_formatter,
    inspect_advanced_stream_filters,
    inspect_custom_stream_addons,
    inspect_debrid_services,
    inspect_stream_formatter,
    required_multi_debrid_secrets,
)
from stream_weaver.core.architecture import compile_architecture_bundle
from stream_weaver.core.coercions import (
    compute_config_diff,
    dehydrate_aiomanager,
    normalize_service_config,
)
from stream_weaver.core.compile import CompiledProfile, compile_profile, derive_instance_catalogs
from stream_weaver.core.composition import config_catalog_keys, symbolic_source
from stream_weaver.core.custom_builder import (
    build_mdblist_source,
    build_tmdb_discover_sources,
    custom_builder_metadata,
    parse_mdblist_reference,
)
from stream_weaver.core.doctor import inspect_addon_manifest, run_health_checks
from stream_weaver.core.errors import StreamWeaverError
from stream_weaver.core.instances import (
    DEFAULT_INSTANCE,
    InstanceBinding,
    instance_env_keys,
    resolve_instance_binding,
)
from stream_weaver.core.library import Library, RecipeUiSummary, describe_recipe
from stream_weaver.core.logins import LoginBinding, login_env_key, resolve_login_binding
from stream_weaver.core.profiles import (
    Appearance,
    ExtraAddon,
    ExtraCatalogSpec,
    NuvioClientSpec,
    Profile,
    StremioClientSpec,
    parse_profile,
    profile_dir,
    profile_file,
    profile_to_doc,
    stable_account_id,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import SECRET_PLACEHOLDER_RE, harvest_and_scrub, inject_secrets
from stream_weaver.core.template import ParamSchema
from stream_weaver.core.tenant import TenantManifest, TenantPaths, parse_tenant_manifest
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
    save_env_updates,
    scaffold_env_keys,
    write_json_file,
    write_text_file,
)
from stream_weaver.providers.services import (
    build_config_services,
    extract_home_catalog_urls,
    refresh_addon_manifests,
    warm_home_catalogs,
)
from stream_weaver.providers.services.nuvio_session import (
    NuvioSessionPool,
    password_grant,
    resolve_nuvio_anon_key,
)

_PROFILE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_STRICT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class EnvKeySpec:
    key: str
    label: str
    group: str
    is_secret: bool = True
    signup_url: str = ""
    hint: str = ""


KNOWN_ENV_KEYS: tuple[EnvKeySpec, ...] = (
    EnvKeySpec(
        "AIOSTREAMS_URL",
        "AIOStreams Server URL",
        "instances",
        is_secret=False,
        hint="https://aiostreams.elfhosted.com",
    ),
    EnvKeySpec("AIOSTREAMS_UUID", "AIOStreams UUID", "instances", is_secret=False, hint="Auto-filled by Provision"),
    EnvKeySpec("AIOSTREAMS_PASSWORD", "AIOStreams Password", "instances", hint="Choose a strong password"),
    EnvKeySpec(
        "AIOMETADATA_URL",
        "AIOMetadata Server URL",
        "instances",
        is_secret=False,
        hint="https://aiometadata.elfhosted.com",
    ),
    EnvKeySpec("AIOMETADATA_UUID", "AIOMetadata UUID", "instances", is_secret=False, hint="Auto-filled by Provision"),
    EnvKeySpec("AIOMETADATA_PASSWORD", "AIOMetadata Password", "instances", hint="Choose a strong password"),
    EnvKeySpec(
        "SECRET_TORBOX_API_KEY",
        "TorBox API Key",
        "debrid",
        signup_url="https://torbox.app/settings",
    ),
    EnvKeySpec(
        "SECRET_RD_API_KEY",
        "Real-Debrid API Token",
        "debrid",
        signup_url="https://real-debrid.com/apitoken",
    ),
    EnvKeySpec(
        "SECRET_ALLDEBRID_API_KEY",
        "AllDebrid API Key",
        "debrid",
        signup_url="https://alldebrid.com/apikeys/",
    ),
    EnvKeySpec(
        "SECRET_PREMIUMIZE_API_KEY",
        "Premiumize API Key",
        "debrid",
        signup_url="https://www.premiumize.me/account",
    ),
    EnvKeySpec(
        "SECRET_EASYNEWS_USERNAME",
        "Easynews Username (Usenet)",
        "debrid",
        signup_url="https://account.easynews.com",
    ),
    EnvKeySpec(
        "SECRET_EASYNEWS_PASSWORD",
        "Easynews Password (Usenet)",
        "debrid",
        signup_url="https://account.easynews.com",
    ),
    EnvKeySpec(
        "SECRET_TMDB_API_KEY",
        "TMDB API Key (v3)",
        "metadata",
        signup_url="https://www.themoviedb.org/settings/api",
    ),
    EnvKeySpec(
        "SECRET_TMDB_ACCESS_TOKEN",
        "TMDB Read Access Token (v4)",
        "metadata",
        signup_url="https://www.themoviedb.org/settings/api",
    ),
    EnvKeySpec(
        "SECRET_TVDB_API_KEY",
        "TheTVDB v4 API Key",
        "metadata",
        signup_url="https://thetvdb.com/api-information",
    ),
    EnvKeySpec(
        "SECRET_RPDB_API_KEY",
        "RatingPosterDB Key",
        "metadata",
        signup_url="https://ratingposterdb.com",
        hint="Use t0-free-rpdb on free tier",
    ),
    EnvKeySpec(
        "SECRET_TOP_POSTER_API_KEY",
        "TopPosters API Key",
        "metadata",
        signup_url="https://api.top-streaming.stream",
    ),
    EnvKeySpec(
        "SECRET_MDBLIST_API_KEY",
        "MDBList API Key (8-Source Ratings)",
        "metadata",
        signup_url="https://mdblist.com/preferences",
    ),
    EnvKeySpec(
        "SECRET_GEMINI_API_KEY",
        "Google Gemini API Key (AI Search)",
        "metadata",
        signup_url="https://aistudio.google.com/apikey",
    ),
    EnvKeySpec(
        "SECRET_OPENROUTER_API_KEY",
        "OpenRouter API Key (AI Search)",
        "metadata",
        signup_url="https://openrouter.ai/keys",
    ),
    EnvKeySpec(
        "SECRET_TRAKT_TOKEN_ID",
        "AIOMetadata Trakt Token ID",
        "personal",
        hint="Generated via Trakt OAuth on your AIOMetadata Configure page",
    ),
    EnvKeySpec(
        "SECRET_BINGECAT_TOKEN",
        "BingeCat Profile Token",
        "personal",
        signup_url="https://bingecat.com",
        hint="Token segment from your bingecat.com/stremio/<token>/manifest.json URL",
    ),
    EnvKeySpec(
        "SECRET_ITSOUT_TOKEN",
        "ItsOut! Release Alert Token",
        "personal",
        signup_url="https://itsout.elfhosted.com/configure",
    ),
    EnvKeySpec(
        "SECRET_SUBSOURCE_API_KEY",
        "SubSource API Key (Dual-Subs)",
        "subtitles",
        signup_url="https://subsource.net",
    ),
    EnvKeySpec("AIOMANAGER_URL", "AIOManager URL (Optional)", "aiomanager", is_secret=False),
    EnvKeySpec("AIOMANAGER_UUID", "AIOManager Sync UUID (Optional)", "aiomanager", is_secret=False),
    EnvKeySpec("AIOMANAGER_SYNC_PASSWORD", "AIOManager Sync Password (Optional)", "aiomanager"),
)

_ENV_SPECS_BY_KEY: Mapping[str, EnvKeySpec] = {s.key: s for s in KNOWN_ENV_KEYS}

_SAMPLE_MOVIE_CARDS: tuple[JsonDict, ...] = (
    {
        "id": "tt15239678",
        "title": "Dune: Part Two",
        "year": "2024",
        "posterUrl": "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
        "rating": "8.5",
        "type": "movie",
    },
    {
        "id": "tt15398776",
        "title": "Oppenheimer",
        "year": "2023",
        "posterUrl": "https://image.tmdb.org/t/p/w500/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg",
        "rating": "8.3",
        "type": "movie",
    },
    {
        "id": "tt9362722",
        "title": "Spider-Man: Across the Spider-Verse",
        "year": "2023",
        "posterUrl": "https://image.tmdb.org/t/p/w500/8Vt6mWEReuy4Of61Lnj5Xj704m8.jpg",
        "rating": "8.6",
        "type": "movie",
    },
    {
        "id": "tt0245429",
        "title": "Spirited Away",
        "year": "2001",
        "posterUrl": "https://image.tmdb.org/t/p/w500/39wmItIWsg5sZMyRUHLkWBcuVCM.jpg",
        "rating": "8.6",
        "type": "movie",
    },
    {
        "id": "tt0816692",
        "title": "Interstellar",
        "year": "2014",
        "posterUrl": "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg",
        "rating": "8.7",
        "type": "movie",
    },
    {
        "id": "tt6710474",
        "title": "Everything Everywhere All at Once",
        "year": "2022",
        "posterUrl": "https://image.tmdb.org/t/p/w500/w3LxiVYdWWRvEVdn5RYq6jIqkb1.jpg",
        "rating": "7.8",
        "type": "movie",
    },
    {
        "id": "tt29623480",
        "title": "The Wild Robot",
        "year": "2024",
        "posterUrl": "https://image.tmdb.org/t/p/w500/wTnV3PCVW5O92JMrFvvrRcV39RU.jpg",
        "rating": "8.3",
        "type": "movie",
    },
    {
        "id": "tt6751668",
        "title": "Parasite",
        "year": "2019",
        "posterUrl": "https://image.tmdb.org/t/p/w500/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg",
        "rating": "8.5",
        "type": "movie",
    },
    {
        "id": "tt2380307",
        "title": "Coco",
        "year": "2017",
        "posterUrl": "https://image.tmdb.org/t/p/w500/gGEsBPAijhVUFoiNpgZXqRVWJt2.jpg",
        "rating": "8.4",
        "type": "movie",
    },
    {
        "id": "tt1856101",
        "title": "Blade Runner 2049",
        "year": "2017",
        "posterUrl": "https://image.tmdb.org/t/p/w500/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg",
        "rating": "8.0",
        "type": "movie",
    },
)

_SAMPLE_SERIES_CARDS: tuple[JsonDict, ...] = (
    {
        "id": "tt11280740",
        "title": "Severance",
        "year": "2022",
        "posterUrl": "https://image.tmdb.org/t/p/w500/lFf6LLrQjYldcZItzOkGmMMigP7.jpg",
        "rating": "8.7",
        "type": "series",
    },
    {
        "id": "tt2788316",
        "title": "Shogun",
        "year": "2024",
        "posterUrl": "https://image.tmdb.org/t/p/w500/7O4iVfOMQmdCSxhOg1WnzG1AgYT.jpg",
        "rating": "8.6",
        "type": "series",
    },
    {
        "id": "tt11126994",
        "title": "Arcane",
        "year": "2021",
        "posterUrl": "https://image.tmdb.org/t/p/w500/fqldf2t8ztc9aiwn3k6mlX3tvRT.jpg",
        "rating": "9.0",
        "type": "series",
    },
    {
        "id": "tt2934286",
        "title": "Frieren: Beyond Journey's End",
        "year": "2023",
        "posterUrl": "https://image.tmdb.org/t/p/w500/dqZENchTd7lp5zht7BdlqM7RBhD.jpg",
        "rating": "8.9",
        "type": "series",
    },
    {
        "id": "tt7660850",
        "title": "Succession",
        "year": "2018",
        "posterUrl": "https://image.tmdb.org/t/p/w500/7HW47XbkNQ5fiwQFYGWdw9gs144.jpg",
        "rating": "8.8",
        "type": "series",
    },
    {
        "id": "tt14452776",
        "title": "The Bear",
        "year": "2022",
        "posterUrl": "https://image.tmdb.org/t/p/w500/sHFlbKS3WLqMnp9t2ghADIJFnuQ.jpg",
        "rating": "8.5",
        "type": "series",
    },
    {
        "id": "tt7366338",
        "title": "Chernobyl",
        "year": "2019",
        "posterUrl": "https://image.tmdb.org/t/p/w500/hlLXt2tOPT6RRnjiUmoxyG1LTFi.jpg",
        "rating": "9.3",
        "type": "series",
    },
    {
        "id": "tt9253866",
        "title": "Andor",
        "year": "2022",
        "posterUrl": "https://image.tmdb.org/t/p/w500/59SVNwLfoMnZPPB6ukW6dlPxAdI.jpg",
        "rating": "8.4",
        "type": "series",
    },
    {
        "id": "tt7678620",
        "title": "Bluey",
        "year": "2018",
        "posterUrl": "https://image.tmdb.org/t/p/w500/b9mY0X5T20s0v8K3X9Q0s0V6B0s.jpg",
        "rating": "9.4",
        "type": "series",
    },
    {
        "id": "tt3032476",
        "title": "Better Call Saul",
        "year": "2015",
        "posterUrl": "https://image.tmdb.org/t/p/w500/fC2HDm5t0kHl7mTm7jxMR31b7by.jpg",
        "rating": "9.0",
        "type": "series",
    },
)


@dataclass(frozen=True, slots=True)
class _LoadedWorkspace:
    paths: TenantPaths
    manifest: TenantManifest
    env: dict[str, str]
    bindings: dict[ServiceId, InstanceBinding]
    profiles: list[ResolvedProfile]
    library: Library
    compiled: list[CompiledProfile]


@dataclass(frozen=True, slots=True)
class PushReport:
    steps: tuple[str, ...]
    snapshot: JsonDict


@dataclass(frozen=True, slots=True)
class DiffReport:
    outcomes: tuple[ConfigDiffOutcome, ...]
    skipped: tuple[str, ...] = ()


def _load_optional_json(path: Path) -> JsonDict:
    if not path.is_file():
        return {}
    match read_json_file(path):
        case Ok(doc):
            return doc
        case Err():
            return {}


def _service_doc_path(docs_dir: Path, service: ServiceId, instance: str = DEFAULT_INSTANCE) -> Path:
    return docs_dir / (f"{service}.json" if instance == DEFAULT_INSTANCE else f"{service}.{instance}.json")


def _parse_targets(
    target: str, *, implicit_all_nuvio: bool
) -> Result[tuple[tuple[ServiceId, ...], tuple[NuvioDoc, ...]], StreamWeaverError]:
    cleaned = target.strip().lower()
    if cleaned == "all":
        return Ok((ALL_SERVICES, ALL_NUVIO_DOCS if implicit_all_nuvio else ()))
    if cleaned in NUVIO_TARGET_ALIASES:
        return Ok(((), NUVIO_TARGET_ALIASES[cleaned]))
    if cleaned in ("aiostreams", "aiometadata", "aiomanager"):
        svc_tuple: tuple[ServiceId, ...] = (cleaned,)  # type: ignore[assignment]
        return Ok((svc_tuple, ()))
    return Err(
        StreamWeaverError(
            f"Unknown service target '{target}'. Choose from: all, aiostreams, aiometadata, aiomanager, "
            + ", ".join(NUVIO_TARGET_ALIASES)
        )
    )


def _load_workspace(
    paths: TenantPaths,
    *,
    instance: str = DEFAULT_INSTANCE,
    env_override: Mapping[str, str] | None = None,
    extra_library_roots: Sequence[Path] = (),
) -> Result[_LoadedWorkspace, StreamWeaverError]:
    match read_json_file(paths.manifest_file):
        case Err(err):
            return Err(StreamWeaverError(f"tenant.json: {err}"))
        case Ok(raw_manifest):
            pass
    match parse_tenant_manifest(raw_manifest):
        case Err(m_err):
            return Err(StreamWeaverError(str(m_err)))
        case Ok(manifest):
            pass

    env = dict(env_override) if env_override is not None else load_env_file(paths.env_file)
    bindings: dict[ServiceId, InstanceBinding] = {}
    for svc in ALL_SERVICES:
        match resolve_instance_binding(manifest, env, svc, instance):
            case Ok(b):
                bindings[svc] = b
            case Err() if instance != DEFAULT_INSTANCE:
                match resolve_instance_binding(manifest, env, svc, DEFAULT_INSTANCE):
                    case Ok(fb_b):
                        bindings[svc] = fb_b
                    case Err():
                        continue
            case Err():
                continue

    roots = list(manifest.library_roots(paths.root))
    for extra in extra_library_roots:
        if extra.is_dir() and extra not in roots:
            roots.append(extra)
    library = Library.load(roots)
    if library.errors:
        return Err(StreamWeaverError("library: " + "; ".join(str(e) for e in library.errors)))

    resolved_profiles: list[ResolvedProfile] = []
    for p_name in manifest.profiles:
        pf = profile_file(paths, p_name)
        match read_json_file(pf):
            case Err(p_err):
                return Err(StreamWeaverError(f"profile `{p_name}`: {p_err}"))
            case Ok(p_doc):
                pass
        match parse_profile(p_doc):
            case Err(val_err):
                return Err(StreamWeaverError(f"profile `{p_name}`: {val_err}"))
            case Ok(prof):
                pass
        if prof.name != p_name:
            return Err(
                StreamWeaverError(
                    f"profile directory `{p_name}` holds a profile named `{prof.name}`; names must match"
                )
            )
        inst_map: dict[ServiceId, InstanceBinding] = {}
        for svc, inst_name in prof.instances.items():
            match resolve_instance_binding(manifest, env, svc, inst_name):
                case Err(ib_err):
                    return Err(StreamWeaverError(f"profile `{p_name}`: {ib_err}"))
                case Ok(ib):
                    inst_map[svc] = ib
        login_map: dict[str, LoginBinding] = {}
        for plat, lname in (
            ("nuvio", prof.nuvio.login if prof.nuvio else None),
            ("stremio", prof.stremio.login if prof.stremio else None),
        ):
            if not lname:
                continue
            match resolve_login_binding(manifest, env, plat, lname):
                case Err(lb_err):
                    return Err(StreamWeaverError(f"profile `{p_name}`: {lb_err}"))
                case Ok(lb):
                    login_map[plat] = lb
        resolved_profiles.append(ResolvedProfile(profile=prof, instances=inst_map, logins=login_map))

    mgr_doc = _load_optional_json(paths.doc("aiomanager"))
    compiled: list[CompiledProfile] = []
    for rp in resolved_profiles:
        match compile_profile(rp, library, mgr_doc):
            case Err(c_err):
                return Err(StreamWeaverError(str(c_err)))
            case Ok(cp):
                compiled.append(cp)

    return Ok(
        _LoadedWorkspace(
            paths=paths,
            manifest=manifest,
            env=env,
            bindings=bindings,
            profiles=resolved_profiles,
            library=library,
            compiled=compiled,
        )
    )


def _scope_bindings_for_profile(
    ws: _LoadedWorkspace, profile: str | None
) -> Result[tuple[dict[ServiceId, InstanceBinding], list[CompiledProfile]], StreamWeaverError]:
    if profile is None:
        return Ok((dict(ws.bindings), list(ws.compiled)))
    chosen = [cp for cp in ws.compiled if cp.name == profile]
    if not chosen:
        known = ", ".join(cp.name for cp in ws.compiled) or "none"
        return Err(StreamWeaverError(f"No profile named `{profile}` (known: {known})"))
    scoped_bindings = dict(ws.bindings)
    for svc, binding in chosen[0].resolved.instances.items():
        scoped_bindings[svc] = binding
    return Ok((scoped_bindings, chosen))


def _serialize_param_schema(schema: ParamSchema) -> list[JsonDict]:
    out: list[JsonDict] = []
    for p in schema.params:
        out.append(
            {
                "name": p.name,
                "label": p.label or p.name,
                "description": p.description,
                "type": p.type,
                "items": p.items,
                "nullable": p.nullable,
                "hasDefault": p.has_default,
                "default": p.default,
                "options": [{"label": o.label, "value": o.value} for o in p.options],
            }
        )
    return out


def _serialize_recipe_summary(summary: RecipeUiSummary) -> JsonDict:
    return {
        "kind": summary.kind,
        "id": summary.id,
        "title": summary.title,
        "description": summary.description,
        "category": summary.category,
        "backdropUrl": summary.backdrop_url,
        "folders": [
            {
                "id": f.id,
                "title": f.title,
                "coverUrl": f.cover_url,
                "rows": list(f.row_titles),
            }
            for f in summary.folders
        ],
        "requiredSecrets": list(summary.required_secrets),
        "params": _serialize_param_schema(summary.params),
        "presets": [{"label": pr.label, "with": dict(pr.params)} for pr in summary.presets],
        "isLocal": summary.is_local,
    }


def _mask_secret(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 6:
        return "••••••"
    return f"••••••{val[-4:]}"


def _sync_derived_files(ws: _LoadedWorkspace) -> Result[None, StreamWeaverError]:
    """Re-derives AIOMetadata catalogs, canonicalises AIOManager accounts, and regenerates diagrams."""
    for inst_name in ws.manifest.instances.get("aiometadata", {}):
        meta_path = _service_doc_path(ws.paths.docs_dir, "aiometadata", inst_name)
        if not meta_path.is_file():
            continue
        match read_json_file(meta_path):
            case Err(err):
                return Err(err)
            case Ok(meta_doc):
                pass
        match derive_instance_catalogs(inst_name, meta_doc, ws.compiled):
            case Err(d_err):
                return Err(StreamWeaverError(str(d_err)))
            case Ok(plan):
                if plan.changed:
                    match write_json_file(meta_path, {**meta_doc, "catalogs": plan.catalogs}):
                        case Err(w_err):
                            return Err(w_err)
                        case Ok():
                            pass

    mgr_path = ws.paths.doc("aiomanager")
    if mgr_path.is_file():
        match read_json_file(mgr_path):
            case Ok(mgr_doc):
                outcome = apply_managed_accounts(mgr_doc, ws.manifest.name, ws.profiles)
                if outcome.changed:
                    match write_json_file(mgr_path, outcome.state):
                        case Err(w_err):
                            return Err(w_err)
                        case Ok():
                            pass
            case Err():
                pass

    merged_cols: list[JsonDict] = []
    seen_cols: set[str] = set()
    badges_doc: JsonDict = {}
    meta_doc_first: JsonDict = {}
    settings_doc_first: JsonDict = {}
    for cp in ws.compiled:
        for col in cp.collections_doc:
            if str(col.get("id")) not in seen_cols:
                seen_cols.add(str(col.get("id")))
                merged_cols.append(col)
        badges_doc = badges_doc or (cp.badges or {})
        meta_doc_first = meta_doc_first or (cp.meta or {})
        settings_doc_first = settings_doc_first or (cp.settings or {})

    profiles_summary: list[JsonDict] = []
    for rp in ws.profiles:
        pr = rp.profile
        nuvio_str = None
        if pr.nuvio:
            target = (
                pr.nuvio.profile_id[:8] + "…"
                if pr.nuvio.profile_id
                else (f"name={pr.nuvio.profile_name}" if pr.nuvio.profile_name else "index 1")
            )
            nuvio_str = f"`{pr.nuvio.login}` → {target}"
        profiles_summary.append(
            {
                "name": pr.name,
                "displayName": pr.display_name,
                "aiostreams": pr.instances.get("aiostreams", "default"),
                "aiometadata": pr.instances.get("aiometadata", "default"),
                "nuvio": nuvio_str,
                "stremio": f"`{pr.stremio.login}`" if pr.stremio else None,
                "collections": [
                    c.get("ref") if isinstance(c, dict) and "ref" in c else (c.get("id") if isinstance(c, dict) else "?")
                    for c in pr.collections
                ],
                "extras": [a.name for a in pr.extra_addons],
            }
        )

    bundle = compile_architecture_bundle(
        aiomanager=_load_optional_json(mgr_path),
        aiostreams=_load_optional_json(ws.paths.doc("aiostreams")),
        aiometadata=_load_optional_json(ws.paths.doc("aiometadata")),
        nuvio_collections={"version": "1.0.0", "collections": merged_cols} if merged_cols else {},
        nuvio_badges=badges_doc,
        nuvio_meta=meta_doc_first,
        nuvio_settings=settings_doc_first,
        profiles=profiles_summary,
    )
    ws.paths.architecture_dir.mkdir(parents=True, exist_ok=True)
    for dest, text in (
        (ws.paths.architecture_dir / "topology.c4", bundle.likec4_dsl),
        (ws.paths.architecture_dir / "likec4.config.json", bundle.likec4_config_json),
        (ws.paths.architecture_md, bundle.architecture_markdown),
    ):
        match write_text_file(dest, text):
            case Err(w_err):
                return Err(w_err)
            case Ok():
                pass
    return Ok(None)


def _discover_available_catalogs(
    meta_service_doc: Mapping[str, Any],
    compiled_profiles: Sequence[CompiledProfile],
) -> list[JsonDict]:
    """
    Enumerates every catalog available in `aiometadata.json` plus any cached addon manifests
    (BingeCat, AIOStreams, custom Stremio addons) so Studio's DIY builder can wire any catalog.
    """
    out: list[JsonDict] = []
    seen: set[tuple[str, str, str]] = set()

    raw_cats = meta_service_doc.get("catalogs")
    if isinstance(raw_cats, list):
        for entry in raw_cats:
            if not isinstance(entry, Mapping) or not entry.get("id") or not entry.get("type"):
                continue
            cid = str(entry["id"])
            ctype = str(entry["type"])
            name = str(entry.get("name") or cid)
            src_provider = str(entry.get("source") or "aiometadata")
            if src_provider == "streaming":
                manifest_cid = f"{cid}_{ctype}"
                manifest_type = str(entry.get("displayType") or ctype)
                src_spec: JsonDict = {
                    "addon": "aiometadata",
                    "catalogId": manifest_cid,
                    "type": manifest_type,
                    "title": name,
                }
            elif ctype == "anime":
                manifest_cid = f"{cid}_anime"
                manifest_type = "series"
                cat_overlay = {
                    k: v for k, v in entry.items() if k not in ("enabled", "showInHome")
                }
                cat_overlay["displayType"] = "series"
                src_spec = {
                    "addon": "aiometadata",
                    "catalogId": manifest_cid,
                    "type": "series",
                    "title": name,
                    "catalog": cat_overlay,
                }
            else:
                manifest_cid = cid
                manifest_type = ctype
                src_spec = {
                    "addon": "aiometadata",
                    "catalogId": manifest_cid,
                    "type": manifest_type,
                    "title": name,
                }
            key = ("aiometadata", manifest_cid, manifest_type)
            if key not in seen:
                seen.add(key)
                out.append(
                    {
                        "key": f"aiometadata::{manifest_cid}::{manifest_type}",
                        "label": f"[{src_provider.upper()}] {name} ({manifest_type})",
                        "name": name,
                        "addon": "aiometadata",
                        "type": manifest_type,
                        "sourceSpec": src_spec,
                    }
                )

    for cp in compiled_profiles:
        for addon_name, info in cp.resolver.addons.items():
            if addon_name == "aiometadata" or not info.catalogs:
                continue
            for cat in info.catalogs:
                if not isinstance(cat, Mapping) or not cat.get("id") or not cat.get("type"):
                    continue
                raw_cid = str(cat["id"])
                ctype = str(cat["type"])
                cname = str(cat.get("name") or raw_cid)
                sym = symbolic_source(
                    {"addonId": info.addon_id, "catalogId": raw_cid, "type": ctype, "title": cname},
                    cp.resolver,
                )
                key = (addon_name, raw_cid, ctype)
                if key not in seen:
                    seen.add(key)
                    out.append(
                        {
                            "key": f"{addon_name}::{raw_cid}::{ctype}",
                            "label": f"[{addon_name}] {cname} ({ctype})",
                            "name": cname,
                            "addon": addon_name,
                            "type": ctype,
                            "sourceSpec": sym,
                        }
                    )
    return out


def _inline_template_from_compiled(comp_col: Mapping[str, Any], cp: CompiledProfile) -> JsonDict:
    """Converts a compiled Nuvio collection into an editable symbolic inline collection object."""
    folders_out: list[JsonDict] = []
    for f in comp_col.get("folders") or []:
        if not isinstance(f, Mapping):
            continue
        f_doc: JsonDict = {
            "id": str(f.get("id") or ""),
            "title": str(f.get("title") or ""),
            "sources": [
                symbolic_source(s, cp.resolver)
                for s in (f.get("sources") or [])
                if isinstance(s, Mapping)
            ],
        }
        if f.get("coverImageUrl"):
            f_doc["coverImageUrl"] = str(f["coverImageUrl"])
        folders_out.append(f_doc)
    out: JsonDict = {
        "id": str(comp_col.get("id") or "col-custom"),
        "title": str(comp_col.get("title") or "Custom Collection"),
        "folders": folders_out,
    }
    if comp_col.get("backdropImageUrl"):
        out["backdropImageUrl"] = str(comp_col["backdropImageUrl"])
    return out


def apply_debrid_and_packs(
    paths: TenantPaths,
    *,
    debrid: str | None = None,
    enabled_packs: Sequence[str] | None = None,
    max_resolution: str | None = None,
    cached_only: bool | None = None,
    audio_languages: Sequence[str] | None = None,
    dual_subtitle_pairs: Sequence[Sequence[str]] | None = None,
) -> Result[None, StreamWeaverError]:
    """
    Applies Debrid provider, pack toggles, stream preferences, and Strelingo dual-sub pairs to a
    tenant's documents, keeping `aiomanager.json` and `fld-watchlist-library.json` in sync.
    """
    streams_path = paths.doc("aiostreams")
    if not streams_path.is_file():
        return Err(StreamWeaverError("aiostreams.json not found in tenant directory"))
    match read_json_file(streams_path):
        case Err(r_err):
            return Err(r_err)
        case Ok(streams_doc):
            pass

    if debrid is not None:
        match apply_debrid_provider(streams_doc, debrid):
            case Err(d_err):
                return Err(StreamWeaverError(str(d_err)))
            case Ok(streams_doc):
                pass
    if dual_subtitle_pairs is not None:
        match apply_dual_subtitle_pairs(streams_doc, dual_subtitle_pairs):
            case Err(ds_err):
                return Err(StreamWeaverError(str(ds_err)))
            case Ok(streams_doc):
                pass
    if enabled_packs is not None:
        match apply_aiostreams_packs(streams_doc, enabled_packs):
            case Err(p_err):
                return Err(StreamWeaverError(str(p_err)))
            case Ok(streams_doc):
                pass
    if max_resolution is not None or cached_only is not None or audio_languages is not None:
        match apply_stream_preferences(
            streams_doc,
            max_resolution=max_resolution,
            cached_only=cached_only,
            audio_languages=audio_languages,
        ):
            case Err(sp_err):
                return Err(StreamWeaverError(str(sp_err)))
            case Ok(streams_doc):
                pass

    match write_json_file(streams_path, streams_doc):
        case Err(w_err):
            return Err(w_err)
        case Ok():
            pass

    cur_debrid = active_debrid_provider(streams_doc)
    pack_states = {st.spec.id: st.enabled for st in inspect_aiostreams_packs(streams_doc)}

    mgr_path = paths.doc("aiomanager")
    if debrid is not None and mgr_path.is_file():
        match read_json_file(mgr_path):
            case Ok(mgr_doc):
                updated_mgr = sync_aiomanager_cached_debrid_catalog(mgr_doc, cur_debrid)
                match write_json_file(mgr_path, updated_mgr):
                    case Err(m_err):
                        return Err(m_err)
                    case Ok():
                        pass
            case Err():
                pass

    wl_path = paths.root / "library" / "folders" / "fld-watchlist-library.json"
    if wl_path.is_file():
        match read_json_file(wl_path):
            case Ok(wl_doc):
                synced_wl = sync_watchlist_folder_recipe(
                    wl_doc,
                    debrid_provider=cur_debrid,
                    release_alerts_enabled=pack_states.get("release_alerts", True),
                    core_debrid_enabled=pack_states.get("core_debrid", True),
                )
                match write_json_file(wl_path, synced_wl):
                    case Err(wl_err):
                        return Err(wl_err)
                    case Ok():
                        pass
            case Err():
                pass

    return Ok(None)


def inspect_tenant_state(
    paths: TenantPaths,
    *,
    env_override: Mapping[str, str] | None = None,
    extra_library_roots: Sequence[Path] = (),
) -> Result[JsonDict, StreamWeaverError]:
    """Builds a complete JSON-serializable snapshot of a tenant for the Studio UI."""
    match _load_workspace(paths, env_override=env_override, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    streams_doc = _load_optional_json(paths.doc("aiostreams"))
    meta_service_doc = _load_optional_json(paths.doc("aiometadata"))
    mgr_raw_doc = _load_optional_json(paths.doc("aiomanager"))
    effective_mgr_doc = (
        apply_managed_accounts(mgr_raw_doc, ws.manifest.name, ws.profiles).state
        if mgr_raw_doc
        else {}
    )

    debrid_id = active_debrid_provider(streams_doc)
    debrid_services_view = inspect_debrid_services(streams_doc, ws.env)
    custom_stream_addons_view = inspect_custom_stream_addons(streams_doc)
    stream_formatter_view = inspect_stream_formatter(streams_doc)
    advanced_filters_view = inspect_advanced_stream_filters(streams_doc)
    pack_statuses = inspect_aiostreams_packs(streams_doc)
    stream_prefs = inspect_stream_preferences(streams_doc)
    dual_pairs = inspect_dual_subtitle_pairs(streams_doc)

    local_lib_root = (paths.root / "library").resolve()
    lib_collections: list[JsonDict] = []
    for (kind, _rid), recipe in sorted(ws.library.recipes.items()):
        if kind != "collection":
            continue
        r_root = ws.library.root_of(kind, recipe.id)
        is_local = r_root is not None and r_root.resolve() == local_lib_root
        lib_collections.append(_serialize_recipe_summary(describe_recipe(recipe, ws.library, is_local=is_local)))

    meta_recipe = ws.library.recipes.get(("meta", "default"))
    settings_recipe = ws.library.recipes.get(("settings", "default"))

    # Compute exact required secrets across enabled Debrid/Usenet, packs, custom addons, metadata power, and profiles
    required_keys: set[str] = {
        "AIOSTREAMS_URL",
        "AIOSTREAMS_PASSWORD",
        "AIOMETADATA_URL",
        "AIOMETADATA_PASSWORD",
        "SECRET_TMDB_API_KEY",
        "SECRET_TMDB_ACCESS_TOKEN",
        "SECRET_TVDB_API_KEY",
    }
    required_keys.update(required_multi_debrid_secrets(streams_doc))
    for st in pack_statuses:
        if st.enabled:
            required_keys.update(st.spec.required_secrets)
    for ca in custom_stream_addons_view:
        if ca.get("enabled", True):
            required_keys.update(
                m.group(1) for m in SECRET_PLACEHOLDER_RE.finditer(str(ca.get("manifestUrl") or ""))
            )

    effective_meta_doc: Mapping[str, Any] = meta_service_doc
    if ws.compiled and meta_service_doc:
        match derive_instance_catalogs(DEFAULT_INSTANCE, meta_service_doc, ws.compiled):
            case Ok(plan):
                effective_meta_doc = {**meta_service_doc, "catalogs": plan.catalogs}
            case Err():
                pass
    required_keys.update(required_aiometadata_power_secrets(effective_meta_doc))

    for inst_name, inst_prefix in ws.manifest.instances.get("aiometadata", {}).items():
        if inst_name != DEFAULT_INSTANCE:
            inst_doc = _load_optional_json(_service_doc_path(paths.docs_dir, "aiometadata", inst_name))
            if inst_doc:
                required_keys.update(required_aiometadata_power_secrets(inst_doc))
            if any(rp.profile.instances.get("aiometadata") == inst_name for rp in ws.profiles):
                required_keys.add(f"{inst_prefix}_PASSWORD")

    metadata_power_view = inspect_aiometadata_power(effective_meta_doc)
    aiomanager_power_view = inspect_aiomanager_power(effective_mgr_doc, ws.env)

    compiled_by_name = {cp.name: cp for cp in ws.compiled}
    profiles_out: list[JsonDict] = []
    for rp in ws.profiles:
        prof = rp.profile
        cp = compiled_by_name[prof.name]
        for extra in prof.extra_addons:
            if extra.enabled:
                required_keys.update(m.group(1) for m in SECRET_PLACEHOLDER_RE.finditer(extra.transport_url))

        active_cols: list[JsonDict] = []
        compiled_cols = cp.collections_doc
        for idx, raw_item in enumerate(prof.collections):
            comp_col = compiled_cols[idx] if idx < len(compiled_cols) else {}
            ref_id = raw_item.get("ref") if isinstance(raw_item, Mapping) else None
            raw_col_with = raw_item.get("with") if isinstance(raw_item, Mapping) else None
            with_params: dict[str, Any] = dict(raw_col_with) if isinstance(raw_col_with, Mapping) else {}
            if isinstance(ref_id, str) and ("collection", ref_id) in ws.library.recipes:
                rec = ws.library.recipes[("collection", ref_id)]
                summary = describe_recipe(rec, ws.library, with_params=with_params)
                required_keys.update(summary.required_secrets)
                folders_view = [
                    {
                        "id": f.id,
                        "title": f.title,
                        "coverUrl": f.cover_url,
                        "rows": list(f.row_titles),
                    }
                    for f in summary.folders
                ]
                col_title = str(comp_col.get("title") or summary.title)
                backdrop = str(comp_col.get("backdropImageUrl") or summary.backdrop_url)
            else:
                col_title = str(comp_col.get("title") or f"Collection {idx + 1}")
                backdrop = str(comp_col.get("backdropImageUrl") or "")
                folders_view = [
                    {
                        "id": str(f.get("id") or ""),
                        "title": str(f.get("title") or ""),
                        "coverUrl": str(f.get("coverImageUrl") or ""),
                        "rows": [
                            str(s.get("title") or s.get("catalogId") or "")
                            for s in (f.get("sources") or [])
                            if isinstance(s, Mapping)
                        ],
                    }
                    for f in (comp_col.get("folders") or [])
                    if isinstance(f, Mapping)
                ]
            for f_comp in comp_col.get("folders") or []:
                if not isinstance(f_comp, Mapping):
                    continue
                for s_comp in f_comp.get("sources") or []:
                    if not isinstance(s_comp, Mapping):
                        continue
                    s_cid = str(s_comp.get("catalogId") or "")
                    if s_cid.startswith("mdblist."):
                        required_keys.add("SECRET_MDBLIST_API_KEY")
                    elif s_cid.startswith("trakt."):
                        required_keys.add("SECRET_TRAKT_TOKEN_ID")
            inline_tpl = (
                copy.deepcopy(dict(raw_item))
                if (ref_id is None and isinstance(raw_item, Mapping))
                else _inline_template_from_compiled(comp_col, cp)
            )
            active_cols.append(
                {
                    "index": idx,
                    "ref": ref_id,
                    "with": dict(with_params),
                    "raw": copy.deepcopy(dict(raw_item)) if (ref_id is None and isinstance(raw_item, Mapping)) else None,
                    "inlineTemplate": inline_tpl,
                    "id": str(comp_col.get("id") or ref_id or idx),
                    "title": col_title,
                    "backdropUrl": backdrop,
                    "folders": folders_view,
                }
            )

        if prof.meta is not None:
            required_keys.add("SECRET_MDBLIST_API_KEY")
            required_keys.add("SECRET_TMDB_API_KEY")

        meta_with = prof.meta.get("with") if isinstance(prof.meta, Mapping) else None
        settings_with = prof.settings.get("with") if isinstance(prof.settings, Mapping) else None
        nuvio_login = rp.logins.get("nuvio")
        profiles_out.append(
            {
                "name": prof.name,
                "displayName": prof.display_name,
                "emoji": prof.appearance.emoji or "🎬",
                "accentColor": prof.appearance.accent_color or "#3b82f6",
                "instances": dict(prof.instances),
                "aiometadataInstance": prof.instances.get("aiometadata", DEFAULT_INSTANCE),
                "extraAddons": inspect_extra_addons(prof.extra_addons),
                "nuvio": (
                    {
                        "login": prof.nuvio.login,
                        "loginReady": bool(nuvio_login and nuvio_login.configured()),
                        "email": nuvio_login.credentials.get("email", "") if nuvio_login else "",
                        "profileId": prof.nuvio.profile_id or "",
                        "profileName": prof.nuvio.profile_name or "",
                        "platforms": list(prof.nuvio.platforms),
                    }
                    if prof.nuvio
                    else None
                ),
                "stremioEnabled": prof.stremio is not None,
                "bingecatEnabled": any(a.name == "bingecat" and a.enabled for a in prof.extra_addons),
                "collections": active_cols,
                "badgesEnabled": prof.badges is not None,
                "meta": {
                    "enabled": prof.meta is not None,
                    "ref": prof.meta.get("ref") if isinstance(prof.meta, Mapping) else None,
                    "with": dict(meta_with) if isinstance(meta_with, Mapping) else {},
                },
                "settings": {
                    "enabled": prof.settings is not None,
                    "ref": prof.settings.get("ref") if isinstance(prof.settings, Mapping) else None,
                    "with": dict(settings_with) if isinstance(settings_with, Mapping) else {},
                },
            }
        )

    dynamic_specs: list[EnvKeySpec] = list(KNOWN_ENV_KEYS)
    known_key_set = set(_ENV_SPECS_BY_KEY)
    for inst_name, inst_prefix in sorted(ws.manifest.instances.get("aiometadata", {}).items()):
        if inst_name == DEFAULT_INSTANCE:
            continue
        uuid_k = f"{inst_prefix}_UUID"
        pw_k = f"{inst_prefix}_PASSWORD"
        if uuid_k not in known_key_set:
            dynamic_specs.append(
                EnvKeySpec(
                    uuid_k,
                    f"AIOMetadata ({inst_name}) UUID",
                    "instances",
                    is_secret=False,
                    hint="Auto-filled by Provision",
                )
            )
            known_key_set.add(uuid_k)
        if pw_k not in known_key_set:
            dynamic_specs.append(
                EnvKeySpec(
                    pw_k,
                    f"AIOMetadata ({inst_name}) Password",
                    "instances",
                    hint="Choose a strong password",
                )
            )
            known_key_set.add(pw_k)

    env_items: list[JsonDict] = []
    for spec in dynamic_specs:
        raw_val = ws.env.get(spec.key, "")
        env_items.append(
            {
                "key": spec.key,
                "label": spec.label,
                "group": spec.group,
                "isSecret": spec.is_secret,
                "required": spec.key in required_keys,
                "isSet": bool(raw_val),
                "value": "" if spec.is_secret else raw_val,
                "masked": _mask_secret(raw_val) if spec.is_secret else raw_val,
                "signupUrl": spec.signup_url,
                "hint": spec.hint,
            }
        )

    streams_base = (ws.env.get("AIOSTREAMS_URL") or "https://aiostreams.elfhosted.com").rstrip("/")
    streams_uuid = ws.env.get("AIOSTREAMS_UUID", "").strip()
    meta_base = (ws.env.get("AIOMETADATA_URL") or "https://aiometadata.elfhosted.com").rstrip("/")
    meta_uuid = ws.env.get("AIOMETADATA_UUID", "").strip()

    missing_required = [item["key"] for item in env_items if item["required"] and not item["isSet"]]
    any_debrid_ready = any(bool(s.get("configured")) for s in debrid_services_view.get("active", []))
    tmdb_ready = bool(ws.env.get("SECRET_TMDB_API_KEY", "").strip())
    instances_provisioned = bool(streams_uuid and meta_uuid)
    nuvio_ready = any(bool(p.get("nuvio") and p["nuvio"].get("loginReady")) for p in profiles_out)
    hosting_ready = bool(
        ws.env.get("AIOSTREAMS_URL", "").strip() and ws.env.get("AIOMETADATA_URL", "").strip()
    )

    onboarding: JsonDict = {
        "needsSetup": not instances_provisioned or bool(missing_required),
        "readyToProvision": bool(
            hosting_ready
            and ws.env.get("AIOSTREAMS_PASSWORD", "").strip()
            and ws.env.get("AIOMETADATA_PASSWORD", "").strip()
            and any_debrid_ready
            and tmdb_ready
        ),
        "instancesProvisioned": instances_provisioned,
        "hostingConfigured": hosting_ready,
        "debridConfigured": any_debrid_ready,
        "metadataConfigured": tmdb_ready,
        "nuvioConnected": nuvio_ready,
        "missingRequiredKeys": missing_required,
        "completedSteps": sum([hosting_ready, bool(any_debrid_ready and tmdb_ready), nuvio_ready, instances_provisioned]),
        "totalSteps": 4,
    }

    return Ok(
        {
            "tenant": {
                "name": ws.manifest.name,
                "root": str(paths.root),
                "aiostreamsProvisioned": bool(streams_uuid),
                "aiometadataProvisioned": bool(meta_uuid),
                "aiostreamsUuid": streams_uuid,
                "aiometadataUuid": meta_uuid,
                "aiostreamsConfigureUrl": f"{streams_base}/stremio/configure",
                "aiometadataConfigureUrl": (
                    f"{meta_base}/stremio/{meta_uuid}/configure" if meta_uuid else f"{meta_base}/configure"
                ),
                "aiometadataInstances": sorted(
                    ws.manifest.instances.get("aiometadata", {DEFAULT_INSTANCE: "AIOMETADATA"})
                ),
            },
            "onboarding": onboarding,
            "debrid": {
                "active": debrid_id,
                "options": [
                    {
                        "id": d.id,
                        "label": d.label,
                        "shortName": d.short_name,
                        "secretKey": d.secret_key,
                        "signupUrl": d.signup_url,
                    }
                    for d in DEBRID_PROVIDERS.values()
                ],
            },
            "debridServices": debrid_services_view,
            "packs": [
                {
                    "id": st.spec.id,
                    "title": st.spec.title,
                    "description": st.spec.description,
                    "enabled": st.enabled,
                    "presets": list(st.present_presets),
                    "requiredSecrets": list(st.spec.required_secrets),
                }
                for st in pack_statuses
            ],
            "customStreamAddons": custom_stream_addons_view,
            "streamFormatter": stream_formatter_view,
            "streamPreferences": {
                "maxResolution": stream_prefs.max_resolution,
                "cachedOnly": stream_prefs.cached_only,
                "audioLanguages": list(stream_prefs.audio_languages),
                "resolutionOptions": list(RESOLUTION_CAPS),
                "audioLanguageOptions": list(STREAM_AUDIO_LANGUAGES),
            },
            "advancedStreamFilters": advanced_filters_view,
            "dualSubtitles": {
                "pairs": [[m, t] for m, t in dual_pairs],
                "languages": [
                    {"code": s.code3, "short": s.code2, "label": s.label}
                    for s in STRELINGO_LANGUAGES
                ],
            },
            "metadataPower": metadata_power_view,
            "aiomanagerPower": aiomanager_power_view,
            "customBuilderMeta": custom_builder_metadata(),
            "availableCatalogs": _discover_available_catalogs(meta_service_doc, ws.compiled),
            "libraryCollections": lib_collections,
            "metaParams": _serialize_param_schema(meta_recipe.params) if meta_recipe else [],
            "settingsParams": _serialize_param_schema(settings_recipe.params) if settings_recipe else [],
            "profiles": profiles_out,
            "env": env_items,
        }
    )


def _ensure_aiometadata_instance_registered(
    paths: TenantPaths, inst_name: str
) -> Result[None, StreamWeaverError]:
    if inst_name == DEFAULT_INSTANCE:
        return Ok(None)
    if not _STRICT_NAME_RE.match(inst_name):
        return Err(
            StreamWeaverError(
                f"AIOMetadata instance name `{inst_name}` must match `{_STRICT_NAME_RE.pattern}`"
            )
        )
    match read_json_file(paths.manifest_file):
        case Err(r_err):
            return Err(r_err)
        case Ok(manifest_doc):
            pass
    instances = dict(manifest_doc.get("instances") or {})
    meta_reg = dict(instances.get("aiometadata") or {})
    env_prefix = meta_reg.get(inst_name) or f"AIOMETADATA_{inst_name.upper().replace('-', '_')}"
    if inst_name not in meta_reg:
        meta_reg[inst_name] = env_prefix
        instances["aiometadata"] = meta_reg
        updated_manifest = {**manifest_doc, "instances": instances}
        match parse_tenant_manifest(updated_manifest):
            case Err(v_err):
                return Err(StreamWeaverError(str(v_err)))
            case Ok():
                pass
        match write_json_file(paths.manifest_file, updated_manifest):
            case Err(w_err):
                return Err(w_err)
            case Ok():
                pass

    dest_doc = _service_doc_path(paths.docs_dir, "aiometadata", inst_name)
    if not dest_doc.is_file():
        src_doc = _load_optional_json(paths.doc("aiometadata"))
        match write_json_file(dest_doc, src_doc or {"catalogs": []}):
            case Err(w_err):
                return Err(w_err)
            case Ok():
                pass

    keys = instance_env_keys("aiometadata", env_prefix)
    scaffold = [k for k in keys if k != f"{env_prefix}_URL"]
    _ = scaffold_env_keys(
        paths.env_example_file, scaffold, header=f"aiometadata/{inst_name} instance"
    )
    if paths.env_file.is_file():
        _ = scaffold_env_keys(
            paths.env_file, scaffold, header=f"aiometadata/{inst_name} instance"
        )
    return Ok(None)


def _sync_catalog_overrides_to_profile_extras(
    ws: _LoadedWorkspace,
    meta_inst: str,
    meta_doc: Mapping[str, Any],
    overrides: Any,
) -> Result[bool, StreamWeaverError]:
    """
    Keeps `Profile.extra_catalogs` aligned with explicit `enabled` / `showInHome` toggles from
    `metadataPower.catalogOverrides` on non-collection catalogs so `derive_instance_catalogs` and
    `sw validate` preserve user catalog overrides cleanly.
    """
    targeted: set[tuple[str, str | None]] = set()
    if isinstance(overrides, Mapping):
        for raw_k, ov in overrides.items():
            if isinstance(ov, Mapping) and ("enabled" in ov or "showInHome" in ov):
                cid = str(raw_k).strip()
                ctype = str(ov["type"]).strip() if isinstance(ov.get("type"), str) else None
                if "::" in cid and not ctype:
                    cid, ctype = cid.split("::", 1)
                targeted.add((cid, ctype))
    elif isinstance(overrides, Sequence) and not isinstance(overrides, (str, bytes)):
        for ov in overrides:
            if isinstance(ov, Mapping) and ("enabled" in ov or "showInHome" in ov):
                cid = str(ov.get("id") or ov.get("key") or "").strip()
                ctype = str(ov["type"]).strip() if isinstance(ov.get("type"), str) else None
                if "::" in cid and not ctype:
                    cid, ctype = cid.split("::", 1)
                if cid:
                    targeted.add((cid, ctype))
    if not targeted:
        return Ok(False)

    on_inst = [
        cp for cp in ws.compiled if cp.resolved.profile.instances.get("aiometadata") == meta_inst
    ]
    if not on_inst:
        return Ok(False)

    used_keys = {(u.catalog_id, u.type) for cp in on_inst for u in cp.catalog_uses}
    raw_cats = meta_doc.get("catalogs")
    if not isinstance(raw_cats, list):
        return Ok(False)

    desired_by_key: dict[tuple[str, str], tuple[bool, bool]] = {}
    for entry in raw_cats:
        if not isinstance(entry, Mapping):
            continue
        cid = str(entry.get("id") or "")
        ctype = str(entry.get("type") or "")
        if not cid or not ctype:
            continue
        if (cid, ctype) not in targeted and (cid, None) not in targeted:
            continue
        if any(k in used_keys for k in config_catalog_keys(entry)):
            continue
        desired_by_key[(cid, ctype)] = (
            bool(entry.get("enabled", True)),
            bool(entry.get("showInHome", False)),
        )

    if not desired_by_key:
        return Ok(False)

    owner_prof = on_inst[0].resolved.profile
    new_extras: list[ExtraCatalogSpec] = []
    seen_keys: set[tuple[str, str]] = set()
    changed = False
    for ex in owner_prof.extra_catalogs:
        key = (ex.catalog_id, ex.type)
        if ex.recipe is None and key in desired_by_key:
            seen_keys.add(key)
            en, home = desired_by_key[key]
            if not en:
                changed = True
                continue
            updated_ex = ExtraCatalogSpec(
                catalog_id=ex.catalog_id, type=ex.type, enabled=True, show_in_home=home
            )
            if updated_ex != ex:
                changed = True
            new_extras.append(updated_ex)
        else:
            new_extras.append(ex)

    for (cid, ctype), (en, home) in desired_by_key.items():
        if (cid, ctype) not in seen_keys and en:
            new_extras.append(
                ExtraCatalogSpec(catalog_id=cid, type=ctype, enabled=True, show_in_home=home)
            )
            changed = True

    if not changed:
        return Ok(False)

    updated_prof = replace(owner_prof, extra_catalogs=tuple(new_extras))
    match write_json_file(profile_file(ws.paths, updated_prof.name), profile_to_doc(updated_prof)):
        case Err(w_err):
            return Err(w_err)
        case Ok():
            return Ok(True)


def apply_studio_mutation(
    paths: TenantPaths,
    mutation: Mapping[str, Any],
    *,
    extra_library_roots: Sequence[Path] = (),
) -> Result[JsonDict, StreamWeaverError]:
    """
    Applies a patch-safe declarative Studio edit (Debrid/Usenet stack, AIOStreams packs, custom
    addons, stream formatter, advanced stream filters, AIOMetadata power controls, AIOManager power
    controls, profile collections / extraAddons / settings / meta / badges / client toggles, and
    `.env` updates), re-derives AIOMetadata catalogs and AIOManager accounts, updates architecture
    diagrams, and returns the fresh workspace snapshot.
    """
    # 1. Update `.env` keys if provided
    env_updates = mutation.get("envUpdates")
    if isinstance(env_updates, Mapping):
        clean_updates = {str(k): str(v).strip() for k, v in env_updates.items() if str(v).strip()}
        if clean_updates:
            match save_env_updates(paths.env_file, clean_updates):
                case Err(e_err):
                    return Err(e_err)
                case Ok():
                    pass

    # 2. Update AIOStreams controls (patch-safe: only touch sections present in `mutation`)
    debrid_val = mutation.get("debrid") if isinstance(mutation.get("debrid"), str) else None
    debrid_services_mut = (
        mutation.get("debridServices")
        if isinstance(mutation.get("debridServices"), Sequence)
        and not isinstance(mutation.get("debridServices"), (str, bytes))
        else None
    )
    custom_addons_mut = (
        mutation.get("customStreamAddons")
        if isinstance(mutation.get("customStreamAddons"), Sequence)
        and not isinstance(mutation.get("customStreamAddons"), (str, bytes))
        else None
    )
    raw_fmt_mut = mutation.get("streamFormatter")
    formatter_preset: str | None = None
    if isinstance(raw_fmt_mut, str) and raw_fmt_mut.strip():
        formatter_preset = raw_fmt_mut.strip()
    elif isinstance(raw_fmt_mut, Mapping):
        cand_fmt = raw_fmt_mut.get("active") or raw_fmt_mut.get("preset") or raw_fmt_mut.get("id")
        if isinstance(cand_fmt, str) and cand_fmt.strip():
            formatter_preset = cand_fmt.strip()

    adv_filters_mut = (
        mutation.get("advancedStreamFilters")
        if isinstance(mutation.get("advancedStreamFilters"), Mapping)
        else None
    )
    packs_val = mutation.get("enabledPacks") if isinstance(mutation.get("enabledPacks"), list) else None
    stream_prefs_mut = (
        mutation.get("streamPreferences")
        if isinstance(mutation.get("streamPreferences"), Mapping)
        else None
    )
    dual_pairs_mut = (
        mutation.get("dualSubtitlePairs") if isinstance(mutation.get("dualSubtitlePairs"), list) else None
    )

    if any(
        x is not None
        for x in (
            debrid_val,
            debrid_services_mut,
            custom_addons_mut,
            formatter_preset,
            adv_filters_mut,
            packs_val,
            stream_prefs_mut,
            dual_pairs_mut,
        )
    ):
        streams_path = paths.doc("aiostreams")
        if streams_path.is_file():
            match read_json_file(streams_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(streams_doc):
                    pass
            if debrid_val is not None and debrid_services_mut is None:
                match apply_debrid_provider(streams_doc, debrid_val):
                    case Err(d_err):
                        return Err(StreamWeaverError(str(d_err)))
                    case Ok(streams_doc):
                        pass
            if debrid_services_mut is not None:
                match apply_debrid_services(streams_doc, debrid_services_mut):
                    case Err(ds_svc_err):
                        return Err(StreamWeaverError(str(ds_svc_err)))
                    case Ok(streams_doc):
                        pass
            if dual_pairs_mut is not None:
                parsed_pairs: list[list[str]] = [
                    [str(x) for x in row]
                    for row in dual_pairs_mut
                    if isinstance(row, Sequence) and not isinstance(row, (str, bytes))
                ]
                match apply_dual_subtitle_pairs(streams_doc, parsed_pairs):
                    case Err(ds_err):
                        return Err(StreamWeaverError(str(ds_err)))
                    case Ok(streams_doc):
                        pass
            if packs_val is not None:
                match apply_aiostreams_packs(streams_doc, [str(x) for x in packs_val]):
                    case Err(p_err):
                        return Err(StreamWeaverError(str(p_err)))
                    case Ok(streams_doc):
                        pass
            if custom_addons_mut is not None:
                match apply_custom_stream_addons(streams_doc, custom_addons_mut):
                    case Err(ca_err):
                        return Err(StreamWeaverError(str(ca_err)))
                    case Ok(streams_doc):
                        pass
            if formatter_preset is not None:
                match apply_stream_formatter(streams_doc, formatter_preset):
                    case Err(fmt_err):
                        return Err(StreamWeaverError(str(fmt_err)))
                    case Ok(streams_doc):
                        pass
            if stream_prefs_mut is not None:
                max_res = (
                    str(stream_prefs_mut["maxResolution"])
                    if isinstance(stream_prefs_mut.get("maxResolution"), str)
                    else None
                )
                cached_only = (
                    bool(stream_prefs_mut["cachedOnly"])
                    if "cachedOnly" in stream_prefs_mut and stream_prefs_mut["cachedOnly"] is not None
                    else None
                )
                audio_langs = (
                    [str(x) for x in stream_prefs_mut["audioLanguages"] if str(x).strip()]
                    if isinstance(stream_prefs_mut.get("audioLanguages"), list)
                    else None
                )
                match apply_stream_preferences(
                    streams_doc,
                    max_resolution=max_res,
                    cached_only=cached_only,
                    audio_languages=audio_langs,
                ):
                    case Err(sp_err):
                        return Err(StreamWeaverError(str(sp_err)))
                    case Ok(streams_doc):
                        pass
            if adv_filters_mut is not None:
                match apply_advanced_stream_filters(streams_doc, adv_filters_mut):
                    case Err(af_err):
                        return Err(StreamWeaverError(str(af_err)))
                    case Ok(streams_doc):
                        pass
            match write_json_file(streams_path, streams_doc):
                case Err(w_err):
                    return Err(w_err)
                case Ok():
                    pass
            cur_debrid = active_debrid_provider(streams_doc)
            pack_states = {st.spec.id: st.enabled for st in inspect_aiostreams_packs(streams_doc)}
            mgr_path = paths.doc("aiomanager")
            if (debrid_val is not None or debrid_services_mut is not None) and mgr_path.is_file():
                match read_json_file(mgr_path):
                    case Ok(mgr_doc):
                        _ = write_json_file(
                            mgr_path, sync_aiomanager_cached_debrid_catalog(mgr_doc, cur_debrid)
                        )
                    case Err():
                        pass
            wl_path = paths.root / "library" / "folders" / "fld-watchlist-library.json"
            if wl_path.is_file():
                match read_json_file(wl_path):
                    case Ok(wl_doc):
                        synced_wl = sync_watchlist_folder_recipe(
                            wl_doc,
                            debrid_provider=cur_debrid,
                            release_alerts_enabled=pack_states.get("release_alerts", True),
                            core_debrid_enabled=pack_states.get("core_debrid", True),
                        )
                        _ = write_json_file(wl_path, synced_wl)
                    case Err():
                        pass

    # 3. Update AIOMetadata power controls if provided
    meta_power_mut = (
        mutation.get("metadataPower") if isinstance(mutation.get("metadataPower"), Mapping) else None
    )
    pending_cat_overrides: tuple[str, JsonDict, Any] | None = None
    if meta_power_mut is not None:
        meta_inst = str(meta_power_mut.get("instance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
        meta_path = _service_doc_path(paths.docs_dir, "aiometadata", meta_inst)
        if meta_path.is_file():
            match read_json_file(meta_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(meta_doc):
                    pass
            match apply_aiometadata_power(meta_doc, meta_power_mut):
                case Err(mp_err):
                    return Err(StreamWeaverError(str(mp_err)))
                case Ok(updated_meta_doc):
                    pass
            match write_json_file(meta_path, updated_meta_doc):
                case Err(w_err):
                    return Err(w_err)
                case Ok():
                    pass
            if "catalogOverrides" in meta_power_mut:
                pending_cat_overrides = (
                    meta_inst,
                    updated_meta_doc,
                    meta_power_mut.get("catalogOverrides"),
                )

    # 4. Update AIOManager power controls if provided
    mgr_power_mut = (
        mutation.get("aiomanagerPower")
        if isinstance(mutation.get("aiomanagerPower"), Mapping)
        else None
    )
    if mgr_power_mut is not None:
        mgr_path = paths.doc("aiomanager")
        base_mgr = _load_optional_json(mgr_path)
        match apply_aiomanager_power(base_mgr, mgr_power_mut):
            case Err(am_err):
                return Err(StreamWeaverError(str(am_err)))
            case Ok(updated_mgr_doc):
                pass
        if mgr_path.is_file() or updated_mgr_doc:
            match write_json_file(mgr_path, updated_mgr_doc):
                case Err(w_err):
                    return Err(w_err)
                case Ok():
                    pass

    # 5. Update profile settings / collections / extraAddons / instance / client toggles
    prof_mut = mutation.get("profile")
    prof_rollback: tuple[Path, bytes] | None = None
    if isinstance(prof_mut, Mapping) and isinstance(prof_mut.get("name"), str):
        p_name = prof_mut["name"]
        pf = profile_file(paths, p_name)
        match read_json_file(pf):
            case Err(r_err):
                return Err(r_err)
            case Ok(raw_p_doc):
                pass
        prof_rollback = (pf, pf.read_bytes())
        match parse_profile(raw_p_doc):
            case Err(v_err):
                return Err(StreamWeaverError(str(v_err)))
            case Ok(prof):
                pass

        updated_prof: Profile = prof
        if isinstance(prof_mut.get("displayName"), str) and prof_mut["displayName"].strip():
            updated_prof = replace(updated_prof, display_name=prof_mut["displayName"].strip())
        if "emoji" in prof_mut or "accentColor" in prof_mut:
            updated_prof = replace(
                updated_prof,
                appearance=Appearance(
                    emoji=str(prof_mut["emoji"])
                    if isinstance(prof_mut.get("emoji"), str)
                    else updated_prof.appearance.emoji,
                    accent_color=str(prof_mut["accentColor"])
                    if isinstance(prof_mut.get("accentColor"), str)
                    else updated_prof.appearance.accent_color,
                ),
            )
        if isinstance(prof_mut.get("aiometadataInstance"), str) and prof_mut["aiometadataInstance"].strip():
            target_inst = prof_mut["aiometadataInstance"].strip().lower()
            match _ensure_aiometadata_instance_registered(paths, target_inst):
                case Err() as inst_err:
                    return inst_err
                case Ok():
                    pass
            updated_prof = replace(
                updated_prof,
                instances={**updated_prof.instances, "aiometadata": target_inst},
            )

        if "stremioEnabled" in prof_mut:
            if bool(prof_mut["stremioEnabled"]):
                st_login = (
                    updated_prof.stremio.login
                    if updated_prof.stremio
                    else (updated_prof.nuvio.login if updated_prof.nuvio else "main")
                )
                updated_prof = replace(updated_prof, stremio=StremioClientSpec(login=st_login))
            else:
                updated_prof = replace(updated_prof, stremio=None)

        if (
            "extraAddons" in prof_mut
            and isinstance(prof_mut["extraAddons"], Sequence)
            and not isinstance(prof_mut["extraAddons"], (str, bytes))
        ):
            match normalize_extra_addons(updated_prof.extra_addons, prof_mut["extraAddons"]):
                case Err(ea_err):
                    return Err(StreamWeaverError(str(ea_err)))
                case Ok(norm_extras):
                    updated_prof = replace(updated_prof, extra_addons=norm_extras)
        elif "bingecatEnabled" in prof_mut:
            bc_on = bool(prof_mut["bingecatEnabled"])
            new_extras = [
                replace(a, enabled=bc_on) if a.name == "bingecat" else a
                for a in updated_prof.extra_addons
            ]
            updated_prof = replace(updated_prof, extra_addons=tuple(new_extras))

        if isinstance(prof_mut.get("nuvio"), Mapping) and updated_prof.nuvio is not None:
            n_mut = prof_mut["nuvio"]
            pid = (
                str(n_mut.get("profileId", "")).strip() or None
                if "profileId" in n_mut
                else updated_prof.nuvio.profile_id
            )
            pname = (
                (None if pid else (str(n_mut.get("profileName", "")).strip() or None))
                if ("profileName" in n_mut or "profileId" in n_mut)
                else updated_prof.nuvio.profile_name
            )
            plats = (
                tuple(str(x) for x in n_mut["platforms"] if str(x))
                if isinstance(n_mut.get("platforms"), list) and n_mut["platforms"]
                else updated_prof.nuvio.platforms
            )
            updated_prof = replace(
                updated_prof,
                nuvio=NuvioClientSpec(
                    login=updated_prof.nuvio.login,
                    profile_id=pid,
                    profile_name=pname,
                    platforms=plats,
                ),
            )

        if isinstance(prof_mut.get("collections"), list):
            new_cols: list[Any] = []
            for item in prof_mut["collections"]:
                if not isinstance(item, Mapping):
                    continue
                if isinstance(item.get("ref"), str):
                    entry: JsonDict = {"ref": item["ref"]}
                    if isinstance(item.get("with"), Mapping) and item["with"]:
                        entry["with"] = dict(item["with"])
                    new_cols.append(entry)
                elif isinstance(item.get("raw"), Mapping):
                    new_cols.append(dict(item["raw"]))
                elif "id" in item and "folders" in item:
                    new_cols.append(dict(item))
            updated_prof = replace(updated_prof, collections=tuple(new_cols))

        if "badgesEnabled" in prof_mut:
            updated_prof = replace(
                updated_prof,
                badges=({"ref": "default"} if updated_prof.badges is None else updated_prof.badges)
                if bool(prof_mut["badgesEnabled"])
                else None,
            )

        if isinstance(prof_mut.get("meta"), Mapping):
            m_mut = prof_mut["meta"]
            if not bool(m_mut.get("enabled", True)):
                updated_prof = replace(updated_prof, meta=None)
            else:
                m_doc: JsonDict = {"ref": str(m_mut.get("ref") or "default")}
                if isinstance(m_mut.get("with"), Mapping) and m_mut["with"]:
                    m_doc["with"] = dict(m_mut["with"])
                updated_prof = replace(updated_prof, meta=m_doc)

        if isinstance(prof_mut.get("settings"), Mapping):
            s_mut = prof_mut["settings"]
            if not bool(s_mut.get("enabled", True)):
                updated_prof = replace(updated_prof, settings=None)
            else:
                s_doc: JsonDict = {"ref": str(s_mut.get("ref") or "default")}
                if isinstance(s_mut.get("with"), Mapping) and s_mut["with"]:
                    s_doc["with"] = dict(s_mut["with"])
                updated_prof = replace(updated_prof, settings=s_doc)

        candidate_doc = profile_to_doc(updated_prof)
        match parse_profile(candidate_doc):
            case Err(schema_err):
                return Err(StreamWeaverError(str(schema_err)))
            case Ok():
                pass
        match write_json_file(pf, candidate_doc):
            case Err(w_err):
                return Err(w_err)
            case Ok():
                pass

    # 6. Reload, reconcile catalog overrides -> profile extras if needed, sync derived files & diagrams
    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            if prof_rollback is not None:
                prof_rollback[0].write_bytes(prof_rollback[1])
            return err
        case Ok(ws):
            pass

    if pending_cat_overrides is not None:
        ov_inst, ov_doc, ov_spec = pending_cat_overrides
        match _sync_catalog_overrides_to_profile_extras(ws, ov_inst, ov_doc, ov_spec):
            case Err() as ov_err:
                return ov_err
            case Ok(True):
                match _load_workspace(paths, extra_library_roots=extra_library_roots):
                    case Err() as err:
                        return err
                    case Ok(ws):
                        pass
            case Ok(False):
                pass

    match _sync_derived_files(ws):
        case Err() as err:
            return err
        case Ok():
            pass
    return inspect_tenant_state(paths, extra_library_roots=extra_library_roots)


def _preset_collections_for_profile(
    preset: str, library: Library
) -> Result[tuple[Any, ...], StreamWeaverError]:
    has_for_you = ("collection", "col-for-you-discovery") in library.recipes
    has_courses = ("collection", "col-courses-docs") in library.recipes

    if preset == "standard":
        cols: list[JsonDict] = [
            {"ref": "col-for-you-discovery" if has_for_you else "col-discover"},
            {"ref": "col-streaming-services"},
            {"ref": "col-indian-cinema"},
            {"ref": "col-anime-japanese"},
            {"ref": "col-courses-docs" if has_courses else "col-studios-auteurs"},
            {"ref": "col-genres-moods"},
            {"ref": "col-franchises"},
        ]
        return Ok(tuple(cols))

    if preset == "minimal":
        disc_ref = "col-discover" if ("collection", "col-discover") in library.recipes else "col-for-you-discovery"
        return Ok(
            (
                {"ref": disc_ref},
                {"ref": "col-streaming-services"},
                {"ref": "col-genres-moods"},
            )
        )

    if preset == "kids":
        return Ok(
            (
                {
                    "ref": "col-genres-moods",
                    "with": {
                        "genres": [
                            "fld-genre-animation",
                            "fld-genre-family",
                            "fld-genre-comedy",
                        ]
                    },
                },
                {
                    "ref": "col-franchises",
                    "with": {
                        "franchises": [
                            "fld-studio-pixar",
                            "fld-franchise-ghibli",
                            "fld-franchise-star-wars",
                            "fld-franchise-mcu",
                        ]
                    },
                },
                {
                    "ref": "col-streaming-services",
                    "with": {
                        "services": [
                            "fld-disney-plus",
                            "fld-netflix",
                            "fld-apple-tv",
                        ]
                    },
                },
            )
        )

    return Err(
        StreamWeaverError(
            f"Unsupported profile preset `{preset}` (choose from: standard, minimal, kids, clone)"
        )
    )


def manage_studio_profile(
    paths: TenantPaths,
    spec: Mapping[str, Any],
    *,
    extra_library_roots: Sequence[Path] = (),
) -> Result[JsonDict, StreamWeaverError]:
    """
    Creates or deletes a household profile (`standard`, `minimal`, `kids`, or `clone` preset,
    with optional dedicated `aiometadata.<name>.json` instance), updates `tenant.json`, syncs
    derived catalogs / AIOManager accounts / architecture diagrams, and returns the fresh snapshot.
    """
    action = str(spec.get("action") or "create").strip().lower()
    raw_name = str(spec.get("name") or "").strip()

    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass
    match read_json_file(paths.manifest_file):
        case Err(m_err):
            return Err(m_err)
        case Ok(manifest_doc):
            pass

    if action == "create":
        if not _PROFILE_SLUG_RE.match(raw_name) or not _STRICT_NAME_RE.match(raw_name):
            return Err(
                StreamWeaverError(
                    f"Profile slug `{raw_name}` must start with a lowercase letter and match "
                    "`^[a-z][a-z0-9_-]{0,31}$`"
                )
            )
        if raw_name in ws.manifest.profiles or profile_file(paths, raw_name).exists():
            return Err(StreamWeaverError(f"Profile `{raw_name}` already exists"))

        preset = str(spec.get("preset") or "standard").strip().lower()
        dedicated_meta = bool(spec.get("dedicatedMetadataInstance", False))
        if dedicated_meta and raw_name == DEFAULT_INSTANCE:
            return Err(
                StreamWeaverError(
                    "Profile name `default` cannot allocate a dedicated AIOMetadata instance"
                )
            )

        display_name = (
            str(spec.get("displayName") or "").strip()
            or raw_name.replace("-", " ").replace("_", " ").title()
        )
        default_emoji = "🧸" if preset == "kids" else "🎬"
        default_color = "#f59e0b" if preset == "kids" else "#3b82f6"
        emoji = str(spec.get("emoji") or "").strip() or default_emoji
        accent_color = str(spec.get("accentColor") or "").strip() or default_color
        nuvio_profile_name = str(spec.get("nuvioProfileName") or "").strip() or display_name

        first_prof = ws.profiles[0].profile if ws.profiles else None
        nuvio_login = (
            str(spec.get("nuvioLogin") or "").strip()
            or (first_prof.nuvio.login if (first_prof and first_prof.nuvio) else "main")
        )
        stremio_login = (
            str(spec.get("stremioLogin") or "").strip()
            or (first_prof.stremio.login if (first_prof and first_prof.stremio) else nuvio_login)
        )

        if preset == "clone":
            src_name = str(spec.get("sourceProfile") or "").strip() or (
                first_prof.name if first_prof else ""
            )
            src_rp = next((rp for rp in ws.profiles if rp.profile.name == src_name), None)
            if src_rp is None:
                return Err(StreamWeaverError(f"Clone source profile `{src_name}` not found"))
            src_p = src_rp.profile
            collections = copy.deepcopy(src_p.collections)
            badges = copy.deepcopy(src_p.badges)
            meta = copy.deepcopy(src_p.meta)
            settings = copy.deepcopy(src_p.settings)
            extra_catalogs = copy.deepcopy(src_p.extra_catalogs)
            extra_addons = copy.deepcopy(src_p.extra_addons)
            stremio_enabled = (
                bool(spec["stremioEnabled"])
                if "stremioEnabled" in spec
                else (src_p.stremio is not None)
            )
        else:
            match _preset_collections_for_profile(preset, ws.library):
                case Err() as p_err:
                    return p_err
                case Ok(collections):
                    pass
            badges = {"ref": "default"} if ("badges", "default") in ws.library.recipes else None
            meta = (
                None
                if preset == "kids"
                else ({"ref": "default"} if ("meta", "default") in ws.library.recipes else None)
            )
            settings = {"ref": "default"} if ("settings", "default") in ws.library.recipes else None
            extra_catalogs = ()
            cinemeta_extra = ExtraAddon(
                name="cinemeta",
                transport_url="https://v3-cinemeta.strem.io",
                custom_name="Cinemeta",
                metadata={"cinemetaConfig": dict(DEFAULT_CINEMETA_CONFIG)},
                enabled=True,
            )
            if preset == "standard" and ("collection", "col-for-you-discovery") in ws.library.recipes:
                bingecat_extra = ExtraAddon(
                    name="bingecat",
                    transport_url="https://bingecat.com/stremio/{{SECRET_BINGECAT_TOKEN}}/manifest.json",
                    custom_name="BingeCat",
                    enabled=True,
                )
                extra_addons = (bingecat_extra, cinemeta_extra)
            else:
                extra_addons = (cinemeta_extra,)
            stremio_enabled = bool(spec.get("stremioEnabled", False))

        meta_inst_name = raw_name if dedicated_meta else DEFAULT_INSTANCE
        if dedicated_meta:
            match _ensure_aiometadata_instance_registered(paths, raw_name):
                case Err() as inst_err:
                    return inst_err
                case Ok():
                    pass
            match read_json_file(paths.manifest_file):
                case Err(r_err):
                    return Err(r_err)
                case Ok(manifest_doc):
                    pass

        new_prof = Profile(
            name=raw_name,
            display_name=display_name,
            instances={"aiostreams": DEFAULT_INSTANCE, "aiometadata": meta_inst_name},
            nuvio=NuvioClientSpec(
                login=nuvio_login,
                profile_id=None,
                profile_name=nuvio_profile_name,
            ),
            stremio=StremioClientSpec(login=stremio_login) if stremio_enabled else None,
            extra_addons=extra_addons,
            appearance=Appearance(emoji=emoji, accent_color=accent_color),
            collections=collections,
            badges=badges,
            meta=meta,
            settings=settings,
            extra_catalogs=extra_catalogs,
        )
        new_prof_doc = profile_to_doc(new_prof)
        match parse_profile(new_prof_doc):
            case Err(val_err):
                return Err(StreamWeaverError(str(val_err)))
            case Ok():
                pass
        match write_json_file(profile_file(paths, raw_name), new_prof_doc):
            case Err(w_err):
                return Err(w_err)
            case Ok():
                pass

        updated_profiles = [*ws.manifest.profiles, raw_name]
        updated_manifest_doc = {**manifest_doc, "profiles": updated_profiles}
        match parse_tenant_manifest(updated_manifest_doc):
            case Err(m_val_err):
                return Err(StreamWeaverError(str(m_val_err)))
            case Ok():
                pass
        match write_json_file(paths.manifest_file, updated_manifest_doc):
            case Err(w_err):
                return Err(w_err)
            case Ok():
                pass

        match _load_workspace(paths, extra_library_roots=extra_library_roots):
            case Err() as err:
                return err
            case Ok(reloaded_ws):
                pass
        match _sync_derived_files(reloaded_ws):
            case Err() as err:
                return err
            case Ok():
                pass
        return inspect_tenant_state(paths, extra_library_roots=extra_library_roots)

    if action == "delete":
        if raw_name not in ws.manifest.profiles:
            return Err(StreamWeaverError(f"Profile `{raw_name}` not found"))
        if len(ws.manifest.profiles) <= 1:
            return Err(
                StreamWeaverError("Cannot delete the last remaining profile in the workspace")
            )

        deleted_rp = next((rp for rp in ws.profiles if rp.profile.name == raw_name), None)
        deleted_acc_ids: set[str] = {stable_account_id(ws.manifest.name, raw_name)}
        deleted_meta_inst = ""
        if deleted_rp is not None:
            if deleted_rp.profile.account_id:
                deleted_acc_ids.add(deleted_rp.profile.account_id)
            deleted_meta_inst = deleted_rp.profile.instances.get("aiometadata", "")

        p_dir = profile_dir(paths, raw_name)
        if p_dir.exists():
            shutil.rmtree(p_dir, ignore_errors=True)

        remaining_profiles = [p for p in ws.manifest.profiles if p != raw_name]
        updated_manifest_doc = {**manifest_doc, "profiles": remaining_profiles}

        # Clean up dedicated aiometadata.<name>.json if owned solely by this deleted profile
        still_uses_inst = any(
            rp.profile.name != raw_name
            and rp.profile.instances.get("aiometadata") == deleted_meta_inst
            for rp in ws.profiles
        )
        if (
            deleted_meta_inst
            and deleted_meta_inst != DEFAULT_INSTANCE
            and deleted_meta_inst == raw_name
            and not still_uses_inst
        ):
            instances_sec = dict(updated_manifest_doc.get("instances") or {})
            meta_reg = dict(instances_sec.get("aiometadata") or {})
            if deleted_meta_inst in meta_reg:
                meta_reg.pop(deleted_meta_inst, None)
                instances_sec["aiometadata"] = meta_reg
                updated_manifest_doc["instances"] = instances_sec
            ded_path = _service_doc_path(paths.docs_dir, "aiometadata", deleted_meta_inst)
            if ded_path.is_file():
                ded_path.unlink()

        match parse_tenant_manifest(updated_manifest_doc):
            case Err(m_val_err):
                return Err(StreamWeaverError(str(m_val_err)))
            case Ok():
                pass
        match write_json_file(paths.manifest_file, updated_manifest_doc):
            case Err(w_err):
                return Err(w_err)
            case Ok():
                pass

        mgr_path = paths.doc("aiomanager")
        if mgr_path.is_file():
            match read_json_file(mgr_path):
                case Ok(mgr_doc):
                    raw_accs = mgr_doc.get("accounts")
                    if isinstance(raw_accs, list):
                        filtered_accs = [
                            a
                            for a in raw_accs
                            if not (
                                isinstance(a, Mapping) and str(a.get("id") or "") in deleted_acc_ids
                            )
                        ]
                        if len(filtered_accs) != len(raw_accs):
                            _ = write_json_file(mgr_path, {**mgr_doc, "accounts": filtered_accs})
                case Err():
                    pass

        match _load_workspace(paths, extra_library_roots=extra_library_roots):
            case Err() as err:
                return err
            case Ok(reloaded_ws):
                pass
        match _sync_derived_files(reloaded_ws):
            case Err() as err:
                return err
            case Ok():
                pass
        return inspect_tenant_state(paths, extra_library_roots=extra_library_roots)

    return Err(
        StreamWeaverError(f"Unsupported profile management action `{action}` (choose `create` or `delete`)")
    )


def verify_workspace_health(
    paths: TenantPaths,
    *,
    pending_env: Mapping[str, str] | None = None,
    probe_network: bool = True,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[JsonDict, StreamWeaverError]:
    """
    Runs live health doctor checks for `paths`, overlaying `pending_env` (for Wizard Step 2
    live verification before saving `.env`) and computing exact dynamic required keys.
    """
    env = load_env_file(paths.env_file)
    if pending_env:
        env.update({str(k): str(v).strip() for k, v in pending_env.items() if str(v).strip()})

    match inspect_tenant_state(paths, env_override=env, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(snap):
            pass

    required_keys = [
        str(item["key"])
        for item in snap.get("env", [])
        if isinstance(item, Mapping) and item.get("required")
    ]

    def _run(client: HttpClientProtocol) -> Result[JsonDict, StreamWeaverError]:
        return Ok(
            run_health_checks(
                env=env,
                required_keys=required_keys,
                http_client=client,
                probe_network=probe_network,
            )
        )

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def inspect_workspace_addon_manifest(
    url: str,
    *,
    http_client: HttpClientProtocol | None = None,
) -> Result[JsonDict, StreamWeaverError]:
    """Fetches and validates an arbitrary Stremio/Nuvio `manifest.json` URL."""
    if http_client is not None:
        return inspect_addon_manifest(url, http_client=http_client)
    with HttpxClient() as live_client:
        return inspect_addon_manifest(url, http_client=live_client)


def _deterministic_sample_cards(
    catalog_id: str, media_type: str, title_hint: str
) -> list[JsonDict]:
    pool = _SAMPLE_SERIES_CARDS if media_type == "series" else _SAMPLE_MOVIE_CARDS
    seed_bytes = hashlib.sha256(f"{catalog_id}|{media_type}|{title_hint}".encode()).digest()
    offset = seed_bytes[0] % len(pool)
    rotated = [*pool[offset:], *pool[:offset]]
    return [copy.deepcopy(card) for card in rotated[:10]]


def preview_catalog_items(
    paths: TenantPaths,
    spec: Mapping[str, Any],
    *,
    pending_env: Mapping[str, str] | None = None,
    http_client: HttpClientProtocol | None = None,
) -> Result[JsonDict, StreamWeaverError]:
    """
    Previews up to 10 `{id, title, year, posterUrl, rating, type}` poster cards for a Studio Home
    folder/row:
    1. Live provisioned AIOMetadata `/catalog/{type}/{id}.json` when configured, else
    2. Live TMDB trending/discover API when `SECRET_TMDB_API_KEY` is set, else
    3. Deterministic realistic sample cards (`"source": "sample"`) for instant offline previews.
    """
    src_obj: Mapping[str, Any] = spec
    if isinstance(spec.get("sourceSpec"), Mapping):
        src_obj = spec["sourceSpec"]
    elif isinstance(spec.get("sources"), list) and spec["sources"] and isinstance(spec["sources"][0], Mapping):
        src_obj = spec["sources"][0]

    catalog_id = str(spec.get("catalogId") or src_obj.get("catalogId") or "").strip()
    raw_type = str(spec.get("type") or spec.get("media") or src_obj.get("type") or "movie").strip().lower()
    media_type = "series" if raw_type in ("series", "tv", "show", "shows", "anime") else "movie"
    title_hint = str(spec.get("title") or src_obj.get("title") or catalog_id or "Preview").strip()
    inst_name = str(spec.get("instance") or spec.get("aiometadataInstance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE

    env = load_env_file(paths.env_file)
    if pending_env:
        env.update({str(k): str(v).strip() for k, v in pending_env.items() if str(v).strip()})

    meta_url = env.get("AIOMETADATA_URL", "").strip().rstrip("/")
    inst_uuid_key = (
        "AIOMETADATA_UUID"
        if inst_name == DEFAULT_INSTANCE
        else f"AIOMETADATA_{inst_name.upper().replace('-', '_')}_UUID"
    )
    meta_uuid = (env.get(inst_uuid_key) or env.get("AIOMETADATA_UUID") or "").strip()
    tmdb_key = env.get("SECRET_TMDB_API_KEY", "").strip()

    has_live_candidate = bool((meta_url and meta_uuid and catalog_id) or tmdb_key)
    if not has_live_candidate:
        return Ok(
            {
                "source": "sample",
                "catalogId": catalog_id,
                "type": media_type,
                "items": _deterministic_sample_cards(catalog_id, media_type, title_hint),
            }
        )

    def _fetch(client: HttpClientProtocol) -> Result[JsonDict, StreamWeaverError]:
        if meta_url and meta_uuid and catalog_id:
            q_type = urllib.parse.quote(media_type, safe=".:_-")
            q_cid = urllib.parse.quote(catalog_id, safe=".:_-")
            cat_url = f"{meta_url}/stremio/{meta_uuid}/catalog/{q_type}/{q_cid}.json"
            match client.request_json("GET", cat_url, timeout=10.0):
                case Ok(resp) if resp.status_code == 200 and isinstance(resp.data, Mapping):
                    metas = resp.data.get("metas")
                    if isinstance(metas, list) and metas:
                        live_items: list[JsonDict] = []
                        for idx, m in enumerate(metas[:10]):
                            if not isinstance(m, Mapping):
                                continue
                            rel = str(m.get("releaseInfo") or m.get("year") or "").strip()
                            live_items.append(
                                {
                                    "id": str(m.get("id") or f"meta-{idx}"),
                                    "title": str(m.get("name") or m.get("title") or "Untitled"),
                                    "year": rel[:4],
                                    "posterUrl": str(m.get("poster") or m.get("posterUrl") or ""),
                                    "rating": str(m.get("imdbRating") or m.get("rating") or ""),
                                    "type": str(m.get("type") or media_type),
                                }
                            )
                        if live_items:
                            return Ok(
                                {
                                    "source": "aiometadata",
                                    "catalogId": catalog_id,
                                    "type": media_type,
                                    "items": live_items,
                                }
                            )
                case _:
                    pass

        if tmdb_key:
            tmdb_media = "tv" if media_type == "series" else "movie"
            q_key = urllib.parse.quote(tmdb_key, safe="")
            tmdb_url = f"https://api.themoviedb.org/3/trending/{tmdb_media}/week?api_key={q_key}"
            match client.request_json("GET", tmdb_url, timeout=10.0):
                case Ok(resp) if resp.status_code == 200 and isinstance(resp.data, Mapping):
                    results = resp.data.get("results")
                    if isinstance(results, list) and results:
                        tmdb_items: list[JsonDict] = []
                        for idx, r in enumerate(results[:10]):
                            if not isinstance(r, Mapping):
                                continue
                            poster_path = str(r.get("poster_path") or "")
                            poster_url = (
                                f"https://image.tmdb.org/t/p/w500{poster_path}"
                                if poster_path.startswith("/")
                                else poster_path
                            )
                            date_str = str(r.get("release_date") or r.get("first_air_date") or "")
                            vote = r.get("vote_average")
                            rating_str = (
                                f"{float(vote):.1f}"
                                if isinstance(vote, (int, float)) and not isinstance(vote, bool) and vote > 0
                                else ""
                            )
                            tmdb_items.append(
                                {
                                    "id": f"tmdb:{r.get('id', idx)}",
                                    "title": str(r.get("title") or r.get("name") or "Untitled"),
                                    "year": date_str[:4],
                                    "posterUrl": poster_url,
                                    "rating": rating_str,
                                    "type": media_type,
                                }
                            )
                        if tmdb_items:
                            return Ok(
                                {
                                    "source": "tmdb",
                                    "catalogId": catalog_id,
                                    "type": media_type,
                                    "items": tmdb_items,
                                }
                            )
                case _:
                    pass

        return Ok(
            {
                "source": "sample",
                "catalogId": catalog_id,
                "type": media_type,
                "items": _deterministic_sample_cards(catalog_id, media_type, title_hint),
            }
        )

    if http_client is not None:
        return _fetch(http_client)
    with HttpxClient() as live_client:
        return _fetch(live_client)


def _available_raw_docs(paths: TenantPaths) -> list[JsonDict]:
    manifest_doc = _load_optional_json(paths.manifest_file)
    raw_profiles = manifest_doc.get("profiles")
    prof_names = (
        [str(p) for p in raw_profiles if isinstance(p, str) and _STRICT_NAME_RE.match(p)]
        if isinstance(raw_profiles, list)
        else []
    )
    docs: list[JsonDict] = [
        {"key": "aiostreams", "label": "aiostreams.json"},
        {"key": "aiometadata", "label": "aiometadata.json"},
    ]
    raw_instances = manifest_doc.get("instances")
    meta_insts = raw_instances.get("aiometadata") if isinstance(raw_instances, Mapping) else None
    if isinstance(meta_insts, Mapping):
        for inst_name in sorted(meta_insts):
            if inst_name != DEFAULT_INSTANCE and _STRICT_NAME_RE.match(str(inst_name)):
                docs.append(
                    {
                        "key": f"aiometadata:{inst_name}",
                        "label": f"aiometadata.{inst_name}.json",
                    }
                )
    docs.append({"key": "aiomanager", "label": "aiomanager.json"})
    docs.append({"key": "tenant", "label": "tenant.json"})
    for p in prof_names:
        docs.append({"key": f"profile:{p}", "label": f"profiles/{p}/profile.json"})
    return docs


def _resolve_raw_doc_target(
    paths: TenantPaths, doc_key: str
) -> Result[tuple[str, Path, ServiceId | None, str | None], StreamWeaverError]:
    cleaned = doc_key.strip()
    if cleaned in ("aiostreams", "aiometadata", "aiomanager"):
        svc_id: ServiceId = cleaned  # type: ignore[assignment]
        return Ok((cleaned, paths.doc(cleaned), svc_id, None))
    if cleaned.startswith("aiometadata:"):
        inst_name = cleaned.partition(":")[2].strip()
        if not _STRICT_NAME_RE.match(inst_name):
            return Err(StreamWeaverError(f"Invalid AIOMetadata instance key `{doc_key}`"))
        return Ok((f"aiometadata:{inst_name}", _service_doc_path(paths.docs_dir, "aiometadata", inst_name), "aiometadata", None))
    if cleaned == "tenant":
        return Ok(("tenant", paths.manifest_file, None, None))
    if cleaned == "profile":
        manifest_doc = _load_optional_json(paths.manifest_file)
        profs = manifest_doc.get("profiles")
        first = str(profs[0]) if isinstance(profs, list) and profs else "main"
        cleaned = f"profile:{first}"
    if cleaned.startswith("profile:"):
        p_name = cleaned.partition(":")[2].strip()
        if not _STRICT_NAME_RE.match(p_name):
            return Err(StreamWeaverError(f"Invalid profile document key `{doc_key}`"))
        pf = profile_file(paths, p_name)
        if not pf.is_file():
            return Err(StreamWeaverError(f"Profile `{p_name}` does not exist"))
        return Ok((f"profile:{p_name}", pf, None, p_name))
    return Err(
        StreamWeaverError(
            f"Unsupported raw document key `{doc_key}` "
            "(choose from `aiostreams`, `aiometadata`, `aiomanager`, `tenant`, or `profile:<name>`)"
        )
    )


def inspect_raw_document(
    paths: TenantPaths,
    doc_key: str = "aiostreams",
) -> Result[JsonDict, StreamWeaverError]:
    """Reads a scrubbed workspace JSON document (`aiostreams`, `aiometadata`, `aiomanager`, `tenant`, `profile:<name>`)."""
    match _resolve_raw_doc_target(paths, doc_key):
        case Err() as err:
            return err
        case Ok((canonical_key, doc_path, _svc_id, _prof_name)):
            pass

    if doc_path.is_file():
        match read_json_file(doc_path):
            case Err(r_err):
                return Err(r_err)
            case Ok(parsed):
                content = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
    else:
        content = "{}\n"

    return Ok(
        {
            "doc": canonical_key,
            "content": content,
            "availableDocs": _available_raw_docs(paths),
        }
    )


def save_raw_document(
    paths: TenantPaths,
    doc_key: str,
    content_str: str,
    *,
    extra_library_roots: Sequence[Path] = (),
) -> Result[JsonDict, StreamWeaverError]:
    """
    Validates, scrubs secrets (harvesting any pasted raw API keys into `.env` so plaintext secrets
    never touch JSON files on disk), writes `doc_key`, syncs derived artifacts, and returns
    `{"doc", "content", "availableDocs", "state"}`. Rolls back if workspace compilation fails.
    """
    match _resolve_raw_doc_target(paths, doc_key):
        case Err() as err:
            return err
        case Ok((canonical_key, doc_path, svc_id, prof_name)):
            pass

    try:
        parsed = json.loads(content_str)
    except ValueError as exc:
        return Err(StreamWeaverError(f"Invalid JSON in `{canonical_key}`: {exc}"))

    if not isinstance(parsed, dict):
        return Err(StreamWeaverError(f"`{canonical_key}` root must be a JSON object"))

    doc_to_write: JsonDict = parsed
    harvested_env: dict[str, str] | None = None

    if svc_id is not None:
        match normalize_service_config(svc_id, parsed):
            case Err(norm_err):
                return Err(StreamWeaverError(str(norm_err)))
            case Ok(normalized):
                pass
        cur_env = load_env_file(paths.env_file)
        scrubbed_doc, updated_env = harvest_and_scrub(svc_id, normalized, cur_env)
        doc_to_write = scrubbed_doc
        if updated_env != cur_env:
            harvested_env = updated_env
    elif canonical_key == "tenant":
        match parse_tenant_manifest(parsed):
            case Err(t_err):
                return Err(StreamWeaverError(str(t_err)))
            case Ok(parsed_manifest):
                for p in parsed_manifest.profiles:
                    if not profile_file(paths, p).is_file():
                        return Err(
                            StreamWeaverError(
                                f"tenant.json references missing profile `{p}`"
                            )
                        )
    elif prof_name is not None:
        match parse_profile(parsed):
            case Err(p_err):
                return Err(StreamWeaverError(str(p_err)))
            case Ok(parsed_prof):
                if parsed_prof.name != prof_name:
                    return Err(
                        StreamWeaverError(
                            f"Profile `name` must remain `{prof_name}` (got `{parsed_prof.name}`)"
                        )
                    )

    existed_before = doc_path.is_file()
    previous_bytes = doc_path.read_bytes() if existed_before else None

    match write_json_file(doc_path, doc_to_write):
        case Err(w_err):
            return Err(w_err)
        case Ok():
            pass

    match _load_workspace(
        paths,
        env_override=harvested_env,
        extra_library_roots=extra_library_roots,
    ):
        case Err(comp_err):
            if previous_bytes is not None:
                doc_path.write_bytes(previous_bytes)
            elif doc_path.is_file():
                doc_path.unlink()
            return Err(comp_err)
        case Ok(ws):
            pass

    if harvested_env is not None:
        match save_env_updates(paths.env_file, harvested_env):
            case Err(e_err):
                return Err(e_err)
            case Ok():
                pass
        _ = scaffold_env_keys(paths.env_example_file, list(harvested_env))

    match _sync_derived_files(ws):
        case Err() as sync_err:
            return sync_err
        case Ok():
            pass

    match inspect_tenant_state(paths, extra_library_roots=extra_library_roots):
        case Err() as state_err:
            return state_err
        case Ok(snap):
            pass

    final_doc = _load_optional_json(doc_path)
    final_content = json.dumps(final_doc, indent=2, ensure_ascii=False) + "\n"
    return Ok(
        {
            "doc": canonical_key,
            "content": final_content,
            "availableDocs": _available_raw_docs(paths),
            "state": snap,
        }
    )


def _build_warm_state(
    ws: _LoadedWorkspace,
    working_env: Mapping[str, str],
    client: HttpClientProtocol,
) -> JsonDict:
    mgr_doc = _load_optional_json(ws.paths.doc("aiomanager"))
    managed = apply_managed_accounts(mgr_doc or {"version": "2.0.0", "accounts": []}, ws.manifest.name, ws.profiles).state
    probe = copy.deepcopy(managed)
    for acc in probe.get("accounts", []):
        if isinstance(acc, dict) and isinstance(acc.get("addons"), list):
            for addon in acc["addons"]:
                if isinstance(addon, dict) and isinstance(addon.get("transportUrl"), str):
                    addon["transportUrl"] = SECRET_PLACEHOLDER_RE.sub(
                        lambda m: working_env.get(m.group(1), "") or m.group(0),
                        addon["transportUrl"],
                    )
    addons_sec = probe.get("addons")
    if isinstance(addons_sec, dict) and isinstance(addons_sec.get("savedAddons"), list):
        for sa in addons_sec["savedAddons"]:
            if isinstance(sa, dict) and isinstance(sa.get("installUrl"), str):
                sa["installUrl"] = SECRET_PLACEHOLDER_RE.sub(
                    lambda m: working_env.get(m.group(1), "") or m.group(0),
                    sa["installUrl"],
                )
    if not extract_home_catalog_urls(probe):
        probe = refresh_addon_manifests(client, probe)
    return probe


def warm_workspace_catalogs(
    paths: TenantPaths,
    *,
    workers: int = 8,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[JsonDict, StreamWeaverError]:
    """Concurrently pre-warms all enabled Home catalog endpoints across the workspace's addons."""
    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    def _run(client: HttpClientProtocol) -> Result[JsonDict, StreamWeaverError]:
        probe_state = _build_warm_state(ws, ws.env, client)
        warmed, total = warm_home_catalogs(probe_state, client, max_workers=max(1, workers))
        return Ok(
            {
                "warmed": warmed,
                "total": total,
                "message": f"[warm] Pre-warmed {warmed}/{total} Home catalog rows",
            }
        )

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def login_nuvio_and_list_profiles(
    paths: TenantPaths,
    *,
    login: str = "",
    email: str | None = None,
    password: str | None = None,
    http_client: HttpClientProtocol | None = None,
) -> Result[JsonDict, StreamWeaverError]:
    """Signs in to Nuvio (when email+password given) and returns the login's Nuvio profiles list."""
    match read_json_file(paths.manifest_file):
        case Err(err):
            return Err(err)
        case Ok(manifest_doc):
            pass
    match parse_tenant_manifest(manifest_doc):
        case Err(m_err):
            return Err(StreamWeaverError(str(m_err)))
        case Ok(manifest):
            pass

    env = load_env_file(paths.env_file)
    anon_key = resolve_nuvio_anon_key(env)
    existing_nuvio = manifest.logins.get("nuvio", {})
    login_name = login.strip().lower() or next(iter(existing_nuvio), "main")
    default_prefix = "NUVIO" if login_name == "main" else f"NUVIO_{login_name.upper().replace('-', '_')}"
    env_prefix = existing_nuvio.get(login_name, default_prefix)

    def _run(client: HttpClientProtocol) -> Result[JsonDict, StreamWeaverError]:
        working = dict(env)
        if email and password:
            match password_grant(client, anon_key, email.strip(), password):
                case Err(auth_err):
                    return Err(StreamWeaverError(str(auth_err)))
                case Ok((acc_tok, ref_tok)):
                    pass
            logins_sec = dict(manifest_doc.get("logins") or {})
            nuvio_reg = dict(logins_sec.get("nuvio") or {})
            if nuvio_reg.get(login_name) != env_prefix:
                nuvio_reg[login_name] = env_prefix
                logins_sec["nuvio"] = nuvio_reg
                _ = write_json_file(paths.manifest_file, {**manifest_doc, "logins": logins_sec})
            updates = {
                login_env_key(env_prefix, "ACCESS_TOKEN"): acc_tok,
                login_env_key(env_prefix, "REFRESH_TOKEN"): ref_tok,
                login_env_key(env_prefix, "EMAIL"): email.strip(),
            }
            match save_env_updates(paths.env_file, updates):
                case Err(s_err):
                    return Err(s_err)
                case Ok():
                    pass
            _ = scaffold_env_keys(paths.env_example_file, list(updates))
            working.update(updates)

        match parse_tenant_manifest(_load_optional_json(paths.manifest_file) or manifest_doc):
            case Err(p_err):
                return Err(StreamWeaverError(str(p_err)))
            case Ok(cur_manifest):
                pass
        match resolve_login_binding(cur_manifest, working, "nuvio", login_name):
            case Err(lb_err):
                return Err(StreamWeaverError(str(lb_err)))
            case Ok(lb):
                pass

        pool = NuvioSessionPool(client, anon_key)
        match pool.acquire(
            access_token=lb.credentials.get("accessToken", ""),
            refresh_token=lb.credentials.get("refreshToken", ""),
            env=working,
        ):
            case Err(sess_err):
                return Err(StreamWeaverError(str(sess_err)))
            case Ok(session):
                pass
        if session.refreshed_secrets:
            _ = save_env_updates(paths.env_file, session.refreshed_secrets)
        return Ok(
            {
                "login": login_name,
                "email": lb.credentials.get("email", ""),
                "profiles": [
                    {
                        "index": p.get("profile_index") or p.get("profileIndex") or 1,
                        "name": str(p.get("name") or p.get("profile_name") or "Main"),
                        "id": str(p.get("id") or ""),
                    }
                    for p in session.profiles
                ],
            }
        )

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def provision_instances(
    paths: TenantPaths,
    *,
    extra_library_roots: Sequence[Path] = (),
    on_step: Callable[[str], None] | None = None,
    http_client: HttpClientProtocol | None = None,
) -> Result[PushReport, StreamWeaverError]:
    """Provisions brand-new AIOStreams and/or AIOMetadata UUIDs (`--create`) for unprovisioned instances."""
    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    def _run(client: HttpClientProtocol) -> Result[PushReport, StreamWeaverError]:
        services = build_config_services(client)
        working = dict(ws.env)
        steps: list[str] = []

        def _emit(msg: str) -> None:
            steps.append(msg)
            if on_step is not None:
                on_step(msg)

        # Provision default AIOStreams + every registered AIOMetadata instance that lacks a UUID
        targets_to_provision: list[tuple[ServiceId, InstanceBinding, Path]] = []
        if "aiostreams" in ws.bindings:
            targets_to_provision.append(
                ("aiostreams", ws.bindings["aiostreams"], paths.doc("aiostreams"))
            )
        for inst_name in ws.manifest.instances.get("aiometadata", {DEFAULT_INSTANCE: "AIOMETADATA"}):
            match resolve_instance_binding(ws.manifest, working, "aiometadata", inst_name):
                case Ok(mb):
                    targets_to_provision.append(
                        ("aiometadata", mb, _service_doc_path(paths.docs_dir, "aiometadata", inst_name))
                    )
                case Err():
                    continue

        for svc_id, binding, cfg_path in targets_to_provision:
            if not cfg_path.is_file():
                continue
            lbl = svc_id if binding.name == DEFAULT_INSTANCE else f"{svc_id}/{binding.name}"
            if binding.uuid:
                _emit(f"[{lbl}] Already provisioned (UUID {binding.uuid})")
                continue
            if not binding.configured_for_push(create=True):
                _emit(f"[{lbl}] Skipped: set {binding.url_env_key} and {binding.password_env_key} first")
                continue
            match read_json_file(cfg_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(scrubbed_cfg):
                    pass
            if svc_id == "aiometadata" and ws.compiled:
                match derive_instance_catalogs(binding.name, scrubbed_cfg, ws.compiled):
                    case Err(d_err):
                        return Err(StreamWeaverError(str(d_err)))
                    case Ok(plan):
                        if plan.changed:
                            scrubbed_cfg = {**scrubbed_cfg, "catalogs": plan.catalogs}
            payload = (
                prepare_aiostreams_for_push(scrubbed_cfg, working)
                if svc_id == "aiostreams"
                else (
                    prepare_aiometadata_for_push(scrubbed_cfg, working)
                    if svc_id == "aiometadata"
                    else scrubbed_cfg
                )
            )
            match inject_secrets(payload, working):
                case Err(sec_err):
                    return Err(StreamWeaverError(f"[{lbl}] {sec_err}"))
                case Ok(injected):
                    pass
            match services[svc_id].push_remote(injected, binding, working, create=True):
                case Err(net_err):
                    return Err(StreamWeaverError(str(net_err)))
                case Ok((outcome, _clean, discovered)):
                    pass
            updates = dict(discovered)
            if outcome.uuid:
                updates[binding.uuid_env_key] = outcome.uuid
            working.update(updates)
            match save_env_updates(paths.env_file, working):
                case Err(s_err):
                    return Err(s_err)
                case Ok():
                    pass
            _emit(f"[{lbl}] Provisioned UUID {outcome.uuid} → {outcome.install_url or ''}")

        match inspect_tenant_state(paths, extra_library_roots=extra_library_roots):
            case Err() as err:
                return err
            case Ok(snap):
                return Ok(PushReport(steps=tuple(steps), snapshot=snap))

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def _refresh_manifests_for_workspace(
    ws: _LoadedWorkspace,
    working_env: dict[str, str],
    client: HttpClientProtocol,
    extra_library_roots: Sequence[Path],
    *,
    instance: str = DEFAULT_INSTANCE,
) -> Result[tuple[_LoadedWorkspace, dict[str, str]], StreamWeaverError]:
    mgr_path = ws.paths.doc("aiomanager")
    mgr_doc = _load_optional_json(mgr_path)
    if mgr_doc:
        managed_doc = apply_managed_accounts(mgr_doc, ws.manifest.name, ws.profiles).state
        probe = copy.deepcopy(managed_doc)
        orig_acc: dict[tuple[int, int], str] = {}
        for i, acc in enumerate(probe.get("accounts", [])):
            if isinstance(acc, dict) and isinstance(acc.get("addons"), list):
                for j, addon in enumerate(acc["addons"]):
                    if isinstance(addon, dict) and isinstance(addon.get("transportUrl"), str):
                        orig_acc[(i, j)] = addon["transportUrl"]
                        addon["transportUrl"] = SECRET_PLACEHOLDER_RE.sub(
                            lambda m: working_env.get(m.group(1), "") or m.group(0),
                            addon["transportUrl"],
                        )
        orig_saved: dict[int, str] = {}
        addons_sec = probe.get("addons")
        if isinstance(addons_sec, dict) and isinstance(addons_sec.get("savedAddons"), list):
            for k, sa in enumerate(addons_sec["savedAddons"]):
                if isinstance(sa, dict) and isinstance(sa.get("installUrl"), str):
                    orig_saved[k] = sa["installUrl"]
                    sa["installUrl"] = SECRET_PLACEHOLDER_RE.sub(
                        lambda m: working_env.get(m.group(1), "") or m.group(0),
                        sa["installUrl"],
                    )
        refreshed = refresh_addon_manifests(client, probe)
        if refreshed != probe:
            for (i, j), raw_u in orig_acc.items():
                refreshed["accounts"][i]["addons"][j]["transportUrl"] = raw_u
            if isinstance(refreshed.get("addons"), dict) and isinstance(refreshed["addons"].get("savedAddons"), list):
                for k, raw_u in orig_saved.items():
                    refreshed["addons"]["savedAddons"][k]["installUrl"] = raw_u
            match dehydrate_aiomanager(refreshed):
                case Ok(dehydrated):
                    scrubbed_mgr, working_env = harvest_and_scrub("aiomanager", dehydrated, working_env)
                    canonical = apply_managed_accounts(scrubbed_mgr, ws.manifest.name, ws.profiles).state
                    if mgr_path.is_file():
                        _ = write_json_file(mgr_path, canonical)
                case Err():
                    pass

    match _load_workspace(
        ws.paths,
        instance=instance,
        env_override=working_env,
        extra_library_roots=extra_library_roots,
    ):
        case Err() as err:
            return err
        case Ok(reloaded):
            return Ok((reloaded, working_env))


def push_workspace(
    paths: TenantPaths,
    *,
    target: str = "all",
    instance: str = DEFAULT_INSTANCE,
    profile: str | None = None,
    create: bool = False,
    warm_catalogs: bool = False,
    on_step: Callable[[str], None] | None = None,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[PushReport, StreamWeaverError]:
    """
    Pushes the workspace's services (Phase 1 `aiostreams` + `aiometadata` in parallel, Phase 2
    `aiomanager`) and/or Nuvio profiles, supporting `--instance`, `--profile`, `--create`,
    real-time `on_step` streaming callbacks, and optional `warm_catalogs`.
    """
    match _parse_targets(target, implicit_all_nuvio=False):
        case Err() as t_err:
            return t_err
        case Ok((svc_targets, explicit_nuvio_docs)):
            pass

    match _load_workspace(paths, instance=instance, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    match _scope_bindings_for_profile(ws, profile):
        case Err() as sc_err:
            return sc_err
        case Ok((scoped_bindings, scoped_cps)):
            pass

    cleaned = target.strip().lower()
    phase1_targets = tuple(s for s in svc_targets if s != "aiomanager")
    phase2_targets = tuple(s for s in svc_targets if s == "aiomanager")

    def _run(client: HttpClientProtocol) -> Result[PushReport, StreamWeaverError]:
        nonlocal ws, scoped_bindings, scoped_cps
        services = build_config_services(client)
        working = dict(ws.env)
        steps: list[str] = []
        step_lock = threading.Lock()
        aiomanager_pushed = False

        def _emit(msg: str) -> None:
            with step_lock:
                steps.append(msg)
                if on_step is not None:
                    on_step(msg)

        def _execute_one_service(
            svc_id: ServiceId,
            cur_ws: _LoadedWorkspace,
            cur_bindings: Mapping[ServiceId, InstanceBinding],
            env_snap: Mapping[str, str],
        ) -> Result[
            tuple[ServicePushOutcome | None, JsonDict | None, dict[str, str], Path | None, bool, list[str]],
            StreamWeaverError,
        ]:
            binding = cur_bindings.get(svc_id)
            if binding is None or not binding.configured_for_push(create=create):
                return Ok((None, None, {}, None, False, [f"[{svc_id}] Skipped (not configured in .env)"]))
            cfg_path = _service_doc_path(paths.docs_dir, svc_id, binding.name)
            if not cfg_path.is_file():
                return Ok((None, None, {}, None, False, [f"[{svc_id}] Skipped ({cfg_path.name} not found)"]))
            match read_json_file(cfg_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(scrubbed_cfg):
                    pass

            local_notes: list[str] = []
            derived_changed = False
            if svc_id == "aiomanager":
                outcome_mgr = apply_managed_accounts(scrubbed_cfg, cur_ws.manifest.name, cur_ws.profiles)
                scrubbed_cfg = outcome_mgr.state
                by_id = {cp.resolved.profile.account_id or "": cp for cp in cur_ws.compiled}
                by_name = {cp.resolved.profile.display_name: cp for cp in cur_ws.compiled}
                for acc in scrubbed_cfg.get("accounts", []):
                    cp = by_id.get(str(acc.get("id"))) or by_name.get(str(acc.get("name")))
                    if cp is not None:
                        rp = cp.resolved
                        if rp.profile.nuvio and rp.profile.nuvio.profile_name and not rp.profile.nuvio.profile_id:
                            acc["nuvioProfileName"] = rp.profile.nuvio.profile_name
                        acc_payload = cp.account_payload()
                        if "nuvioMeta" in acc_payload:
                            match inject_secrets(acc_payload["nuvioMeta"], env_snap):
                                case Err(m_err):
                                    local_notes.append(f"[{cp.name}] meta: skipped ({m_err})")
                                    acc_payload.pop("nuvioMeta", None)
                                case Ok():
                                    pass
                        acc.update(acc_payload)

            if svc_id == "aiometadata" and cur_ws.compiled:
                match derive_instance_catalogs(binding.name, scrubbed_cfg, cur_ws.compiled):
                    case Err(d_err):
                        return Err(StreamWeaverError(str(d_err)))
                    case Ok(plan):
                        if plan.changed:
                            scrubbed_cfg = {**scrubbed_cfg, "catalogs": plan.catalogs}
                            derived_changed = True

            payload = (
                prepare_aiostreams_for_push(scrubbed_cfg, env_snap)
                if svc_id == "aiostreams"
                else (
                    prepare_aiometadata_for_push(scrubbed_cfg, env_snap)
                    if svc_id == "aiometadata"
                    else scrubbed_cfg
                )
            )
            match inject_secrets(payload, env_snap):
                case Err(sec_err):
                    if svc_id == "aiomanager" and any(
                        k.startswith("SECRET_STREMIO") for k in sec_err.missing_keys
                    ):
                        return Err(
                            StreamWeaverError(
                                f"{sec_err} (run `sw login stremio --login <name>`, or remove `clients.stremio` "
                                "from profile.json if you only use Nuvio)"
                            )
                        )
                    if svc_id == "aiomanager" and any(
                        k.startswith("SECRET_NUVIO") for k in sec_err.missing_keys
                    ):
                        return Err(
                            StreamWeaverError(
                                f"{sec_err} (run `sw login nuvio --login <name>`)"
                            )
                        )
                    return Err(StreamWeaverError(f"[{svc_id}] {sec_err}"))
                case Ok(injected):
                    pass

            match services[svc_id].push_remote(injected, binding, env_snap, create=create):
                case Err(net_err):
                    return Err(StreamWeaverError(str(net_err)))
                case Ok((outcome, updated_cfg, discovered)):
                    pass

            disc_out = dict(discovered)
            if outcome.created and outcome.uuid:
                disc_out[binding.uuid_env_key] = outcome.uuid
            return Ok((outcome, updated_cfg, disc_out, cfg_path, derived_changed, local_notes))

        for phase in (phase1_targets, phase2_targets):
            if not phase:
                continue
            if phase == phase2_targets and any(
                (b := scoped_bindings.get(sid)) is not None and b.configured_for_push(create=create)
                for sid in phase
            ):
                match _refresh_manifests_for_workspace(
                    ws, working, client, extra_library_roots, instance=instance
                ):
                    case Err() as err:
                        return err
                    case Ok((ws, working)):
                        pass
                match _scope_bindings_for_profile(ws, profile):
                    case Err() as sc_err2:
                        return sc_err2
                    case Ok((scoped_bindings, scoped_cps)):
                        pass

            if len(phase) > 1:
                env_for_phase = dict(working)
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(phase)) as pool:
                    fut_map = {
                        sid: pool.submit(_execute_one_service, sid, ws, scoped_bindings, env_for_phase)
                        for sid in phase
                    }
                    phase_results = {sid: fut_map[sid].result() for sid in phase}
            else:
                only_sid = phase[0]
                phase_results = {
                    only_sid: _execute_one_service(only_sid, ws, scoped_bindings, working)
                }

            env_dirty = False
            for svc_id in phase:
                match phase_results[svc_id]:
                    case Err() as svc_err:
                        return svc_err
                    case Ok((outcome, updated_cfg, disc_out, cfg_path, derived_changed, notes)):
                        for note in notes:
                            _emit(note)
                        if outcome is None:
                            continue
                        if disc_out:
                            working.update(disc_out)
                            env_dirty = True
                        if svc_id == "aiometadata" and derived_changed and updated_cfg is not None and cfg_path is not None:
                            scrubbed_meta, working = harvest_and_scrub("aiometadata", updated_cfg, working)
                            _ = write_json_file(cfg_path, scrubbed_meta)
                            env_dirty = True
                        if svc_id == "aiomanager" and updated_cfg is not None and cfg_path is not None:
                            aiomanager_pushed = True
                            scrubbed_back, working = harvest_and_scrub("aiomanager", updated_cfg, working)
                            _ = write_json_file(cfg_path, scrubbed_back)
                            env_dirty = True
                        if outcome.created:
                            suffix = f" → {outcome.install_url}" if outcome.install_url else ""
                            _emit(f"[{svc_id}] Created UUID {outcome.uuid}{suffix}")
                        else:
                            _emit(f"[{svc_id}] Updated UUID {outcome.uuid}")
                        for c_msg in outcome.synced_clients:
                            _emit(c_msg)

            if env_dirty:
                match save_env_updates(paths.env_file, working):
                    case Err(s_err):
                        return Err(s_err)
                    case Ok():
                        pass

        nuvio_docs: tuple[NuvioDoc, ...] = explicit_nuvio_docs
        if (
            not nuvio_docs
            and cleaned == "all"
            and not aiomanager_pushed
            and any((lb := cp.resolved.logins.get("nuvio")) is not None and lb.configured() for cp in scoped_cps)
        ):
            nuvio_docs = ALL_NUVIO_DOCS

        if nuvio_docs:
            if "collections" in nuvio_docs and not aiomanager_pushed:
                match _refresh_manifests_for_workspace(
                    ws, working, client, extra_library_roots, instance=instance
                ):
                    case Err() as err:
                        return err
                    case Ok((ws, working)):
                        pass
                match _scope_bindings_for_profile(ws, profile):
                    case Err() as sc_err3:
                        return sc_err3
                    case Ok((scoped_bindings, scoped_cps)):
                        pass
            match nuvio_targets(
                tenant_name=ws.manifest.name,
                scrubbed_aiomanager=_load_optional_json(paths.doc("aiomanager")),
                env=working,
                compiled_profiles=scoped_cps,
            ):
                case Err(t_err):
                    return Err(t_err)
                case Ok(targets):
                    pass
            if targets:
                match push_nuvio(targets, nuvio_docs, working, client):
                    case Err(p_err):
                        return Err(p_err)
                    case Ok((msgs, refreshed)):
                        for m in msgs:
                            _emit(m)
                        if refreshed:
                            working.update(refreshed)
                            _ = save_env_updates(paths.env_file, working)

        if warm_catalogs and not aiomanager_pushed:
            probe_state = _build_warm_state(ws, working, client)
            warmed, total = warm_home_catalogs(probe_state, client)
            _emit(f"[warm] Pre-warmed {warmed}/{total} Home catalog rows")

        match _load_workspace(
            paths, instance=instance, env_override=working, extra_library_roots=extra_library_roots
        ):
            case Ok(final_ws):
                _ = _sync_derived_files(final_ws)
            case Err():
                _ = _sync_derived_files(ws)

        match inspect_tenant_state(paths, extra_library_roots=extra_library_roots):
            case Err() as err:
                return err
            case Ok(snap):
                return Ok(PushReport(steps=tuple(steps), snapshot=snap))

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def diff_workspace(
    paths: TenantPaths,
    *,
    target: str = "all",
    instance: str = DEFAULT_INSTANCE,
    profile: str | None = None,
    on_step: Callable[[str], None] | None = None,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[DiffReport, StreamWeaverError]:
    """Computes live drift across configured AIO services and Nuvio profile documents in parallel."""
    match _parse_targets(target, implicit_all_nuvio=True):
        case Err() as t_err:
            return t_err
        case Ok((svc_targets, nuvio_docs)):
            pass

    match _load_workspace(paths, instance=instance, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    match _scope_bindings_for_profile(ws, profile):
        case Err() as sc_err:
            return sc_err
        case Ok((scoped_bindings, scoped_cps)):
            pass

    cleaned = target.strip().lower()

    def _run(client: HttpClientProtocol) -> Result[DiffReport, StreamWeaverError]:
        services = build_config_services(client)

        def _diff_one_svc(
            svc_id: ServiceId,
        ) -> Result[tuple[ConfigDiffOutcome | None, str | None], StreamWeaverError]:
            binding = scoped_bindings.get(svc_id)
            if binding is None or not binding.configured_for_pull():
                return Ok(
                    (
                        None,
                        f"[{svc_id}] Skipping diff: missing local config or credentials in .env",
                    )
                )
            cfg_path = _service_doc_path(paths.docs_dir, svc_id, binding.name)
            if not cfg_path.is_file():
                return Ok(
                    (
                        None,
                        f"[{svc_id}] Skipping diff: missing local config or credentials in .env",
                    )
                )
            match read_json_file(cfg_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(local_scrubbed):
                    pass
            if svc_id == "aiomanager":
                local_scrubbed = apply_managed_accounts(
                    local_scrubbed, ws.manifest.name, ws.profiles
                ).state
                for acc in local_scrubbed.get("accounts", []):
                    if isinstance(acc, dict):
                        acc.pop("nuvioProfileName", None)
            if svc_id == "aiometadata" and ws.compiled:
                match derive_instance_catalogs(binding.name, local_scrubbed, ws.compiled):
                    case Err(d_err):
                        return Err(StreamWeaverError(str(d_err)))
                    case Ok(plan):
                        local_scrubbed = {**local_scrubbed, "catalogs": plan.catalogs}
            match services[svc_id].pull_remote(binding):
                case Err(net_err):
                    return Err(StreamWeaverError(str(net_err)))
                case Ok(remote_norm):
                    remote_scrubbed, _ = harvest_and_scrub(svc_id, remote_norm, ws.env)
                    return Ok(
                        (compute_config_diff(local_scrubbed, remote_scrubbed, target=svc_id), None)
                    )

        def _diff_nuvio() -> Result[tuple[list[ConfigDiffOutcome], list[str]], StreamWeaverError]:
            if not nuvio_docs:
                return Ok(([], []))
            if not any(
                (lb := cp.resolved.logins.get("nuvio")) is not None and lb.configured()
                for cp in scoped_cps
            ):
                if cleaned != "all":
                    match nuvio_targets(
                        tenant_name=ws.manifest.name,
                        scrubbed_aiomanager=_load_optional_json(paths.doc("aiomanager")),
                        env=ws.env,
                        compiled_profiles=scoped_cps,
                    ):
                        case Err(t_err):
                            return Err(t_err)
                        case Ok():
                            return Ok(([], []))
                return Ok(([], []))

            match nuvio_targets(
                tenant_name=ws.manifest.name,
                scrubbed_aiomanager=_load_optional_json(paths.doc("aiomanager")),
                env=ws.env,
                compiled_profiles=scoped_cps,
            ):
                case Err(t_err):
                    if cleaned != "all":
                        return Err(t_err)
                    return Ok(([], [f"[nuvio] Skipped ({t_err})"]))
                case Ok(targets):
                    if not targets:
                        return Ok(([], []))
            match pull_nuvio(targets, nuvio_docs, dict(ws.env), client, ws.library):
                case Err(n_err):
                    if cleaned != "all":
                        return Err(n_err)
                    return Ok(([], [f"[nuvio] Skipped ({n_err})"]))
                case Ok((pulled, _)):
                    nuv_out: list[ConfigDiffOutcome] = []
                    by_name = {cp.name: cp for cp in scoped_cps}
                    for p in pulled:
                        local = local_nuvio_docs(by_name[p.name])
                        for kind in nuvio_docs:
                            if kind not in p.remote or kind not in local:
                                continue
                            lbl = f"nuvio_{kind}:{p.name}" if len(pulled) > 1 else f"nuvio_{kind}"
                            l_doc = (
                                {"collections": local[kind]}
                                if kind == "collections"
                                else dict(local[kind])
                            )
                            r_doc = (
                                {"collections": p.remote[kind]}
                                if kind == "collections"
                                else dict(p.remote[kind])
                            )
                            l_doc.pop("version", None)
                            r_doc.pop("version", None)
                            nuv_out.append(compute_config_diff(l_doc, r_doc, target=lbl))
                    return Ok((nuv_out, []))

        max_workers = max(1, len(svc_targets) + (1 if nuvio_docs else 0))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            svc_futs = {sid: pool.submit(_diff_one_svc, sid) for sid in svc_targets}
            nuvio_fut = pool.submit(_diff_nuvio) if nuvio_docs else None
            svc_results = {sid: svc_futs[sid].result() for sid in svc_targets}
            nuvio_res = nuvio_fut.result() if nuvio_fut is not None else Ok(([], []))

        outcomes: list[ConfigDiffOutcome] = []
        skipped: list[str] = []
        for sid in svc_targets:
            match svc_results[sid]:
                case Err() as s_err:
                    return s_err
                case Ok((out_item, skip_msg)):
                    if skip_msg:
                        skipped.append(skip_msg)
                        if on_step is not None:
                            on_step(skip_msg)
                    if out_item is not None:
                        outcomes.append(out_item)

        match nuvio_res:
            case Err() as n_err:
                return n_err
            case Ok((nuv_outcomes, nuv_skips)):
                for ns in nuv_skips:
                    skipped.append(ns)
                    if on_step is not None:
                        on_step(ns)
                outcomes.extend(nuv_outcomes)

        return Ok(DiffReport(outcomes=tuple(outcomes), skipped=tuple(skipped)))

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def pull_workspace(
    paths: TenantPaths,
    *,
    target: str = "all",
    instance: str = DEFAULT_INSTANCE,
    profile: str | None = None,
    on_step: Callable[[str], None] | None = None,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[PushReport, StreamWeaverError]:
    """
    Pulls live configurations from remote AIOStreams, AIOMetadata, AIOManager (in parallel), and
    Nuvio profiles, harvests secrets into `.env`, scrubs JSON documents, ejects Nuvio changes into
    `profile.json`, and returns the fresh workspace snapshot.
    """
    match _parse_targets(target, implicit_all_nuvio=True):
        case Err() as t_err:
            return t_err
        case Ok((svc_targets, nuvio_docs)):
            pass

    match _load_workspace(paths, instance=instance, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    match _scope_bindings_for_profile(ws, profile):
        case Err() as sc_err:
            return sc_err
        case Ok((scoped_bindings, scoped_cps)):
            pass

    cleaned = target.strip().lower()

    def _run(client: HttpClientProtocol) -> Result[PushReport, StreamWeaverError]:
        nonlocal ws, scoped_bindings, scoped_cps
        services = build_config_services(client)
        working = dict(ws.env)
        steps: list[str] = []

        def _emit(msg: str) -> None:
            steps.append(msg)
            if on_step is not None:
                on_step(msg)

        configured_svcs: list[ServiceId] = []
        for svc_id in svc_targets:
            binding = scoped_bindings.get(svc_id)
            if binding is None or not binding.configured_for_pull():
                _emit(f"[{svc_id}] Skipped pull (not configured in .env)")
            else:
                configured_svcs.append(svc_id)

        pulled_raw: dict[ServiceId, Result[JsonDict, Any]] = {}
        if len(configured_svcs) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(configured_svcs)) as pool:
                futs = {
                    sid: pool.submit(services[sid].pull_remote, scoped_bindings[sid])
                    for sid in configured_svcs
                }
                pulled_raw = {sid: futs[sid].result() for sid in configured_svcs}
        elif len(configured_svcs) == 1:
            only = configured_svcs[0]
            pulled_raw = {only: services[only].pull_remote(scoped_bindings[only])}

        for svc_id in configured_svcs:
            binding = scoped_bindings[svc_id]
            match pulled_raw[svc_id]:
                case Err(net_err):
                    return Err(StreamWeaverError(str(net_err)))
                case Ok(remote_cfg):
                    pass
            if svc_id == "aiomanager":
                match dehydrate_aiomanager(remote_cfg):
                    case Err(d_err):
                        return Err(StreamWeaverError(str(d_err)))
                    case Ok(remote_cfg):
                        pass
            scrubbed_cfg, working = harvest_and_scrub(svc_id, remote_cfg, working)
            cfg_path = _service_doc_path(paths.docs_dir, svc_id, binding.name)
            match write_json_file(cfg_path, scrubbed_cfg):
                case Err(w_err):
                    return Err(w_err)
                case Ok():
                    pass
            _ = save_env_updates(paths.env_file, working)
            _ = scaffold_env_keys(paths.env_example_file, list(working))
            _emit(f"[{svc_id}] Pulled & scrubbed UUID {binding.uuid}")

        has_nuvio_pull = bool(
            nuvio_docs
            and any(
                (lb := cp.resolved.logins.get("nuvio")) is not None and lb.configured()
                for cp in scoped_cps
            )
        )
        if "aiomanager" in configured_svcs or has_nuvio_pull:
            match _refresh_manifests_for_workspace(
                ws, working, client, extra_library_roots, instance=instance
            ):
                case Err() as err:
                    return err
                case Ok((ws, working)):
                    pass

            match _scope_bindings_for_profile(ws, profile):
                case Err() as sc_err2:
                    return sc_err2
                case Ok((scoped_bindings, scoped_cps)):
                    pass

        if nuvio_docs:
            if any(
                (lb := cp.resolved.logins.get("nuvio")) is not None and lb.configured()
                for cp in scoped_cps
            ):
                match nuvio_targets(
                    tenant_name=ws.manifest.name,
                    scrubbed_aiomanager=_load_optional_json(paths.doc("aiomanager")),
                    env=working,
                    compiled_profiles=scoped_cps,
                ):
                    case Err(t_err):
                        if cleaned != "all":
                            return Err(t_err)
                        _emit(f"[nuvio] Skipped ({t_err})")
                    case Ok(targets) if targets:
                        match pull_nuvio(targets, nuvio_docs, working, client, ws.library):
                            case Err(n_err):
                                if cleaned != "all":
                                    return Err(n_err)
                                _emit(f"[nuvio] Skipped ({n_err})")
                            case Ok((pulled_list, refreshed)):
                                if refreshed:
                                    working.update(refreshed)
                                    _ = save_env_updates(paths.env_file, working)
                                for pulled in pulled_list:
                                    if pulled.changed_docs:
                                        pf = profile_file(paths, pulled.name)
                                        _ = write_json_file(pf, profile_doc_after_pull(pulled))
                                        _emit(
                                            f"[nuvio:{pulled.name}] Pulled changes in {', '.join(pulled.changed_docs)}"
                                        )
                                    else:
                                        _emit(f"[nuvio:{pulled.name}] Already in sync")
                    case _:
                        pass
            elif cleaned != "all":
                match nuvio_targets(
                    tenant_name=ws.manifest.name,
                    scrubbed_aiomanager=_load_optional_json(paths.doc("aiomanager")),
                    env=working,
                    compiled_profiles=scoped_cps,
                ):
                    case Err(t_err):
                        return Err(t_err)
                    case Ok():
                        _emit("[nuvio] No profile has a Nuvio client; nothing to pull")

        match _load_workspace(
            paths, instance=instance, env_override=working, extra_library_roots=extra_library_roots
        ):
            case Err() as err:
                return err
            case Ok(ws):
                pass
        _ = _sync_derived_files(ws)
        match inspect_tenant_state(paths, extra_library_roots=extra_library_roots):
            case Err() as err:
                return err
            case Ok(snap):
                return Ok(PushReport(steps=tuple(steps), snapshot=snap))

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def build_custom_folder_sources(
    paths: TenantPaths,
    spec: Mapping[str, Any],
    *,
    pending_env: Mapping[str, str] | None = None,
    http_client: HttpClientProtocol | None = None,
) -> Result[JsonDict, StreamWeaverError]:
    """
    Resolves a Studio DIY folder creation request (`tmdb` filter or `mdblist` URL/ID) into
    `{ "title": <folder_title>, "sources": [<symbolic_source_with_inline_catalog>, ...] }`.
    """
    kind = str(spec.get("kind") or "").strip().lower()
    title_hint = str(spec.get("title") or "").strip()

    if kind == "tmdb":
        row_title = title_hint or "Custom Picks"
        match build_tmdb_discover_sources(
            row_title,
            media=str(spec.get("media") or "both"),
            genre=str(spec.get("genre") or ""),
            language=str(spec.get("language") or ""),
            decade=str(spec.get("decade") or "any"),
            sort=str(spec.get("sort") or "popularity.desc"),
        ):
            case Err(t_err):
                return Err(StreamWeaverError(str(t_err)))
            case Ok(sources):
                return Ok({"title": row_title, "sources": sources})

    if kind == "mdblist":
        raw_input = str(spec.get("input") or "").strip()
        media_type = str(spec.get("media") or "movie").strip().lower()
        match parse_mdblist_reference(raw_input):
            case Err(p_err):
                return Err(StreamWeaverError(str(p_err)))
            case Ok(ref):
                pass

        if ref.list_id is not None:
            resolved_title = title_hint or f"MDBList #{ref.list_id}"
            match build_mdblist_source(ref.list_id, title=resolved_title, media_type=media_type):
                case Err(b_err):
                    return Err(StreamWeaverError(str(b_err)))
                case Ok(src):
                    return Ok({"title": resolved_title, "sources": [src]})

        env = load_env_file(paths.env_file)
        if pending_env:
            env.update({str(k): str(v).strip() for k, v in pending_env.items() if str(v).strip()})
        api_key = env.get("SECRET_MDBLIST_API_KEY", "").strip()
        if not api_key:
            return Err(
                StreamWeaverError(
                    "Set SECRET_MDBLIST_API_KEY in 'Keys & Sign-In' first to resolve MDBList URLs "
                    "(or enter the numeric MDBList ID directly, e.g. 88434)."
                )
            )

        url = f"https://api.mdblist.com/lists/{ref.username}/{ref.slug}?apikey={api_key}"

        def _fetch(client: HttpClientProtocol) -> Result[JsonDict, StreamWeaverError]:
            match client.request_json("GET", url, timeout=15.0):
                case Err(net_err):
                    return Err(StreamWeaverError(f"MDBList lookup failed: {net_err}"))
                case Ok(resp):
                    if resp.status_code >= 400:
                        return Err(
                            StreamWeaverError(
                                f"MDBList returned HTTP {resp.status_code} for {ref.username}/{ref.slug}"
                            )
                        )
                    data = resp.data
            items = data if isinstance(data, list) else ([data] if isinstance(data, Mapping) else [])
            valid_items = [it for it in items if isinstance(it, Mapping) and str(it.get("id") or "").isdigit()]
            if not valid_items:
                return Err(StreamWeaverError(f"MDBList list `{ref.username}/{ref.slug}` returned no list ID"))

            first_name = str(valid_items[0].get("name") or ref.slug or "MDBList").strip()
            folder_title = title_hint or first_name
            out_sources: list[JsonDict] = []
            for it in valid_items:
                lid = str(it["id"])
                raw_mt = str(it.get("mediatype") or "movie").lower()
                st_type = "series" if raw_mt in ("show", "series", "tv", "shows") else "movie"
                row_lbl = (
                    f"{folder_title} — {'Series' if st_type == 'series' else 'Movies'}"
                    if len(valid_items) > 1
                    else folder_title
                )
                match build_mdblist_source(lid, title=row_lbl, media_type=st_type):
                    case Err(b_err):
                        return Err(StreamWeaverError(str(b_err)))
                    case Ok(s_obj):
                        out_sources.append(s_obj)
            return Ok({"title": folder_title, "sources": out_sources})

        if http_client is not None:
            return _fetch(http_client)
        with HttpxClient() as live_client:
            return _fetch(live_client)

    return Err(StreamWeaverError(f"Unsupported custom row builder kind `{kind}`"))


def export_tenant_zip_bytes(paths: TenantPaths, *, include_env: bool = False) -> bytes:
    """Packages a tenant directory as a portable GitOps `.zip` archive (excluding `.env` unless requested)."""
    buf = io.BytesIO()
    prefix = paths.root.name or "tenant"
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(paths.root.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(paths.root)
            if ".cache" in rel.parts or "__pycache__" in rel.parts:
                continue
            if rel.name == ".env" and not include_env:
                continue
            zf.write(file_path, arcname=f"{prefix}/{rel.as_posix()}")
    return buf.getvalue()
