"""
UI-agnostic workspace engine — powers `sw studio` (both local GitOps mode and stateless web mode)
and programmatic integrations without any `typer` or `rich` terminal coupling.
"""

from __future__ import annotations

import copy
import io
import zipfile
from collections.abc import Mapping, Sequence
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
from stream_weaver.core.aiomanager_accounts import ResolvedProfile, apply_managed_accounts
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
    required_aiometadata_secrets,
    required_aiostreams_secrets,
    sync_aiomanager_cached_debrid_catalog,
    sync_watchlist_folder_recipe,
)
from stream_weaver.core.architecture import compile_architecture_bundle
from stream_weaver.core.coercions import compute_config_diff, dehydrate_aiomanager
from stream_weaver.core.compile import CompiledProfile, compile_profile, derive_instance_catalogs
from stream_weaver.core.composition import symbolic_source
from stream_weaver.core.custom_builder import (
    build_mdblist_source,
    build_tmdb_discover_sources,
    custom_builder_metadata,
    parse_mdblist_reference,
)
from stream_weaver.core.errors import StreamWeaverError
from stream_weaver.core.instances import DEFAULT_INSTANCE, InstanceBinding, resolve_instance_binding
from stream_weaver.core.library import Library, RecipeUiSummary, describe_recipe
from stream_weaver.core.logins import LoginBinding, login_env_key, resolve_login_binding
from stream_weaver.core.profiles import (
    Appearance,
    NuvioClientSpec,
    Profile,
    StremioClientSpec,
    parse_profile,
    profile_file,
    profile_to_doc,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import SECRET_PLACEHOLDER_RE, harvest_and_scrub, inject_secrets
from stream_weaver.core.template import ParamSchema
from stream_weaver.core.tenant import TenantManifest, TenantPaths, parse_tenant_manifest
from stream_weaver.core.types import ALL_SERVICES, ConfigDiffOutcome, JsonDict, ServiceId
from stream_weaver.effects.http import HttpClientProtocol, HttpxClient
from stream_weaver.effects.storage import (
    load_env_file,
    read_json_file,
    save_env_updates,
    scaffold_env_keys,
    write_json_file,
    write_text_file,
)
from stream_weaver.providers.services import build_config_services, refresh_addon_manifests
from stream_weaver.providers.services.nuvio_session import (
    NuvioSessionPool,
    password_grant,
    resolve_nuvio_anon_key,
)


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
        "SECRET_MDBLIST_API_KEY",
        "MDBList API Key (8-Source Ratings)",
        "metadata",
        signup_url="https://mdblist.com/preferences",
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


def _load_optional_json(path: Path) -> JsonDict:
    if not path.is_file():
        return {}
    match read_json_file(path):
        case Ok(doc):
            return doc
        case Err():
            return {}


def _load_workspace(
    paths: TenantPaths,
    *,
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
        match resolve_instance_binding(manifest, env, svc, DEFAULT_INSTANCE):
            case Ok(b):
                bindings[svc] = b
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
        meta_path = ws.paths.docs_dir / (
            "aiometadata.json" if inst_name == DEFAULT_INSTANCE else f"aiometadata.{inst_name}.json"
        )
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
    debrid_id = active_debrid_provider(streams_doc)
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

    # Compute exact required secrets across enabled packs, derived metadata catalogs, and active collections
    required_keys: set[str] = {
        "AIOSTREAMS_URL",
        "AIOSTREAMS_PASSWORD",
        "AIOMETADATA_URL",
        "AIOMETADATA_PASSWORD",
    }
    required_keys.update(required_aiostreams_secrets(streams_doc))
    effective_meta_doc: Mapping[str, Any] = meta_service_doc
    if ws.compiled and meta_service_doc:
        match derive_instance_catalogs(DEFAULT_INSTANCE, meta_service_doc, ws.compiled):
            case Ok(plan):
                effective_meta_doc = {**meta_service_doc, "catalogs": plan.catalogs}
            case Err():
                pass
    required_keys.update(required_aiometadata_secrets(effective_meta_doc))

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
                        "rows": [str(s.get("title") or s.get("catalogId") or "") for s in (f.get("sources") or []) if isinstance(s, Mapping)],
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

    env_items: list[JsonDict] = []
    for spec in KNOWN_ENV_KEYS:
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
            },
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
            "streamPreferences": {
                "maxResolution": stream_prefs.max_resolution,
                "cachedOnly": stream_prefs.cached_only,
                "audioLanguages": list(stream_prefs.audio_languages),
                "resolutionOptions": list(RESOLUTION_CAPS),
                "audioLanguageOptions": list(STREAM_AUDIO_LANGUAGES),
            },
            "dualSubtitles": {
                "pairs": [[m, t] for m, t in dual_pairs],
                "languages": [
                    {"code": s.code3, "short": s.code2, "label": s.label}
                    for s in STRELINGO_LANGUAGES
                ],
            },
            "customBuilderMeta": custom_builder_metadata(),
            "availableCatalogs": _discover_available_catalogs(meta_service_doc, ws.compiled),
            "libraryCollections": lib_collections,
            "metaParams": _serialize_param_schema(meta_recipe.params) if meta_recipe else [],
            "settingsParams": _serialize_param_schema(settings_recipe.params) if settings_recipe else [],
            "profiles": profiles_out,
            "env": env_items,
        }
    )


def apply_studio_mutation(
    paths: TenantPaths,
    mutation: Mapping[str, Any],
    *,
    extra_library_roots: Sequence[Path] = (),
) -> Result[JsonDict, StreamWeaverError]:
    """
    Applies a declarative Studio edit (Debrid provider, AIOStreams packs, profile collections /
    settings / meta / badges / client toggles, and `.env` updates), re-derives AIOMetadata catalogs
    and AIOManager accounts, updates architecture diagrams, and returns the fresh workspace snapshot.
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

    # 2. Update Debrid provider, AIOStreams packs, stream preferences & dual-subtitle pairs
    debrid_val = mutation.get("debrid") if isinstance(mutation.get("debrid"), str) else None
    packs_val = mutation.get("enabledPacks") if isinstance(mutation.get("enabledPacks"), list) else None
    stream_prefs_mut = mutation.get("streamPreferences") if isinstance(mutation.get("streamPreferences"), Mapping) else None
    dual_pairs_mut = mutation.get("dualSubtitlePairs") if isinstance(mutation.get("dualSubtitlePairs"), list) else None
    if (
        debrid_val is not None
        or packs_val is not None
        or stream_prefs_mut is not None
        or dual_pairs_mut is not None
    ):
        streams_path = paths.doc("aiostreams")
        if streams_path.is_file():
            match read_json_file(streams_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(streams_doc):
                    pass
            if debrid_val is not None:
                match apply_debrid_provider(streams_doc, debrid_val):
                    case Err(d_err):
                        return Err(StreamWeaverError(str(d_err)))
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
            match write_json_file(streams_path, streams_doc):
                case Err(w_err):
                    return Err(w_err)
                case Ok():
                    pass
            cur_debrid = active_debrid_provider(streams_doc)
            pack_states = {st.spec.id: st.enabled for st in inspect_aiostreams_packs(streams_doc)}
            mgr_path = paths.doc("aiomanager")
            if debrid_val is not None and mgr_path.is_file():
                match read_json_file(mgr_path):
                    case Ok(mgr_doc):
                        _ = write_json_file(mgr_path, sync_aiomanager_cached_debrid_catalog(mgr_doc, cur_debrid))
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

    # 3. Update profile settings / collections / client toggles
    prof_mut = mutation.get("profile")
    if isinstance(prof_mut, Mapping) and isinstance(prof_mut.get("name"), str):
        p_name = prof_mut["name"]
        pf = profile_file(paths, p_name)
        match read_json_file(pf):
            case Err(r_err):
                return Err(r_err)
            case Ok(raw_p_doc):
                pass
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
                    emoji=str(prof_mut["emoji"]) if isinstance(prof_mut.get("emoji"), str) else updated_prof.appearance.emoji,
                    accent_color=str(prof_mut["accentColor"]) if isinstance(prof_mut.get("accentColor"), str) else updated_prof.appearance.accent_color,
                ),
            )
        if "stremioEnabled" in prof_mut:
            if bool(prof_mut["stremioEnabled"]):
                st_login = updated_prof.stremio.login if updated_prof.stremio else (updated_prof.nuvio.login if updated_prof.nuvio else "don")
                updated_prof = replace(updated_prof, stremio=StremioClientSpec(login=st_login))
            else:
                updated_prof = replace(updated_prof, stremio=None)

        if "bingecatEnabled" in prof_mut:
            bc_on = bool(prof_mut["bingecatEnabled"])
            new_extras = [
                replace(a, enabled=bc_on) if a.name == "bingecat" else a
                for a in updated_prof.extra_addons
            ]
            updated_prof = replace(updated_prof, extra_addons=tuple(new_extras))

        if isinstance(prof_mut.get("nuvio"), Mapping) and updated_prof.nuvio is not None:
            n_mut = prof_mut["nuvio"]
            pid = str(n_mut.get("profileId", "")).strip() or None if "profileId" in n_mut else updated_prof.nuvio.profile_id
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

    # 4. Reload, compile, derive AIOMetadata catalogs, canonicalise AIOManager, regenerate diagrams
    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass
    match _sync_derived_files(ws):
        case Err() as err:
            return err
        case Ok():
            pass
    return inspect_tenant_state(paths, extra_library_roots=extra_library_roots)


def login_nuvio_and_list_profiles(
    paths: TenantPaths,
    *,
    login: str = "don",
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
    login_name = login.strip().lower()
    env_prefix = manifest.logins.get("nuvio", {}).get(
        login_name, f"NUVIO_{login_name.upper().replace('-', '_')}"
    )

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
        for svc_id in ("aiostreams", "aiometadata"):
            binding = ws.bindings.get(svc_id)
            cfg_path = paths.doc(svc_id)
            if binding is None or not cfg_path.is_file():
                continue
            if binding.uuid:
                steps.append(f"[{svc_id}] Already provisioned (UUID {binding.uuid})")
                continue
            if not binding.configured_for_push(create=True):
                steps.append(f"[{svc_id}] Skipped: set {binding.url_env_key} and {binding.password_env_key} first")
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
                    return Err(StreamWeaverError(f"[{svc_id}] {sec_err}"))
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
            steps.append(f"[{svc_id}] Provisioned UUID {outcome.uuid} → {outcome.install_url or ''}")

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

    match _load_workspace(ws.paths, env_override=working_env, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(reloaded):
            return Ok((reloaded, working_env))


def push_workspace(
    paths: TenantPaths,
    *,
    target: str = "all",
    profile: str | None = None,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[PushReport, StreamWeaverError]:
    """Pushes the workspace's services and/or Nuvio profiles and returns structured step logs."""
    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    cleaned = target.strip().lower()
    svc_targets: tuple[ServiceId, ...] = (
        ALL_SERVICES
        if cleaned == "all"
        else (() if cleaned in NUVIO_TARGET_ALIASES else (cleaned,))  # type: ignore[assignment]
    )

    def _run(client: HttpClientProtocol) -> Result[PushReport, StreamWeaverError]:
        nonlocal ws
        services = build_config_services(client)
        working = dict(ws.env)
        steps: list[str] = []
        aiomanager_pushed = False

        for svc_id in svc_targets:
            if svc_id == "aiomanager":
                match _refresh_manifests_for_workspace(ws, working, client, extra_library_roots):
                    case Err() as err:
                        return err
                    case Ok((ws, working)):
                        pass
            binding = ws.bindings.get(svc_id)
            if profile:
                chosen_cp = next((cp for cp in ws.compiled if cp.name == profile), None)
                if chosen_cp and svc_id in chosen_cp.resolved.instances:
                    binding = chosen_cp.resolved.instances[svc_id]
            if binding is None or not binding.configured_for_push(create=False):
                steps.append(f"[{svc_id}] Skipped (not configured in .env)")
                continue
            cfg_path = paths.docs_dir / (
                f"{svc_id}.json" if binding.name == DEFAULT_INSTANCE else f"{svc_id}.{binding.name}.json"
            )
            if not cfg_path.is_file():
                continue
            match read_json_file(cfg_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(scrubbed_cfg):
                    pass

            if svc_id == "aiomanager":
                outcome_mgr = apply_managed_accounts(scrubbed_cfg, ws.manifest.name, ws.profiles)
                scrubbed_cfg = outcome_mgr.state
                by_id = {cp.resolved.profile.account_id or "": cp for cp in ws.compiled}
                by_name = {cp.resolved.profile.display_name: cp for cp in ws.compiled}
                for acc in scrubbed_cfg.get("accounts", []):
                    cp = by_id.get(str(acc.get("id"))) or by_name.get(str(acc.get("name")))
                    if cp is not None:
                        acc_payload = cp.account_payload()
                        if "nuvioMeta" in acc_payload:
                            match inject_secrets(acc_payload["nuvioMeta"], working):
                                case Err(m_err):
                                    steps.append(f"[{cp.name}] meta: skipped ({m_err})")
                                    acc_payload.pop("nuvioMeta", None)
                                case Ok():
                                    pass
                        acc.update(acc_payload)

            if svc_id == "aiometadata" and ws.compiled:
                match derive_instance_catalogs(binding.name, scrubbed_cfg, ws.compiled):
                    case Err(d_err):
                        return Err(StreamWeaverError(str(d_err)))
                    case Ok(plan):
                        if plan.changed:
                            scrubbed_cfg = {**scrubbed_cfg, "catalogs": plan.catalogs}
                            _ = write_json_file(cfg_path, scrubbed_cfg)

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
                    return Err(StreamWeaverError(f"[{svc_id}] {sec_err}"))
                case Ok(injected):
                    pass
            match services[svc_id].push_remote(injected, binding, working, create=False):
                case Err(net_err):
                    return Err(StreamWeaverError(str(net_err)))
                case Ok((outcome, updated_cfg, discovered)):
                    pass
            working.update(discovered)
            if discovered:
                _ = save_env_updates(paths.env_file, working)
            if svc_id == "aiomanager":
                aiomanager_pushed = True
                scrubbed_back, working = harvest_and_scrub("aiomanager", updated_cfg, working)
                _ = write_json_file(cfg_path, scrubbed_back)
                _ = save_env_updates(paths.env_file, working)
            steps.append(f"[{svc_id}] Updated UUID {outcome.uuid}")
            steps.extend(outcome.synced_clients)

        nuvio_docs: tuple[NuvioDoc, ...] = NUVIO_TARGET_ALIASES.get(cleaned, ())
        if not nuvio_docs and cleaned == "all" and not aiomanager_pushed:
            scoped_check = [cp for cp in ws.compiled if profile is None or cp.name == profile]
            if any((lb := cp.resolved.logins.get("nuvio")) is not None and lb.configured() for cp in scoped_check):
                nuvio_docs = ALL_NUVIO_DOCS

        if nuvio_docs:
            if "collections" in nuvio_docs and not aiomanager_pushed:
                match _refresh_manifests_for_workspace(ws, working, client, extra_library_roots):
                    case Err() as err:
                        return err
                    case Ok((ws, working)):
                        pass
            scoped_cps = [cp for cp in ws.compiled if profile is None or cp.name == profile]
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
                        steps.extend(msgs)
                        if refreshed:
                            working.update(refreshed)
                            _ = save_env_updates(paths.env_file, working)

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
    profile: str | None = None,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[DiffReport, StreamWeaverError]:
    """Computes live drift across configured AIO services and Nuvio profile documents."""
    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    cleaned = target.strip().lower()
    svc_targets: tuple[ServiceId, ...] = (
        ALL_SERVICES
        if cleaned == "all"
        else (() if cleaned in NUVIO_TARGET_ALIASES else (cleaned,))  # type: ignore[assignment]
    )
    nuvio_docs: tuple[NuvioDoc, ...] = (
        ALL_NUVIO_DOCS if cleaned == "all" else NUVIO_TARGET_ALIASES.get(cleaned, ())
    )

    def _run(client: HttpClientProtocol) -> Result[DiffReport, StreamWeaverError]:
        services = build_config_services(client)
        outcomes: list[ConfigDiffOutcome] = []
        for svc_id in svc_targets:
            binding = ws.bindings.get(svc_id)
            if binding is None or not binding.configured_for_pull():
                continue
            cfg_path = paths.docs_dir / (
                f"{svc_id}.json" if binding.name == DEFAULT_INSTANCE else f"{svc_id}.{binding.name}.json"
            )
            if not cfg_path.is_file():
                continue
            match read_json_file(cfg_path):
                case Err(r_err):
                    return Err(r_err)
                case Ok(local_scrubbed):
                    pass
            if svc_id == "aiomanager":
                local_scrubbed = apply_managed_accounts(local_scrubbed, ws.manifest.name, ws.profiles).state
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
                    outcomes.append(compute_config_diff(local_scrubbed, remote_scrubbed, target=svc_id))

        if nuvio_docs:
            scoped_cps = [cp for cp in ws.compiled if profile is None or cp.name == profile]
            if any((lb := cp.resolved.logins.get("nuvio")) is not None and lb.configured() for cp in scoped_cps):
                match nuvio_targets(
                    tenant_name=ws.manifest.name,
                    scrubbed_aiomanager=_load_optional_json(paths.doc("aiomanager")),
                    env=ws.env,
                    compiled_profiles=scoped_cps,
                ):
                    case Ok(targets) if targets:
                        match pull_nuvio(targets, nuvio_docs, dict(ws.env), client, ws.library):
                            case Ok((pulled, _)):
                                by_name = {cp.name: cp for cp in scoped_cps}
                                for p in pulled:
                                    local = local_nuvio_docs(by_name[p.name])
                                    for kind in nuvio_docs:
                                        if kind not in p.remote or kind not in local:
                                            continue
                                        lbl = f"nuvio_{kind}:{p.name}" if len(pulled) > 1 else f"nuvio_{kind}"
                                        l_doc = {"collections": local[kind]} if kind == "collections" else dict(local[kind])
                                        r_doc = {"collections": p.remote[kind]} if kind == "collections" else dict(p.remote[kind])
                                        l_doc.pop("version", None)
                                        r_doc.pop("version", None)
                                        outcomes.append(compute_config_diff(l_doc, r_doc, target=lbl))
                            case Err(n_err) if cleaned != "all":
                                return Err(n_err)
                            case _:
                                pass
                    case Err(t_err) if cleaned != "all":
                        return Err(t_err)
                    case _:
                        pass

        return Ok(DiffReport(outcomes=tuple(outcomes)))

    if http_client is not None:
        return _run(http_client)
    with HttpxClient() as live_client:
        return _run(live_client)


def pull_workspace(
    paths: TenantPaths,
    *,
    target: str = "all",
    profile: str | None = None,
    extra_library_roots: Sequence[Path] = (),
    http_client: HttpClientProtocol | None = None,
) -> Result[PushReport, StreamWeaverError]:
    """
    Pulls live configurations from remote AIOStreams, AIOMetadata, AIOManager, and Nuvio profiles,
    harvests secrets into `.env`, scrubs JSON documents, ejects Nuvio changes into `profile.json`,
    and returns the fresh workspace snapshot.
    """
    match _load_workspace(paths, extra_library_roots=extra_library_roots):
        case Err() as err:
            return err
        case Ok(ws):
            pass

    cleaned = target.strip().lower()
    svc_targets: tuple[ServiceId, ...] = (
        ALL_SERVICES
        if cleaned == "all"
        else (() if cleaned in NUVIO_TARGET_ALIASES else (cleaned,))  # type: ignore[assignment]
    )
    nuvio_docs: tuple[NuvioDoc, ...] = (
        ALL_NUVIO_DOCS if cleaned == "all" else NUVIO_TARGET_ALIASES.get(cleaned, ())
    )

    def _run(client: HttpClientProtocol) -> Result[PushReport, StreamWeaverError]:
        nonlocal ws
        services = build_config_services(client)
        working = dict(ws.env)
        steps: list[str] = []

        for svc_id in svc_targets:
            binding = ws.bindings.get(svc_id)
            if profile:
                chosen_cp = next((cp for cp in ws.compiled if cp.name == profile), None)
                if chosen_cp and svc_id in chosen_cp.resolved.instances:
                    binding = chosen_cp.resolved.instances[svc_id]
            if binding is None or not binding.configured_for_pull():
                steps.append(f"[{svc_id}] Skipped pull (not configured in .env)")
                continue
            match services[svc_id].pull_remote(binding):
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
            cfg_path = paths.docs_dir / (
                f"{svc_id}.json" if binding.name == DEFAULT_INSTANCE else f"{svc_id}.{binding.name}.json"
            )
            match write_json_file(cfg_path, scrubbed_cfg):
                case Err(w_err):
                    return Err(w_err)
                case Ok():
                    pass
            _ = save_env_updates(paths.env_file, working)
            _ = scaffold_env_keys(paths.env_example_file, list(working))
            steps.append(f"[{svc_id}] Pulled & scrubbed UUID {binding.uuid}")

        match _refresh_manifests_for_workspace(ws, working, client, extra_library_roots):
            case Err() as err:
                return err
            case Ok((ws, working)):
                pass

        if nuvio_docs:
            scoped_cps = [cp for cp in ws.compiled if profile is None or cp.name == profile]
            if any((lb := cp.resolved.logins.get("nuvio")) is not None and lb.configured() for cp in scoped_cps):
                match nuvio_targets(
                    tenant_name=ws.manifest.name,
                    scrubbed_aiomanager=_load_optional_json(paths.doc("aiomanager")),
                    env=working,
                    compiled_profiles=scoped_cps,
                ):
                    case Err(t_err):
                        if cleaned != "all":
                            return Err(t_err)
                        steps.append(f"[nuvio] Skipped ({t_err})")
                    case Ok(targets) if targets:
                        match pull_nuvio(targets, nuvio_docs, working, client, ws.library):
                            case Err(n_err):
                                if cleaned != "all":
                                    return Err(n_err)
                                steps.append(f"[nuvio] Skipped ({n_err})")
                            case Ok((pulled_list, refreshed)):
                                if refreshed:
                                    working.update(refreshed)
                                    _ = save_env_updates(paths.env_file, working)
                                for pulled in pulled_list:
                                    if pulled.changed_docs:
                                        pf = profile_file(paths, pulled.name)
                                        _ = write_json_file(pf, profile_doc_after_pull(pulled))
                                        steps.append(
                                            f"[nuvio:{pulled.name}] Pulled changes in {', '.join(pulled.changed_docs)}"
                                        )
                                    else:
                                        steps.append(f"[nuvio:{pulled.name}] Already in sync")
                    case _:
                        pass

        match _load_workspace(paths, env_override=working, extra_library_roots=extra_library_roots):
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
