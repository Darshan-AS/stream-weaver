"""
Live workspace health doctor and Stremio/Nuvio addon manifest inspector.

Runs structured diagnostics across environment keys, AIOStreams & AIOMetadata servers
and provisioned instances, Debrid/Usenet providers, TMDB/MDBList metadata APIs, and
client login readiness through an injected `HttpClientProtocol`.
"""

from __future__ import annotations

import concurrent.futures
import urllib.parse
from collections.abc import Callable, Mapping, Sequence

from stream_weaver.core.errors import StreamWeaverError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict
from stream_weaver.effects.http import HttpClientProtocol

HealthCategory = str
HealthStatus = str


def _make_check(
    check_id: str,
    category: HealthCategory,
    label: str,
    status: HealthStatus,
    detail: str,
) -> JsonDict:
    return {
        "id": check_id,
        "category": category,
        "label": label,
        "status": status,
        "detail": detail,
    }


def normalize_manifest_urls(raw_url: str) -> Result[tuple[str, str], StreamWeaverError]:
    """
    Normalizes an addon base URL, `/manifest.json` URL, or `stremio://` link into
    `(manifest_url, transport_url)`.
    """
    cleaned = raw_url.strip()
    if not cleaned:
        return Err(StreamWeaverError("Addon URL must not be empty"))
    if cleaned.lower().startswith("stremio://"):
        cleaned = "https://" + cleaned[len("stremio://") :]

    parts = urllib.parse.urlsplit(cleaned)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return Err(
            StreamWeaverError(
                f"Addon URL must start with https://, http://, or stremio:// (got {raw_url!r})"
            )
        )

    clean_path = parts.path.rstrip("/")
    if clean_path.endswith("/manifest.json"):
        manifest_path = clean_path
        base_path = clean_path.removesuffix("/manifest.json")
    else:
        base_path = clean_path
        manifest_path = f"{clean_path}/manifest.json"

    manifest_url = urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, manifest_path, parts.query, "")
    )
    transport_url = (
        manifest_url
        if parts.query
        else urllib.parse.urlunsplit((parts.scheme, parts.netloc, base_path, "", "")).rstrip("/")
    )
    return Ok((manifest_url, transport_url))


def inspect_addon_manifest(
    url: str,
    *,
    http_client: HttpClientProtocol,
) -> Result[JsonDict, StreamWeaverError]:
    """
    Fetches and validates a Stremio/Nuvio `manifest.json` from either a base addon URL,
    an explicit `.../manifest.json` URL, or a `stremio://` link.
    """
    match normalize_manifest_urls(url):
        case Err() as err:
            return err
        case Ok((manifest_url, transport_url)):
            pass

    match http_client.request_json("GET", manifest_url, timeout=12.0):
        case Err(net_err):
            return Err(StreamWeaverError(f"Failed to fetch addon manifest ({manifest_url}): {net_err}"))
        case Ok(resp):
            if resp.status_code != 200:
                return Err(
                    StreamWeaverError(
                        f"Addon manifest returned HTTP {resp.status_code} ({manifest_url})"
                    )
                )
            data = resp.data

    if not isinstance(data, Mapping):
        return Err(StreamWeaverError(f"Addon manifest did not return a JSON object ({manifest_url})"))

    addon_id = str(data.get("id") or "").strip()
    addon_name = str(data.get("name") or "").strip()
    if not addon_id or not addon_name:
        return Err(
            StreamWeaverError(
                f"Invalid Stremio manifest at {manifest_url}: missing required `id` or `name`"
            )
        )

    raw_resources = data.get("resources")
    resources: list[str] = []
    if isinstance(raw_resources, list):
        for res_item in raw_resources:
            if isinstance(res_item, str) and res_item.strip():
                r_name = res_item.strip()
            elif (
                isinstance(res_item, Mapping)
                and isinstance(res_item.get("name"), str)
                and res_item["name"].strip()
            ):
                r_name = res_item["name"].strip()
            else:
                continue
            if r_name not in resources:
                resources.append(r_name)

    raw_types = data.get("types")
    types: list[str] = (
        [str(t).strip() for t in raw_types if isinstance(t, str) and str(t).strip()]
        if isinstance(raw_types, list)
        else []
    )

    raw_catalogs = data.get("catalogs")
    catalogs_count = len(raw_catalogs) if isinstance(raw_catalogs, list) else 0
    version = str(data.get("version") or "0.0.0").strip()
    description = str(data.get("description") or "").strip()

    return Ok(
        {
            "id": addon_id,
            "name": addon_name,
            "version": version,
            "description": description,
            "resources": resources,
            "types": types,
            "catalogsCount": catalogs_count,
            "manifestUrl": manifest_url,
            "transportUrl": transport_url,
        }
    )


def _probe_server_manifest(
    *,
    check_id: str,
    label: str,
    base_url: str,
    url_key: str,
    required_set: set[str],
    http_client: HttpClientProtocol,
    probe_network: bool,
) -> JsonDict:
    if not base_url:
        status = "error" if url_key in required_set else "warn"
        return _make_check(check_id, "instances", label, status, f"{url_key} is not set")
    if not probe_network:
        return _make_check(
            check_id, "instances", label, "skipped", f"Network probe skipped ({base_url})"
        )
    manifest_url = f"{base_url}/manifest.json"
    match http_client.request_json("GET", manifest_url, timeout=10.0):
        case Err(net_err):
            return _make_check(
                check_id, "instances", label, "error", f"Unreachable ({base_url}): {net_err}"
            )
        case Ok(resp):
            if resp.status_code == 200 and isinstance(resp.data, Mapping) and (
                resp.data.get("id") or resp.data.get("name")
            ):
                svc_name = str(resp.data.get("name") or label)
                ver = str(resp.data.get("version") or "unknown")
                return _make_check(
                    check_id,
                    "instances",
                    label,
                    "ok",
                    f"Reachable ({svc_name} v{ver}) at {base_url}",
                )
            return _make_check(
                check_id,
                "instances",
                label,
                "error",
                f"HTTP {resp.status_code} from {manifest_url}",
            )


def _probe_provisioned_uuid(
    *,
    check_id: str,
    label: str,
    base_url: str,
    uuid_val: str,
    encrypted_password: str,
    is_aiostreams: bool,
    http_client: HttpClientProtocol,
    probe_network: bool,
) -> JsonDict:
    if not uuid_val:
        return _make_check(
            check_id,
            "instances",
            label,
            "skipped",
            "UUID not provisioned yet",
        )
    if not base_url:
        return _make_check(
            check_id,
            "instances",
            label,
            "error",
            f"UUID {uuid_val} is set without a server base URL",
        )
    if not probe_network:
        return _make_check(
            check_id,
            "instances",
            label,
            "skipped",
            f"Network probe skipped (UUID {uuid_val})",
        )
    manifest_url = (
        f"{base_url}/stremio/{uuid_val}/{encrypted_password}/manifest.json"
        if (is_aiostreams and encrypted_password)
        else f"{base_url}/stremio/{uuid_val}/manifest.json"
    )
    match http_client.request_json("GET", manifest_url, timeout=10.0):
        case Err(net_err):
            return _make_check(
                check_id,
                "instances",
                label,
                "error",
                f"Provisioned manifest unreachable ({uuid_val}): {net_err}",
            )
        case Ok(resp):
            if resp.status_code == 200 and isinstance(resp.data, Mapping) and resp.data.get("id"):
                cats = resp.data.get("catalogs")
                cat_count = len(cats) if isinstance(cats, list) else 0
                detail = (
                    f"Provisioned UUID {uuid_val} active ({cat_count} catalogs)"
                    if not is_aiostreams
                    else f"Provisioned UUID {uuid_val} active ({resp.data.get('name', 'AIOStreams')})"
                )
                return _make_check(check_id, "instances", label, "ok", detail)
            if is_aiostreams and not encrypted_password and resp.status_code in (401, 403):
                return _make_check(
                    check_id,
                    "instances",
                    label,
                    "warn",
                    f"UUID {uuid_val} requires SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD (HTTP {resp.status_code})",
                )
            return _make_check(
                check_id,
                "instances",
                label,
                "error",
                f"Provisioned UUID {uuid_val} returned HTTP {resp.status_code}",
            )


def _probe_tmdb(api_key: str, http_client: HttpClientProtocol, probe_network: bool) -> JsonDict:
    label = "TMDB v3 API Key"
    if not api_key:
        return _make_check("metadata.tmdb", "metadata", label, "skipped", "SECRET_TMDB_API_KEY not set")
    if not probe_network:
        return _make_check("metadata.tmdb", "metadata", label, "skipped", "Network probe skipped")
    q_key = urllib.parse.quote(api_key, safe="")
    url = f"https://api.themoviedb.org/3/configuration?api_key={q_key}"
    match http_client.request_json("GET", url, timeout=10.0):
        case Err(net_err):
            return _make_check("metadata.tmdb", "metadata", label, "error", f"TMDB probe failed: {net_err}")
        case Ok(resp):
            if resp.status_code == 200 and isinstance(resp.data, Mapping):
                return _make_check("metadata.tmdb", "metadata", label, "ok", "TMDB v3 API key verified")
            return _make_check(
                "metadata.tmdb",
                "metadata",
                label,
                "error",
                f"TMDB rejected API key (HTTP {resp.status_code})",
            )


def _probe_mdblist(api_key: str, http_client: HttpClientProtocol, probe_network: bool) -> JsonDict:
    label = "MDBList API Key"
    if not api_key:
        return _make_check(
            "metadata.mdblist", "metadata", label, "skipped", "SECRET_MDBLIST_API_KEY not set"
        )
    if not probe_network:
        return _make_check("metadata.mdblist", "metadata", label, "skipped", "Network probe skipped")
    q_key = urllib.parse.quote(api_key, safe="")
    url = f"https://api.mdblist.com/user?apikey={q_key}"
    match http_client.request_json("GET", url, timeout=10.0):
        case Err(net_err):
            return _make_check(
                "metadata.mdblist", "metadata", label, "error", f"MDBList probe failed: {net_err}"
            )
        case Ok(resp):
            if (
                resp.status_code == 200
                and isinstance(resp.data, Mapping)
                and not resp.data.get("error")
            ):
                who = str(resp.data.get("username") or resp.data.get("patron_status") or "verified")
                return _make_check(
                    "metadata.mdblist", "metadata", label, "ok", f"MDBList API key verified ({who})"
                )
            return _make_check(
                "metadata.mdblist",
                "metadata",
                label,
                "error",
                f"MDBList rejected API key (HTTP {resp.status_code})",
            )


def _probe_torbox(api_key: str, http_client: HttpClientProtocol, probe_network: bool) -> JsonDict:
    label = "TorBox API Key"
    if not probe_network:
        return _make_check("debrid.torbox", "debrid", label, "skipped", "Network probe skipped")
    url = "https://api.torbox.app/v1/api/user/me"
    match http_client.request_json(
        "GET", url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0
    ):
        case Err(net_err):
            return _make_check("debrid.torbox", "debrid", label, "error", f"TorBox probe failed: {net_err}")
        case Ok(resp):
            if resp.status_code == 200 and isinstance(resp.data, Mapping):
                if resp.data.get("success") is False:
                    msg = str(resp.data.get("detail") or "TorBox rejected API key")
                    return _make_check("debrid.torbox", "debrid", label, "error", msg)
                inner = resp.data.get("data")
                user_obj = inner if isinstance(inner, Mapping) else resp.data
                who = str(user_obj.get("email") or user_obj.get("plan") or "authenticated")
                return _make_check("debrid.torbox", "debrid", label, "ok", f"TorBox authenticated ({who})")
            return _make_check(
                "debrid.torbox",
                "debrid",
                label,
                "error",
                f"TorBox rejected API key (HTTP {resp.status_code})",
            )


def _probe_realdebrid(api_key: str, http_client: HttpClientProtocol, probe_network: bool) -> JsonDict:
    label = "Real-Debrid API Token"
    if not probe_network:
        return _make_check("debrid.realdebrid", "debrid", label, "skipped", "Network probe skipped")
    url = "https://api.real-debrid.com/rest/1.0/user"
    match http_client.request_json(
        "GET", url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0
    ):
        case Err(net_err):
            return _make_check(
                "debrid.realdebrid", "debrid", label, "error", f"Real-Debrid probe failed: {net_err}"
            )
        case Ok(resp):
            if (
                resp.status_code == 200
                and isinstance(resp.data, Mapping)
                and not resp.data.get("error_code")
            ):
                who = str(resp.data.get("username") or resp.data.get("email") or "authenticated")
                return _make_check(
                    "debrid.realdebrid", "debrid", label, "ok", f"Real-Debrid authenticated ({who})"
                )
            return _make_check(
                "debrid.realdebrid",
                "debrid",
                label,
                "error",
                f"Real-Debrid rejected API token (HTTP {resp.status_code})",
            )


def _probe_alldebrid(api_key: str, http_client: HttpClientProtocol, probe_network: bool) -> JsonDict:
    label = "AllDebrid API Key"
    if not probe_network:
        return _make_check("debrid.alldebrid", "debrid", label, "skipped", "Network probe skipped")
    q_key = urllib.parse.quote(api_key, safe="")
    url = f"https://api.alldebrid.com/v4/user?agent=streamweaver&apikey={q_key}"
    match http_client.request_json("GET", url, timeout=10.0):
        case Err(net_err):
            return _make_check(
                "debrid.alldebrid", "debrid", label, "error", f"AllDebrid probe failed: {net_err}"
            )
        case Ok(resp):
            if resp.status_code == 200 and isinstance(resp.data, Mapping):
                if resp.data.get("status") == "error":
                    err_obj = resp.data.get("error")
                    msg = (
                        str(err_obj.get("message") or "Invalid AllDebrid API key")
                        if isinstance(err_obj, Mapping)
                        else "Invalid AllDebrid API key"
                    )
                    return _make_check("debrid.alldebrid", "debrid", label, "error", msg)
                data_obj = resp.data.get("data")
                raw_user = data_obj.get("user") if isinstance(data_obj, Mapping) else None
                user_obj = raw_user if isinstance(raw_user, Mapping) else {}
                who = str(user_obj.get("username") or "authenticated")
                return _make_check(
                    "debrid.alldebrid", "debrid", label, "ok", f"AllDebrid authenticated ({who})"
                )
            return _make_check(
                "debrid.alldebrid",
                "debrid",
                label,
                "error",
                f"AllDebrid rejected API key (HTTP {resp.status_code})",
            )


def _check_client_sessions(env: Mapping[str, str]) -> JsonDict:
    has_refresh = any(
        k == "NUVIO_REFRESH_TOKEN" or (k.startswith("NUVIO_") and k.endswith("_REFRESH_TOKEN"))
        for k, v in env.items()
        if str(v).strip()
    )
    has_access = any(
        k == "NUVIO_ACCESS_TOKEN" or (k.startswith("NUVIO_") and k.endswith("_ACCESS_TOKEN"))
        for k, v in env.items()
        if str(v).strip()
    )
    has_email = any(
        k == "NUVIO_EMAIL" or (k.startswith("NUVIO_") and k.endswith("_EMAIL"))
        for k, v in env.items()
        if str(v).strip()
    )
    if has_refresh and has_access:
        return _make_check(
            "clients.nuvio", "clients", "Nuvio Account Session", "ok", "Nuvio session tokens present"
        )
    if has_email or has_access or has_refresh:
        return _make_check(
            "clients.nuvio",
            "clients",
            "Nuvio Account Session",
            "warn",
            "Incomplete Nuvio session tokens (sign in again to save access + refresh tokens)",
        )
    return _make_check(
        "clients.nuvio", "clients", "Nuvio Account Session", "skipped", "No Nuvio login configured"
    )


def run_health_checks(
    *,
    env: Mapping[str, str],
    required_keys: Sequence[str],
    http_client: HttpClientProtocol,
    probe_network: bool = True,
) -> JsonDict:
    """
    Executes structured workspace health checks across `.env` completeness, AIOStreams &
    AIOMetadata reachability + provisioned UUIDs, Debrid API keys, TMDB/MDBList metadata keys,
    and client login tokens.
    """
    req_ordered = list(dict.fromkeys(str(k).strip() for k in required_keys if str(k).strip()))
    req_set = set(req_ordered)
    missing_req = [k for k in req_ordered if not str(env.get(k, "")).strip()]

    if missing_req:
        env_check = _make_check(
            "env.required",
            "instances",
            "Required .env Keys",
            "error",
            f"Missing {len(missing_req)} required key(s): {', '.join(missing_req)}",
        )
    elif req_ordered:
        env_check = _make_check(
            "env.required",
            "instances",
            "Required .env Keys",
            "ok",
            f"All {len(req_ordered)} required key(s) present",
        )
    else:
        env_check = _make_check(
            "env.required",
            "instances",
            "Required .env Keys",
            "ok",
            "No required keys missing",
        )

    streams_url = str(env.get("AIOSTREAMS_URL") or "").strip().rstrip("/")
    streams_uuid = str(env.get("AIOSTREAMS_UUID") or "").strip()
    streams_enc_pw = str(env.get("SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD") or "").strip()

    meta_url = str(env.get("AIOMETADATA_URL") or "").strip().rstrip("/")
    meta_uuid = str(env.get("AIOMETADATA_UUID") or "").strip()

    tmdb_key = str(env.get("SECRET_TMDB_API_KEY") or "").strip()
    mdblist_key = str(env.get("SECRET_MDBLIST_API_KEY") or "").strip()

    torbox_key = str(env.get("SECRET_TORBOX_API_KEY") or "").strip()
    rd_key = str(env.get("SECRET_RD_API_KEY") or "").strip()
    ad_key = str(env.get("SECRET_ALLDEBRID_API_KEY") or "").strip()
    pm_key = str(env.get("SECRET_PREMIUMIZE_API_KEY") or "").strip()
    en_user = str(env.get("SECRET_EASYNEWS_USERNAME") or "").strip()
    en_pass = str(env.get("SECRET_EASYNEWS_PASSWORD") or "").strip()

    tasks: list[Callable[[], JsonDict]] = [
        lambda: _probe_server_manifest(
            check_id="aiostreams.server",
            label="AIOStreams Server",
            base_url=streams_url,
            url_key="AIOSTREAMS_URL",
            required_set=req_set,
            http_client=http_client,
            probe_network=probe_network,
        ),
        lambda: _probe_provisioned_uuid(
            check_id="aiostreams.instance",
            label="AIOStreams Provisioned UUID",
            base_url=streams_url,
            uuid_val=streams_uuid,
            encrypted_password=streams_enc_pw,
            is_aiostreams=True,
            http_client=http_client,
            probe_network=probe_network,
        ),
        lambda: _probe_server_manifest(
            check_id="aiometadata.server",
            label="AIOMetadata Server",
            base_url=meta_url,
            url_key="AIOMETADATA_URL",
            required_set=req_set,
            http_client=http_client,
            probe_network=probe_network,
        ),
        lambda: _probe_provisioned_uuid(
            check_id="aiometadata.instance",
            label="AIOMetadata Provisioned UUID",
            base_url=meta_url,
            uuid_val=meta_uuid,
            encrypted_password="",
            is_aiostreams=False,
            http_client=http_client,
            probe_network=probe_network,
        ),
    ]

    if torbox_key:
        tasks.append(lambda: _probe_torbox(torbox_key, http_client, probe_network))
    if rd_key:
        tasks.append(lambda: _probe_realdebrid(rd_key, http_client, probe_network))
    if ad_key:
        tasks.append(lambda: _probe_alldebrid(ad_key, http_client, probe_network))
    if pm_key:
        tasks.append(
            lambda: _make_check(
                "debrid.premiumize",
                "debrid",
                "Premiumize API Key",
                "ok",
                "Premiumize API key configured",
            )
        )
    if en_user or en_pass:
        tasks.append(
            lambda: _make_check(
                "debrid.easynews",
                "debrid",
                "Easynews Credentials",
                "ok" if (en_user and en_pass) else "warn",
                "Easynews username & password configured"
                if (en_user and en_pass)
                else "Both SECRET_EASYNEWS_USERNAME and SECRET_EASYNEWS_PASSWORD are required",
            )
        )
    if not (torbox_key or rd_key or ad_key or pm_key or en_user or en_pass):
        tasks.append(
            lambda: _make_check(
                "debrid.none",
                "debrid",
                "Debrid / Usenet Provider",
                "skipped",
                "No Debrid or Usenet API key set",
            )
        )

    tasks.append(lambda: _probe_tmdb(tmdb_key, http_client, probe_network))
    tasks.append(lambda: _probe_mdblist(mdblist_key, http_client, probe_network))

    if probe_network and len(tasks) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(tasks))) as pool:
            probed_checks = list(pool.map(lambda fn: fn(), tasks))
    else:
        probed_checks = [fn() for fn in tasks]

    checks: list[JsonDict] = [env_check, *probed_checks, _check_client_sessions(env)]

    summary: dict[str, int] = {"ok": 0, "warn": 0, "error": 0, "skipped": 0}
    for chk in checks:
        st = str(chk["status"])
        if st in summary:
            summary[st] += 1

    if summary["error"] > 0:
        overall = "error"
    elif summary["warn"] > 0:
        overall = "warn"
    else:
        overall = "healthy"

    return {
        "overall": overall,
        "summary": summary,
        "checks": checks,
    }
