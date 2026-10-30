"""Client sync providers (`NuvioClientSync`, `StremioClientSync`) implementing `ClientSyncProtocol`."""

import copy
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.crypto import decrypt_field
from stream_weaver.core.errors import NetworkError, StreamWeaverError
from stream_weaver.core.nuvio_badges import (
    STREAM_WEAVER_ORIGIN_CLIENT_ID,
    VALID_NUVIO_BADGE_PLATFORMS,
    build_platform_stream_badge_settings,
    compile_nuvio_badge_import,
    compile_nuvio_badge_rules_json,
    decompile_nuvio_badges,
    merge_extra_into_badge_import,
    merge_profile_settings_blob_badges,
    normalize_nuvio_badges_doc,
)
from stream_weaver.core.nuvio_meta import (
    VALID_NUVIO_META_PLATFORMS,
    build_platform_meta_features,
    build_provider_credentials,
    decompile_nuvio_meta,
    merge_profile_settings_blob_meta,
    merge_provider_credential_rows,
    normalize_nuvio_meta_doc,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import EnvMap, JsonDict
from stream_weaver.effects.http import HttpClientProtocol


def compile_nuvio_collections(
    raw_collections: list[Any],
    addons: list[Any],
) -> list[JsonDict]:
    """
    Pure function: compiles declarative Nuvio Collection definitions against the account's
    live addon manifests, expanding dynamic BingeCat `aicat_list_for_you_*` catalogs and
    populating both `sources` and `catalogSources` so Nuvio absorbs them from loose Home rows.
    """
    bingecat_addon_id: str | None = None
    bingecat_catalogs: list[dict[str, Any]] = []
    for addon in addons:
        if not isinstance(addon, dict):
            continue
        mf = addon.get("manifest")
        if not isinstance(mf, dict):
            continue
        mid = str(mf.get("id") or "")
        if mid.startswith("com.aicat."):
            bingecat_addon_id = mid
            cats = mf.get("catalogs")
            if isinstance(cats, list):
                bingecat_catalogs = [c for c in cats if isinstance(c, dict)]
            break

    compiled: list[JsonDict] = []
    for col in raw_collections:
        if not isinstance(col, dict):
            continue
        col_copy: JsonDict = copy.deepcopy(col)
        col_copy.setdefault("pinToTop", True)
        col_copy.setdefault("focusGlowEnabled", True)
        col_copy.setdefault("viewMode", "TABBED_GRID")
        col_copy.setdefault("showAllTab", True)

        raw_folders = col_copy.get("folders")
        folders_out: list[JsonDict] = []
        if isinstance(raw_folders, list):
            for fld in raw_folders:
                if not isinstance(fld, dict):
                    continue
                fld_copy: JsonDict = copy.deepcopy(fld)
                dynamic_lists = bool(fld_copy.pop("dynamicBingecatLists", False))
                fld_copy.setdefault("tileShape", "LANDSCAPE")
                fld_copy.setdefault("focusGifEnabled", bool(fld_copy.get("focusGifUrl")))
                fld_copy.setdefault("hideTitle", False)

                raw_sources = fld_copy.get("sources")
                sources_in: list[dict[str, Any]] = (
                    [s for s in raw_sources if isinstance(s, dict)]
                    if isinstance(raw_sources, list)
                    else []
                )

                if dynamic_lists and bingecat_addon_id:
                    for cat in bingecat_catalogs:
                        cid = str(cat.get("id") or "")
                        if not cid.startswith("aicat_list_for_you_"):
                            continue
                        if cat.get("showInHome", True) is False:
                            continue
                        ctype = str(cat.get("type") or "movie")
                        cname = str(cat.get("name") or cid)
                        type_label = "Movies" if ctype == "movie" else "Series"
                        sources_in.append(
                            {
                                "provider": "addon",
                                "addonId": bingecat_addon_id,
                                "catalogId": cid,
                                "type": ctype,
                                "genre": "None",
                                "title": f"{cname} ({type_label})",
                            }
                        )

                norm_sources: list[JsonDict] = []
                cat_sources: list[JsonDict] = []
                for src in sources_in:
                    addon_id = str(src.get("addonId") or "")
                    cat_id = str(src.get("catalogId") or "")
                    ctype = str(src.get("type") or "movie")
                    genre = str(src.get("genre") or "None")
                    title = str(src.get("title") or cat_id)
                    if not addon_id or not cat_id:
                        continue
                    norm_sources.append(
                        {
                            "provider": str(src.get("provider") or "addon"),
                            "addonId": addon_id,
                            "catalogId": cat_id,
                            "type": ctype,
                            "genre": genre,
                            "title": title,
                        }
                    )
                    cat_sources.append(
                        {
                            "addonId": addon_id,
                            "catalogId": cat_id,
                            "type": ctype,
                            "genre": genre,
                        }
                    )

                fld_copy["sources"] = norm_sources
                fld_copy["catalogSources"] = cat_sources
                folders_out.append(fld_copy)

        col_copy["folders"] = folders_out
        compiled.append(col_copy)
    return compiled


def decompile_nuvio_collections(remote_payload: Any) -> list[JsonDict]:
    """
    Pure function: unwraps Nuvio's `sync_pull_collections` response and strips runtime-compiled
    fields (`catalogSources`) so the collections match the declarative `configs/nuvio_collections.json` schema.
    """
    raw_list: list[Any] = []
    if isinstance(remote_payload, list):
        if (
            len(remote_payload) == 1
            and isinstance(remote_payload[0], Mapping)
            and any(
                k in remote_payload[0]
                for k in ("collections", "collections_json", "p_collections_json")
            )
        ):
            first = remote_payload[0]
            inner = (
                first.get("collections")
                or first.get("collections_json")
                or first.get("p_collections_json")
            )
            if isinstance(inner, list):
                raw_list = inner
        else:
            raw_list = remote_payload
    elif isinstance(remote_payload, Mapping):
        inner = (
            remote_payload.get("collections")
            or remote_payload.get("collections_json")
            or remote_payload.get("p_collections_json")
            or remote_payload.get("data")
        )
        if isinstance(inner, list):
            raw_list = inner

    decompiled: list[JsonDict] = []
    for col in raw_list:
        if not isinstance(col, Mapping):
            continue
        col_copy: JsonDict = copy.deepcopy(dict(col))
        raw_folders = col_copy.get("folders")
        if isinstance(raw_folders, list):
            folders_out: list[JsonDict] = []
            for fld in raw_folders:
                if not isinstance(fld, Mapping):
                    continue
                fld_copy: JsonDict = copy.deepcopy(dict(fld))
                fld_copy.pop("catalogSources", None)
                raw_sources = fld_copy.get("sources")
                if isinstance(raw_sources, list):
                    norm_sources: list[JsonDict] = []
                    for src in raw_sources:
                        if not isinstance(src, Mapping):
                            continue
                        addon_id = str(src.get("addonId") or "")
                        cat_id = str(src.get("catalogId") or "")
                        if not addon_id or not cat_id:
                            continue
                        norm_sources.append(
                            {
                                "provider": str(src.get("provider") or "addon"),
                                "addonId": addon_id,
                                "catalogId": cat_id,
                                "type": str(src.get("type") or "movie"),
                                "genre": str(src.get("genre") or "None"),
                                "title": str(src.get("title") or cat_id),
                            }
                        )
                    fld_copy["sources"] = norm_sources
                folders_out.append(fld_copy)
            col_copy["folders"] = folders_out
        decompiled.append(col_copy)
    return decompiled


def build_nuvio_addons_payload(addons: list[Any]) -> list[JsonDict]:
    """Pure function: transforms AIOManager account addons into Nuvio's `p_addons` RPC format."""
    p_addons: list[JsonDict] = []
    for idx, addon in enumerate(addons):
        if not isinstance(addon, dict):
            continue
        url = str(addon.get("transportUrl", "")).rstrip("/")
        if url and not url.endswith("/manifest.json"):
            url = f"{url}/manifest.json"
        raw_mf = addon.get("manifest")
        raw_flags = addon.get("flags")
        manifest: dict[str, Any] = raw_mf if isinstance(raw_mf, dict) else {}
        flags: dict[str, Any] = raw_flags if isinstance(raw_flags, dict) else {}
        p_addons.append(
            {
                "url": url,
                "name": str(manifest.get("name", "")),
                "enabled": flags.get("enabled", True) is not False,
                "sort_order": idx,
            }
        )
    return p_addons


def build_stremio_addons_payload(addons: list[Any]) -> list[JsonDict]:
    """Pure function: transforms AIOManager account addons into Stremio's `AddonCollectionSet` format."""
    stremio_addons: list[JsonDict] = []
    for addon in addons:
        if not isinstance(addon, dict):
            continue
        raw_flags = addon.get("flags")
        flags: dict[str, Any] = raw_flags if isinstance(raw_flags, dict) else {}
        if flags.get("enabled", True) is False:
            continue
        url = str(addon.get("transportUrl", "")).rstrip("/")
        if url and not url.endswith("/manifest.json"):
            url = f"{url}/manifest.json"
        raw_mf = addon.get("manifest")
        manifest: dict[str, Any] = raw_mf if isinstance(raw_mf, dict) else {}
        stremio_addons.append(
            {
                "transportUrl": url,
                "transportName": str(addon.get("transportName", "")),
                "manifest": manifest,
                "flags": flags,
            }
        )
    return stremio_addons


class NuvioClientSync:
    """Pushes and pulls an account's addons and curated collections to/from a linked Nuvio profile."""

    def __init__(self, http_client: HttpClientProtocol) -> None:
        self._http = http_client

    @property
    def platform_id(self) -> str:
        return "nuvio"

    def _authenticate_and_resolve_profile(
        self,
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
    ) -> Result[tuple[dict[str, str], int, dict[str, str]]]:
        """Authenticates against Nuvio Supabase (refreshing JWT on 401) and resolves `profile_idx`."""
        creds = connection.get("credentials")
        if not isinstance(creds, Mapping) or not creds.get("accessToken"):
            return Err(StreamWeaverError("Nuvio connection has no accessToken."))

        nuvio_anon_key = env.get("NUVIO_SUPABASE_ANON_KEY", "")
        if not nuvio_anon_key:
            return Err(
                StreamWeaverError(
                    "NUVIO_SUPABASE_ANON_KEY is not set in .env; skipping direct Nuvio sync."
                )
            )

        access_token = decrypt_field(str(creds["accessToken"]), master_password, salt_b64)
        headers: dict[str, str] = {
            "apikey": nuvio_anon_key,
            "Authorization": f"Bearer {access_token}",
        }
        updated_secrets: dict[str, str] = {}

        prof_res = self._http.request_json(
            "POST",
            "https://api.nuvio.tv/rest/v1/rpc/sync_pull_profiles",
            headers=headers,
            json_body={},
        )
        match prof_res:
            case Err() as err:
                return err
            case Ok(prof_resp):
                p_status = prof_resp.status_code
                profiles = prof_resp.data

        if p_status == 401 and creds.get("refreshToken"):
            refresh_tok = decrypt_field(str(creds["refreshToken"]), master_password, salt_b64)
            refresh_res = self._http.request_json(
                "POST",
                "https://api.nuvio.tv/auth/v1/token?grant_type=refresh_token",
                headers={"apikey": nuvio_anon_key},
                json_body={"refresh_token": refresh_tok},
            )
            match refresh_res:
                case Ok(r_resp) if (
                    r_resp.status_code == 200
                    and isinstance(r_resp.data, dict)
                    and r_resp.data.get("access_token")
                ):
                    access_token = str(r_resp.data["access_token"])
                    updated_secrets["SECRET_NUVIO_ACCESS_TOKEN"] = access_token
                    if r_resp.data.get("refresh_token"):
                        updated_secrets["SECRET_NUVIO_REFRESH_TOKEN"] = str(
                            r_resp.data["refresh_token"]
                        )
                    headers["Authorization"] = f"Bearer {access_token}"
                    retry_prof = self._http.request_json(
                        "POST",
                        "https://api.nuvio.tv/rest/v1/rpc/sync_pull_profiles",
                        headers=headers,
                        json_body={},
                    )
                    if isinstance(retry_prof, Ok):
                        p_status = retry_prof.value.status_code
                        profiles = retry_prof.value.data
                case _:
                    pass

        profile_idx = 1
        target_pid = creds.get("profileId")
        if p_status == 200 and isinstance(profiles, list):
            for p in profiles:
                if isinstance(p, dict) and str(p.get("id")) == str(target_pid):
                    profile_idx = int(p.get("profile_index") or p.get("profileIndex") or 1)
                    break

        return Ok((headers, profile_idx, updated_secrets))

    def pull_collections(
        self,
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
    ) -> Result[tuple[list[JsonDict], dict[str, str]]]:
        """
        Pulls live Home Collections from Nuvio (`POST /rest/v1/rpc/sync_pull_collections`)
        and decompiles them into declarative `configs/nuvio_collections.json` format.
        """
        match self._authenticate_and_resolve_profile(connection, master_password, salt_b64, env):
            case Err() as err:
                return err
            case Ok((headers, profile_idx, updated_secrets)):
                pass

        col_res = self._http.request_json(
            "POST",
            "https://api.nuvio.tv/rest/v1/rpc/sync_pull_collections",
            headers=headers,
            json_body={"p_profile_id": profile_idx},
        )
        match col_res:
            case Err() as err:
                return err
            case Ok(resp):
                if resp.status_code != 200:
                    return Err(
                        NetworkError(
                            message=f"Nuvio collections pull returned HTTP {resp.status_code}",
                            status_code=resp.status_code,
                        )
                    )
                return Ok((decompile_nuvio_collections(resp.data), updated_secrets))

    def _pull_platform_settings_blob(
        self,
        headers: Mapping[str, str],
        profile_idx: int,
        platform: str,
    ) -> Result[JsonDict | None]:
        res = self._http.request_json(
            "POST",
            "https://api.nuvio.tv/rest/v1/rpc/sync_pull_profile_settings_blob",
            headers=headers,
            json_body={"p_profile_id": profile_idx, "p_platform": platform},
        )
        match res:
            case Err() as err:
                return err
            case Ok(resp):
                if resp.status_code != 200:
                    return Err(
                        NetworkError(
                            message=(
                                f"Nuvio {platform} profile settings pull returned HTTP {resp.status_code}"
                            ),
                            status_code=resp.status_code,
                        )
                    )
                if isinstance(resp.data, list) and resp.data and isinstance(resp.data[0], Mapping):
                    sjson = resp.data[0].get("settings_json")
                    if isinstance(sjson, Mapping):
                        return Ok(dict(sjson))
                return Ok(None)

    def pull_badges(
        self,
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
        *,
        version: str = "1.0.0",
    ) -> Result[tuple[JsonDict, dict[str, str]]]:
        """
        Pulls live `stream_badge_settings` across Nuvio platforms (`tv`, `desktop`, `mobile`)
        and decompiles them into the declarative `configs/nuvio_badges.json` format.
        """
        match self._authenticate_and_resolve_profile(connection, master_password, salt_b64, env):
            case Err() as err:
                return err
            case Ok((headers, profile_idx, updated_secrets)):
                pass

        platform_blobs: dict[str, Mapping[str, Any] | None] = {}
        for plat in VALID_NUVIO_BADGE_PLATFORMS:
            match self._pull_platform_settings_blob(headers, profile_idx, plat):
                case Err() as err:
                    return err
                case Ok(blob):
                    platform_blobs[plat] = blob

        return Ok((decompile_nuvio_badges(platform_blobs, version=version), updated_secrets))

    def _sync_badges_with_headers(
        self,
        raw_badge_doc: Mapping[str, Any],
        headers: Mapping[str, str],
        profile_idx: int,
    ) -> Result[str]:
        match normalize_nuvio_badges_doc(raw_badge_doc):
            case Err() as err:
                return err
            case Ok(norm_doc):
                pass

        platforms = [str(p) for p in norm_doc.get("platforms", [])]
        if not platforms:
            return Ok("")

        compiled_imports: list[JsonDict] = []
        active_filter_count = 0
        for imp in norm_doc.get("imports", []):
            if not isinstance(imp, Mapping):
                continue
            src_url = str(imp.get("sourceUrl") or "")
            is_active = bool(imp.get("isActive", True))
            fetch_res = self._http.request_json("GET", src_url, timeout=15.0)
            match fetch_res:
                case Err() as err:
                    return err
                case Ok(f_resp):
                    if f_resp.status_code != 200:
                        return Err(
                            NetworkError(
                                message=f"Fetching badge pack {src_url} returned HTTP {f_resp.status_code}",
                                status_code=f_resp.status_code,
                                url=src_url,
                            )
                        )
                    if len(f_resp.text) > 2_000_000:
                        return Err(
                            StreamWeaverError(
                                f"Badge pack {src_url} exceeds 2 MB maximum payload size."
                            )
                        )
                    match compile_nuvio_badge_import(src_url, f_resp.data, is_active=is_active):
                        case Err() as err:
                            return err
                        case Ok(compiled_imp):
                            if is_active:
                                raw_extra_groups = norm_doc.get("extraGroups", [])
                                raw_extra_filters = norm_doc.get("extraFilters", [])
                                compiled_imp = merge_extra_into_badge_import(
                                    compiled_imp,
                                    raw_extra_groups if isinstance(raw_extra_groups, list) else [],
                                    raw_extra_filters
                                    if isinstance(raw_extra_filters, list)
                                    else [],
                                )
                                active_filter_count = sum(
                                    1
                                    for flt in compiled_imp.get("filters", [])
                                    if isinstance(flt, Mapping) and flt.get("isEnabled", True)
                                )
                            compiled_imports.append(compiled_imp)

        rules_json = compile_nuvio_badge_rules_json(compiled_imports)
        pushed_platforms: list[str] = []

        for plat in platforms:
            match self._pull_platform_settings_blob(headers, profile_idx, plat):
                case Err() as err:
                    return err
                case Ok(remote_blob):
                    pass

            plat_badge_settings = build_platform_stream_badge_settings(
                norm_doc,
                rules_json,
                platform=plat,
            )
            merged_blob = merge_profile_settings_blob_badges(
                remote_blob,
                plat_badge_settings,
                platform=plat,
            )
            if merged_blob is None:
                continue

            push_res = self._http.request_json(
                "POST",
                "https://api.nuvio.tv/rest/v1/rpc/sync_push_profile_settings_blob",
                headers=headers,
                json_body={
                    "p_profile_id": profile_idx,
                    "p_platform": plat,
                    "p_settings_json": merged_blob,
                    "p_origin_client_id": STREAM_WEAVER_ORIGIN_CLIENT_ID,
                },
            )
            match push_res:
                case Err() as err:
                    return err
                case Ok(p_resp):
                    if p_resp.status_code not in (200, 204):
                        return Err(
                            NetworkError(
                                message=(
                                    f"Nuvio {plat} profile settings push returned HTTP {p_resp.status_code}"
                                ),
                                status_code=p_resp.status_code,
                            )
                        )
                    pushed_platforms.append(plat)

        if not pushed_platforms:
            return Ok("")
        plat_label = ", ".join(pushed_platforms)
        return Ok(f"stream badges ({active_filter_count} filters across {plat_label})")

    def push_badges(
        self,
        raw_badge_doc: Mapping[str, Any],
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
    ) -> Result[tuple[str, dict[str, str]]]:
        """Directly compiles and pushes `configs/nuvio_badges.json` to Nuvio profile settings blobs."""
        match self._authenticate_and_resolve_profile(connection, master_password, salt_b64, env):
            case Err() as err:
                return err
            case Ok((headers, profile_idx, updated_secrets)):
                pass

        match self._sync_badges_with_headers(raw_badge_doc, headers, profile_idx):
            case Err() as err:
                return err
            case Ok(summary):
                msg = (
                    f"Pushed {summary} to Nuvio profile #{profile_idx}"
                    if summary
                    else f"No target Nuvio platforms configured for profile #{profile_idx}"
                )
                return Ok((msg, updated_secrets))

    # ------------------------------------------------------------------
    # Meta / details-page enrichment (MDBList ratings, TMDB, Trakt comments)
    # ------------------------------------------------------------------

    def _pull_provider_credentials(
        self,
        headers: Mapping[str, str],
        profile_idx: int,
    ) -> Result[list[JsonDict]]:
        res = self._http.request_json(
            "POST",
            "https://api.nuvio.tv/rest/v1/rpc/sync_pull_provider_credentials",
            headers=headers,
            json_body={"p_profile_id": profile_idx},
        )
        match res:
            case Err() as err:
                return err
            case Ok(resp):
                if resp.status_code == 404:
                    return Ok([])
                if resp.status_code != 200:
                    return Err(
                        NetworkError(
                            message=f"Nuvio provider credentials pull returned HTTP {resp.status_code}",
                            status_code=resp.status_code,
                        )
                    )
                rows = resp.data if isinstance(resp.data, list) else []
                return Ok([dict(r) for r in rows if isinstance(r, Mapping)])

    def _push_provider_credentials(
        self,
        headers: Mapping[str, str],
        profile_idx: int,
        desired_rows: list[JsonDict],
    ) -> Result[int]:
        """Seeds missing providers then upserts the merged credential set. Returns providers written."""
        if not desired_rows:
            return Ok(0)
        match self._pull_provider_credentials(headers, profile_idx):
            case Err() as err:
                return err
            case Ok(remote_rows):
                pass

        merged_rows, rows_to_seed = merge_provider_credential_rows(remote_rows, desired_rows)
        for rpc_name, rows in (
            ("sync_seed_provider_credentials", rows_to_seed),
            ("sync_push_provider_credentials", merged_rows),
        ):
            if not rows:
                continue
            res = self._http.request_json(
                "POST",
                f"https://api.nuvio.tv/rest/v1/rpc/{rpc_name}",
                headers=headers,
                json_body={
                    "p_profile_id": profile_idx,
                    "p_credentials": rows,
                    "p_origin_client_id": STREAM_WEAVER_ORIGIN_CLIENT_ID,
                },
            )
            match res:
                case Err() as err:
                    return err
                case Ok(resp):
                    if resp.status_code not in (200, 204):
                        return Err(
                            NetworkError(
                                message=f"Nuvio {rpc_name} returned HTTP {resp.status_code}",
                                status_code=resp.status_code,
                            )
                        )
        return Ok(len(desired_rows))

    def _sync_meta_with_headers(
        self,
        raw_meta_doc: Mapping[str, Any],
        headers: Mapping[str, str],
        profile_idx: int,
    ) -> Result[str]:
        match normalize_nuvio_meta_doc(raw_meta_doc):
            case Err() as err:
                return err
            case Ok(norm_doc):
                pass

        platforms = [str(p) for p in norm_doc.get("platforms", [])]
        if not platforms:
            return Ok("")

        pushed_platforms: list[str] = []
        for plat in platforms:
            match self._pull_platform_settings_blob(headers, profile_idx, plat):
                case Err() as err:
                    return err
                case Ok(remote_blob):
                    pass

            plat_features = build_platform_meta_features(norm_doc, platform=plat)
            merged_blob = merge_profile_settings_blob_meta(
                remote_blob, plat_features, platform=plat
            )
            if merged_blob is None:
                continue

            push_res = self._http.request_json(
                "POST",
                "https://api.nuvio.tv/rest/v1/rpc/sync_push_profile_settings_blob",
                headers=headers,
                json_body={
                    "p_profile_id": profile_idx,
                    "p_platform": plat,
                    "p_settings_json": merged_blob,
                    "p_origin_client_id": STREAM_WEAVER_ORIGIN_CLIENT_ID,
                },
            )
            match push_res:
                case Err() as err:
                    return err
                case Ok(p_resp):
                    if p_resp.status_code not in (200, 204):
                        return Err(
                            NetworkError(
                                message=(
                                    f"Nuvio {plat} profile settings push returned HTTP {p_resp.status_code}"
                                ),
                                status_code=p_resp.status_code,
                            )
                        )
                    pushed_platforms.append(plat)

        match self._push_provider_credentials(
            headers, profile_idx, build_provider_credentials(norm_doc)
        ):
            case Err() as err:
                return err
            case Ok(cred_count):
                pass

        if not pushed_platforms and cred_count == 0:
            return Ok("")
        ratings = norm_doc.get("ratings", {})
        active_providers = sum(1 for v in ratings.get("providers", {}).values() if v)
        parts = [
            f"meta enrichment ({active_providers} rating providers"
            + (", TMDB" if norm_doc.get("tmdbEnrichment", {}).get("enabled") else "")
            + (", Trakt comments" if norm_doc.get("traktComments") else "")
            + f" across {', '.join(pushed_platforms) or 'no platforms'})"
        ]
        if cred_count:
            parts.append(f"{cred_count} provider credential(s)")
        return Ok(" and ".join(parts))

    def pull_meta(
        self,
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
        *,
        version: str = "1.0.0",
    ) -> Result[tuple[JsonDict, dict[str, str]]]:
        """
        Pulls live MDBList / TMDB / Trakt-comments / Meta-screen settings across Nuvio platforms
        (plus provider credentials) and decompiles them into `configs/nuvio_meta.json` format.
        """
        match self._authenticate_and_resolve_profile(connection, master_password, salt_b64, env):
            case Err() as err:
                return err
            case Ok((headers, profile_idx, updated_secrets)):
                pass

        platform_blobs: dict[str, Mapping[str, Any] | None] = {}
        for plat in VALID_NUVIO_META_PLATFORMS:
            match self._pull_platform_settings_blob(headers, profile_idx, plat):
                case Err() as err:
                    return err
                case Ok(blob):
                    platform_blobs[plat] = blob

        match self._pull_provider_credentials(headers, profile_idx):
            case Err() as err:
                return err
            case Ok(cred_rows):
                pass

        return Ok(
            (decompile_nuvio_meta(platform_blobs, cred_rows, version=version), updated_secrets)
        )

    def push_meta(
        self,
        raw_meta_doc: Mapping[str, Any],
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
    ) -> Result[tuple[str, dict[str, str]]]:
        """Directly compiles and pushes `configs/nuvio_meta.json` to Nuvio profile settings + credentials."""
        match self._authenticate_and_resolve_profile(connection, master_password, salt_b64, env):
            case Err() as err:
                return err
            case Ok((headers, profile_idx, updated_secrets)):
                pass

        match self._sync_meta_with_headers(raw_meta_doc, headers, profile_idx):
            case Err() as err:
                return err
            case Ok(summary):
                msg = (
                    f"Pushed {summary} to Nuvio profile #{profile_idx}"
                    if summary
                    else f"No target Nuvio platforms configured for profile #{profile_idx}"
                )
                return Ok((msg, updated_secrets))

    def sync_account(
        self,
        account: Mapping[str, Any],
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
    ) -> Result[tuple[str, dict[str, str]]]:
        match self._authenticate_and_resolve_profile(connection, master_password, salt_b64, env):
            case Err() as err:
                return err
            case Ok((headers, profile_idx, updated_secrets)):
                pass

        raw_addons = account.get("addons")
        addons_list = raw_addons if isinstance(raw_addons, list) else []
        p_addons = build_nuvio_addons_payload(addons_list)

        push_res = self._http.request_json(
            "POST",
            "https://api.nuvio.tv/rest/v1/rpc/sync_push_addons",
            headers=headers,
            json_body={
                "p_profile_id": profile_idx,
                "p_addons": p_addons,
                "p_origin_client_id": STREAM_WEAVER_ORIGIN_CLIENT_ID,
            },
        )
        match push_res:
            case Err() as err:
                return err
            case Ok(s_resp):
                if s_resp.status_code not in (200, 204):
                    return Err(
                        NetworkError(
                            message=f"Nuvio push returned HTTP {s_resp.status_code}",
                            status_code=s_resp.status_code,
                        )
                    )

        parts: list[str] = [f"{len(p_addons)} addons"]

        raw_collections = account.get("nuvioCollections")
        if isinstance(raw_collections, list) and raw_collections:
            compiled_cols = compile_nuvio_collections(raw_collections, addons_list)
            col_res = self._http.request_json(
                "POST",
                "https://api.nuvio.tv/rest/v1/rpc/sync_push_collections",
                headers=headers,
                json_body={
                    "p_profile_id": profile_idx,
                    "p_collections_json": compiled_cols,
                    "p_origin_client_id": STREAM_WEAVER_ORIGIN_CLIENT_ID,
                },
            )
            match col_res:
                case Err() as err:
                    return err
                case Ok(c_resp):
                    if c_resp.status_code not in (200, 204):
                        return Err(
                            NetworkError(
                                message=f"Nuvio collections push returned HTTP {c_resp.status_code}",
                                status_code=c_resp.status_code,
                            )
                        )
                    total_folders = sum(
                        len(c.get("folders", []))
                        for c in compiled_cols
                        if isinstance(c.get("folders"), list)
                    )
                    parts.append(f"{len(compiled_cols)} collections ({total_folders} folders)")

        raw_badges = account.get("nuvioBadges")
        if isinstance(raw_badges, Mapping) and raw_badges:
            match self._sync_badges_with_headers(raw_badges, headers, profile_idx):
                case Err() as err:
                    return err
                case Ok(badge_summary):
                    if badge_summary:
                        parts.append(badge_summary)

        raw_meta = account.get("nuvioMeta")
        if isinstance(raw_meta, Mapping) and raw_meta:
            match self._sync_meta_with_headers(raw_meta, headers, profile_idx):
                case Err() as err:
                    return err
                case Ok(meta_summary):
                    if meta_summary:
                        parts.append(meta_summary)

        if len(parts) == 1:
            joined = f"{parts[0]} directly"
        elif len(parts) == 2:
            joined = f"{parts[0]} and {parts[1]}"
        else:
            joined = f"{', '.join(parts[:-1])}, and {parts[-1]}"

        return Ok((f"Pushed {joined} to Nuvio profile #{profile_idx}", updated_secrets))


class StremioClientSync:
    """Pushes an account's enabled addon list directly to a linked Stremio account."""

    def __init__(self, http_client: HttpClientProtocol) -> None:
        self._http = http_client

    @property
    def platform_id(self) -> str:
        return "stremio"

    def sync_account(
        self,
        account: Mapping[str, Any],
        connection: Mapping[str, Any],
        master_password: str,
        salt_b64: str,
        env: EnvMap,
    ) -> Result[tuple[str, dict[str, str]]]:
        _ = env
        creds = connection.get("credentials")
        if not isinstance(creds, Mapping) or not creds.get("authKey") or not salt_b64:
            return Err(StreamWeaverError("Stremio connection is missing authKey or salt."))

        auth_key = decrypt_field(str(creds["authKey"]), master_password, salt_b64)
        raw_addons = account.get("addons")
        addons_list = raw_addons if isinstance(raw_addons, list) else []
        stremio_addons = build_stremio_addons_payload(addons_list)

        res = self._http.request_json(
            "POST",
            "https://api.strem.io/api/addonCollectionSet",
            json_body={
                "type": "AddonCollectionSet",
                "authKey": auth_key,
                "addons": stremio_addons,
            },
        )
        match res:
            case Err() as err:
                return err
            case Ok(s_resp):
                if (
                    s_resp.status_code == 200
                    and isinstance(s_resp.data, dict)
                    and isinstance(s_resp.data.get("result"), dict)
                    and s_resp.data["result"].get("success")
                ):
                    return Ok(
                        (
                            f"Pushed {len(stremio_addons)} enabled addons directly to Stremio account",
                            {},
                        )
                    )
                return Err(
                    NetworkError(
                        message=f"Stremio sync returned HTTP {s_resp.status_code}",
                        status_code=s_resp.status_code,
                    )
                )
