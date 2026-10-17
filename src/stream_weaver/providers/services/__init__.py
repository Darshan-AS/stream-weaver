"""AIO GitOps service providers (AIOStreams, AIOMetadata, AIOManager)."""

from __future__ import annotations

import base64
import concurrent.futures
import copy
import json
import urllib.parse
from collections.abc import Mapping
from typing import Any

from stream_weaver.core.coercions import (
    dehydrate_aiomanager,
    normalize_aiometadata,
    normalize_aiostreams,
    rehydrate_aiomanager,
    sanitize_manifest_home_catalogs,
)
from stream_weaver.core.crypto import (
    decrypt_envelope,
    derive_sync_token,
    encrypt_envelope,
)
from stream_weaver.core.errors import NetworkError, StreamWeaverError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import EnvMap, JsonDict, ServiceId, ServicePushOutcome
from stream_weaver.effects.http import HttpClientProtocol
from stream_weaver.providers.protocols import ClientSyncProtocol, ConfigServiceProtocol
from stream_weaver.providers.services.clients import NuvioClientSync, StremioClientSync


def _http_err(label: str, action: str, status_code: int, data: Any, url: str) -> Err[NetworkError]:
    if isinstance(data, Mapping) and isinstance(data.get("error"), str):
        err_str = data["error"].strip()
        if err_str.startswith(("<!DOCTYPE", "<html")):
            detail = f"Upstream gateway error (Cloudflare/HTML HTTP {status_code})"
        else:
            detail = err_str[:240]
    else:
        detail = json.dumps(data)[:240]
    return Err(
        NetworkError(
            message=f"[{label}] {action} failed (HTTP {status_code}): {detail}",
            status_code=status_code,
            url=url,
        )
    )


class AIOStreamsService:
    """Manages pulling and pushing AIOStreams configurations."""

    def __init__(self, http_client: HttpClientProtocol) -> None:
        self._http = http_client

    @property
    def service_id(self) -> ServiceId:
        return "aiostreams"

    def is_configured_for_pull(self, env: EnvMap) -> bool:
        return bool(
            env.get("AIOSTREAMS_URL")
            and env.get("AIOSTREAMS_UUID")
            and env.get("AIOSTREAMS_PASSWORD")
        )

    def is_configured_for_push(self, env: EnvMap, *, create: bool = False) -> bool:
        return bool(
            env.get("AIOSTREAMS_URL")
            and env.get("AIOSTREAMS_PASSWORD")
            and (create or env.get("AIOSTREAMS_UUID"))
        )

    def pull_remote(self, env: EnvMap) -> Result[JsonDict, StreamWeaverError]:
        base_url = env.get("AIOSTREAMS_URL", "").rstrip("/")
        token = base64.b64encode(
            f"{env.get('AIOSTREAMS_UUID', '')}:{env.get('AIOSTREAMS_PASSWORD', '')}".encode()
        ).decode("ascii")
        url = f"{base_url}/api/v1/user?raw=true"

        match self._http.request_json("GET", url, headers={"Authorization": f"Basic {token}"}):
            case Err() as err:
                return err
            case Ok(resp) if resp.status_code != 200:
                return _http_err("AIOStreams", "Pull", resp.status_code, resp.data, url)
            case Ok(resp):
                return normalize_aiostreams(resp.data)

    def push_remote(
        self,
        config: JsonDict,
        env: EnvMap,
        *,
        create: bool = False,
    ) -> Result[tuple[ServicePushOutcome, JsonDict, dict[str, str]], StreamWeaverError]:
        base_url = env.get("AIOSTREAMS_URL", "").rstrip("/")
        uuid = env.get("AIOSTREAMS_UUID", "")
        password = env.get("AIOSTREAMS_PASSWORD", "")
        addon_password = env.get("AIOSTREAMS_ADDON_PASSWORD", "")

        match normalize_aiostreams(config):
            case Err() as err:
                return err
            case Ok(clean_cfg):
                pass

        payload_cfg = dict(clean_cfg)
        if addon_password:
            payload_cfg["addonPassword"] = addon_password

        url = f"{base_url}/api/v1/user"
        if create:
            match self._http.request_json(
                "POST", url, json_body={"config": payload_cfg, "password": password}
            ):
                case Err() as err:
                    return err
                case Ok(resp) if resp.status_code not in (200, 201) or not isinstance(resp.data, dict):
                    return _http_err("AIOStreams", "Create", resp.status_code, resp.data, url)
                case Ok(resp):
                    raw_data = resp.data.get("data")
                    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
                    new_uuid = str(data.get("uuid", ""))
                    enc_pass = str(data.get("encryptedPassword", ""))
                    manifest_url = f"{base_url}/stremio/{new_uuid}/{enc_pass}/manifest.json"
                    return Ok(
                        (
                            ServicePushOutcome(
                                service="aiostreams",
                                uuid=new_uuid,
                                created=True,
                                install_url=manifest_url,
                            ),
                            clean_cfg,
                            {},
                        )
                    )

        token = base64.b64encode(f"{uuid}:{password}".encode()).decode("ascii")
        match self._http.request_json(
            "PUT",
            url,
            headers={"Authorization": f"Basic {token}"},
            json_body={"config": payload_cfg},
        ):
            case Err() as err:
                return err
            case Ok(resp) if resp.status_code != 200:
                return _http_err("AIOStreams", "Push", resp.status_code, resp.data, url)
            case Ok():
                return Ok(
                    (
                        ServicePushOutcome(service="aiostreams", uuid=uuid, created=False),
                        clean_cfg,
                        {},
                    )
                )


class AIOMetadataService:
    """Manages pulling and pushing AIOMetadata configurations."""

    def __init__(self, http_client: HttpClientProtocol) -> None:
        self._http = http_client

    @property
    def service_id(self) -> ServiceId:
        return "aiometadata"

    def is_configured_for_pull(self, env: EnvMap) -> bool:
        return bool(
            env.get("AIOMETADATA_URL")
            and env.get("AIOMETADATA_UUID")
            and env.get("AIOMETADATA_PASSWORD")
        )

    def is_configured_for_push(self, env: EnvMap, *, create: bool = False) -> bool:
        return bool(
            env.get("AIOMETADATA_URL")
            and env.get("AIOMETADATA_PASSWORD")
            and (create or env.get("AIOMETADATA_UUID"))
        )

    def pull_remote(self, env: EnvMap) -> Result[JsonDict, StreamWeaverError]:
        base_url = env.get("AIOMETADATA_URL", "").rstrip("/")
        uuid = env.get("AIOMETADATA_UUID", "")
        url = f"{base_url}/api/config/load/{uuid}"
        body: JsonDict = {"password": env.get("AIOMETADATA_PASSWORD", "")}
        if addon_pw := env.get("AIOMETADATA_ADDON_PASSWORD", ""):
            body["addonPassword"] = addon_pw

        match self._http.request_json("POST", url, json_body=body):
            case Err() as err:
                return err
            case Ok(resp) if resp.status_code != 200:
                return _http_err("AIOMetadata", "Pull", resp.status_code, resp.data, url)
            case Ok(resp):
                return normalize_aiometadata(resp.data)

    def push_remote(
        self,
        config: JsonDict,
        env: EnvMap,
        *,
        create: bool = False,
    ) -> Result[tuple[ServicePushOutcome, JsonDict, dict[str, str]], StreamWeaverError]:
        base_url = env.get("AIOMETADATA_URL", "").rstrip("/")
        uuid = env.get("AIOMETADATA_UUID", "")

        match normalize_aiometadata(config):
            case Err() as err:
                return err
            case Ok(clean_cfg):
                pass

        body: JsonDict = {
            "config": clean_cfg,
            "password": env.get("AIOMETADATA_PASSWORD", ""),
            "force": True,
        }
        if addon_pw := env.get("AIOMETADATA_ADDON_PASSWORD", ""):
            body["addonPassword"] = addon_pw

        if create:
            url = f"{base_url}/api/config/save"
            match self._http.request_json("POST", url, json_body=body):
                case Err() as err:
                    return err
                case Ok(resp) if resp.status_code not in (200, 201) or not isinstance(resp.data, dict):
                    return _http_err("AIOMetadata", "Create", resp.status_code, resp.data, url)
                case Ok(resp):
                    new_uuid = str(resp.data.get("userUUID", ""))
                    install_url = str(
                        resp.data.get("installUrl")
                        or f"{base_url}/stremio/{new_uuid}/manifest.json"
                    )
                    return Ok(
                        (
                            ServicePushOutcome(
                                service="aiometadata",
                                uuid=new_uuid,
                                created=True,
                                install_url=install_url,
                            ),
                            clean_cfg,
                            {},
                        )
                    )

        url = f"{base_url}/api/config/update/{uuid}"
        match self._http.request_json("PUT", url, json_body=body):
            case Err() as err:
                return err
            case Ok(resp) if resp.status_code != 200:
                return _http_err("AIOMetadata", "Push", resp.status_code, resp.data, url)
            case Ok():
                return Ok(
                    (
                        ServicePushOutcome(service="aiometadata", uuid=uuid, created=False),
                        clean_cfg,
                        {},
                    )
                )


class AIOManagerService:
    """Manages pulling, dehydrating, rehydrating, encrypting, and pushing AIOManager state."""

    def __init__(
        self,
        http_client: HttpClientProtocol,
        client_syncers: Mapping[str, ClientSyncProtocol] | None = None,
    ) -> None:
        self._http = http_client
        self._client_syncers = dict(client_syncers or {})

    @property
    def service_id(self) -> ServiceId:
        return "aiomanager"

    def is_configured_for_pull(self, env: EnvMap) -> bool:
        return bool(
            env.get("AIOMANAGER_URL")
            and env.get("AIOMANAGER_UUID")
            and env.get("AIOMANAGER_SYNC_PASSWORD")
        )

    def is_configured_for_push(self, env: EnvMap, *, create: bool = False) -> bool:
        _ = create
        return self.is_configured_for_pull(env)

    def pull_remote(self, env: EnvMap) -> Result[JsonDict, StreamWeaverError]:
        base_url = env.get("AIOMANAGER_URL", "").rstrip("/")
        uuid = env.get("AIOMANAGER_UUID", "")
        password = env.get("AIOMANAGER_SYNC_PASSWORD", "")
        url = f"{base_url}/api/sync/{uuid}"

        derived_token = derive_sync_token(password)
        res = self._http.request_json("GET", url, headers={"x-sync-password": derived_token})
        match res:
            case Err() as err:
                return err
            case Ok(resp) if resp.status_code == 401:
                match self._http.request_json("GET", url, headers={"x-sync-password": password}):
                    case Err() as err:
                        return err
                    case Ok(resp2):
                        resp = resp2
            case Ok(resp):
                pass

        if resp.status_code != 200 or not isinstance(resp.data, Mapping):
            return _http_err("AIOManager", "Pull", resp.status_code, resp.data, url)

        match decrypt_envelope(resp.data, password):
            case Err() as err:
                return err
            case Ok(decrypted_state):
                return dehydrate_aiomanager(decrypted_state)

    def _refresh_manifests(self, state: JsonDict) -> JsonDict:
        """Fetches fresh `manifest.json` for AIOMetadata, AIOStreams, and Watchly."""
        updated = copy.deepcopy(state)
        accounts = updated.get("accounts")
        if not isinstance(accounts, list):
            return updated

        for acc in accounts:
            if not isinstance(acc, dict) or not isinstance(acc.get("addons"), list):
                continue
            for a in acc["addons"]:
                if not isinstance(a, dict):
                    continue
                raw_mf = a.get("manifest")
                mf_existing: dict[str, Any] = raw_mf if isinstance(raw_mf, dict) else {}
                mid = str(mf_existing.get("id", ""))
                t_url = str(a.get("transportUrl", "")).rstrip("/")
                if not t_url:
                    continue
                if mid in ("aio-metadata", "com.elfhosted.watchly") or mid.startswith("aiostreams"):
                    m_url = t_url if t_url.endswith("/manifest.json") else f"{t_url}/manifest.json"
                    match self._http.request_json("GET", m_url, timeout=15.0):
                        case Ok(resp) if (
                            resp.status_code == 200
                            and isinstance(resp.data, dict)
                            and resp.data.get("id")
                        ):
                            mf = sanitize_manifest_home_catalogs(dict(resp.data))
                            if mid == "com.linvo.cinemeta":
                                mf["catalogs"] = []
                            a["manifest"] = mf
                            ver = str(mf.get("version", "1.0.0"))
                            manifest_id = f"{mf['id']}:{ver}"
                            a["manifestId"] = manifest_id
                            manifests_dict = updated.setdefault("manifests", {})
                            if isinstance(manifests_dict, dict):
                                manifests_dict[manifest_id] = mf
                            addons_sec = updated.get("addons")
                            if isinstance(addons_sec, dict) and isinstance(
                                addons_sec.get("savedAddons"), list
                            ):
                                for sa in addons_sec["savedAddons"]:
                                    if (
                                        isinstance(sa, dict)
                                        and isinstance(sa.get("manifest"), dict)
                                        and sa["manifest"].get("id") == mf["id"]
                                    ):
                                        sa["manifest"] = copy.deepcopy(mf)
                        case _:
                            continue
        return updated

    def push_remote(
        self,
        config: JsonDict,
        env: EnvMap,
        *,
        create: bool = False,
    ) -> Result[tuple[ServicePushOutcome, JsonDict, dict[str, str]], StreamWeaverError]:
        _ = create
        base_url = env.get("AIOMANAGER_URL", "").rstrip("/")
        uuid = env.get("AIOMANAGER_UUID", "")
        password = env.get("AIOMANAGER_SYNC_PASSWORD", "")

        rehydrated = rehydrate_aiomanager(config)
        refreshed = self._refresh_manifests(rehydrated)

        match dehydrate_aiomanager(refreshed):
            case Err() as err:
                return err
            case Ok(dehydrated_for_disk):
                pass

        match encrypt_envelope(refreshed, password):
            case Err() as err:
                return err
            case Ok(envelope):
                pass

        derived_token = derive_sync_token(password)
        url = f"{base_url}/api/sync/{uuid}"
        match self._http.request_json(
            "POST", url, headers={"x-sync-password": derived_token}, json_body=envelope
        ):
            case Err() as err:
                return err
            case Ok(resp) if resp.status_code not in (200, 201):
                return _http_err("AIOManager", "Push", resp.status_code, resp.data, url)
            case Ok():
                pass

        salt_b64 = str(refreshed.get("salt") or "")
        synced_summaries: list[str] = []
        discovered_secrets: dict[str, str] = {}

        accounts = refreshed.get("accounts")
        if isinstance(accounts, list):
            for acc in accounts:
                if not isinstance(acc, dict) or not isinstance(acc.get("connections"), list):
                    continue
                for conn in acc["connections"]:
                    if not isinstance(conn, dict) or not conn.get("enabled", True):
                        continue
                    plat = str(conn.get("platform") or "").lower()
                    syncer = self._client_syncers.get(plat)
                    if syncer is not None:
                        match syncer.sync_account(acc, conn, password, salt_b64, env):
                            case Ok((msg, new_secrets)):
                                synced_summaries.append(msg)
                                discovered_secrets.update(new_secrets)
                            case Err():
                                continue

        warmed, total = warm_home_catalogs(refreshed, self._http)
        if total > 0:
            synced_summaries.append(f"[warm] Pre-warmed {warmed}/{total} Home catalog rows")

        return Ok(
            (
                ServicePushOutcome(
                    service="aiomanager",
                    uuid=uuid,
                    created=False,
                    synced_clients=tuple(synced_summaries),
                ),
                dehydrated_for_disk,
                discovered_secrets,
            )
        )


def extract_home_catalog_urls(state: JsonDict) -> list[str]:
    """
    Purely extracts all unique Stremio/Nuvio Home catalog endpoint URLs
    (`/catalog/{type}/{id}.json`) from a rehydrated AIOManager state object.
    """
    rehydrated = rehydrate_aiomanager(state) if "manifests" not in state else state
    seen: set[str] = set()
    urls: list[str] = []

    accounts = rehydrated.get("accounts")
    if not isinstance(accounts, list):
        return urls

    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        addons = acc.get("addons")
        if not isinstance(addons, list):
            continue
        for addon in addons:
            if not isinstance(addon, dict):
                continue
            flags = addon.get("flags")
            if isinstance(flags, dict) and not flags.get("enabled", True):
                continue
            t_url = str(addon.get("transportUrl") or "").strip()
            if not t_url:
                continue
            addon_base = t_url.removesuffix("/manifest.json").rstrip("/")
            mf = addon.get("manifest")
            if not isinstance(mf, dict):
                continue
            catalogs = mf.get("catalogs")
            if not isinstance(catalogs, list):
                continue
            for cat in catalogs:
                if not isinstance(cat, dict):
                    continue
                cat_type = str(cat.get("type") or "").strip()
                cat_id = str(cat.get("id") or "").strip()
                if not cat_type or not cat_id:
                    continue
                extras = cat.get("extra")
                is_home = not isinstance(extras, list) or all(
                    not (isinstance(e, dict) and e.get("isRequired", False)) for e in extras
                )
                if not is_home:
                    continue
                q_type = urllib.parse.quote(cat_type, safe=".:_-")
                q_id = urllib.parse.quote(cat_id, safe=".:_-")
                cat_url = f"{addon_base}/catalog/{q_type}/{q_id}.json"
                if cat_url not in seen:
                    seen.add(cat_url)
                    urls.append(cat_url)
    return urls


def warm_home_catalogs(
    state: JsonDict,
    http_client: HttpClientProtocol,
    *,
    max_workers: int = 8,
) -> tuple[int, int]:
    """
    Concurrently fetches all Home catalog endpoints across enabled addons so server-side
    caches (AIOMetadata / Watchly / AIOStreams) are warm before Nuvio scrolls the Home feed.
    Short-circuits unreachable origins (e.g. Cloudflare 52x) so offline hosts fail fast.
    Returns `(warmed_count, total_count)`.
    """
    urls = extract_home_catalog_urls(state)
    if not urls:
        return 0, 0

    dead_hosts: set[str] = set()

    def _fetch_one(url: str) -> bool:
        host = urllib.parse.urlsplit(url).netloc
        if host in dead_hosts:
            return False
        match http_client.request_json("GET", url, timeout=8.0):
            case Ok(resp) if resp.status_code == 200 and isinstance(resp.data, dict):
                return isinstance(resp.data.get("metas"), list)
            case Ok(resp) if resp.status_code >= 520:
                dead_hosts.add(host)
                return False
            case Err():
                dead_hosts.add(host)
                return False
            case _:
                return False

    warmed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for ok in pool.map(_fetch_one, urls):
            if ok:
                warmed += 1
    return warmed, len(urls)


def build_config_services(
    http_client: HttpClientProtocol,
) -> dict[ServiceId, ConfigServiceProtocol]:
    """Constructs the default dictionary of `ConfigServiceProtocol` handlers."""
    nuvio = NuvioClientSync(http_client)
    stremio = StremioClientSync(http_client)
    client_syncers: dict[str, ClientSyncProtocol] = {
        nuvio.platform_id: nuvio,
        stremio.platform_id: stremio,
    }
    aiostreams = AIOStreamsService(http_client)
    aiometadata = AIOMetadataService(http_client)
    aiomanager = AIOManagerService(http_client, client_syncers=client_syncers)
    return {
        aiostreams.service_id: aiostreams,
        aiometadata.service_id: aiometadata,
        aiomanager.service_id: aiomanager,
    }
