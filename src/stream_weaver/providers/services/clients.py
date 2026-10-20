"""Client sync providers (`NuvioClientSync`, `StremioClientSync`) implementing `ClientSyncProtocol`."""

import copy
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.crypto import decrypt_field
from stream_weaver.core.errors import NetworkError, StreamWeaverError
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
            json_body={"p_profile_id": profile_idx, "p_addons": p_addons},
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
                    return Ok(
                        (
                            f"Pushed {len(p_addons)} addons and {len(compiled_cols)} collections ({total_folders} folders) to Nuvio profile #{profile_idx}",
                            updated_secrets,
                        )
                    )

        return Ok(
            (
                f"Pushed {len(p_addons)} addons directly to Nuvio profile #{profile_idx}",
                updated_secrets,
            )
        )


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
