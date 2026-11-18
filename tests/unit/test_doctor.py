"""Unit tests for the live workspace health doctor and Stremio addon manifest inspector."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from stream_weaver.core.doctor import inspect_addon_manifest, run_health_checks
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.effects.http import HttpResponse

ResponseHandler = Callable[[str, str, Mapping[str, str] | None], Result[HttpResponse, NetworkError]]


class FakeDoctorHttpClient:
    """Deterministic `HttpClientProtocol` double recording every outbound call."""

    def __init__(self, handler: ResponseHandler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        raw_body: bytes | None = None,
        timeout: float = 30.0,
    ) -> Result[HttpResponse, NetworkError]:
        _ = json_body, raw_body, timeout
        hdrs = dict(headers or {})
        self.calls.append((method, url, hdrs))
        return self._handler(method, url, hdrs)


def test_inspect_addon_manifest_normalizes_stremio_and_base_urls() -> None:
    def _handler(
        method: str, url: str, _hdrs: Mapping[str, str] | None
    ) -> Result[HttpResponse, NetworkError]:
        assert method == "GET"
        assert url == "https://v3-cinemeta.strem.io/manifest.json"
        return Ok(
            HttpResponse(
                status_code=200,
                data={
                    "id": "com.linvo.cinemeta",
                    "name": "Cinemeta",
                    "version": "3.0.13",
                    "description": "Official movie and series metadata",
                    "resources": [
                        "catalog",
                        {"name": "meta", "types": ["movie", "series"]},
                        {"name": "subtitles", "types": ["movie"]},
                    ],
                    "types": ["movie", "series"],
                    "catalogs": [{"id": "top", "type": "movie"}, {"id": "top", "type": "series"}],
                },
                text="",
            )
        )

    client = FakeDoctorHttpClient(_handler)

    res_stremio = inspect_addon_manifest(
        "stremio://v3-cinemeta.strem.io/manifest.json", http_client=client
    )
    assert isinstance(res_stremio, Ok)
    info = res_stremio.value
    assert info == {
        "id": "com.linvo.cinemeta",
        "name": "Cinemeta",
        "version": "3.0.13",
        "description": "Official movie and series metadata",
        "resources": ["catalog", "meta", "subtitles"],
        "types": ["movie", "series"],
        "catalogsCount": 2,
        "manifestUrl": "https://v3-cinemeta.strem.io/manifest.json",
        "transportUrl": "https://v3-cinemeta.strem.io",
    }

    res_base = inspect_addon_manifest("https://v3-cinemeta.strem.io/", http_client=client)
    assert isinstance(res_base, Ok)
    assert res_base.value["manifestUrl"] == "https://v3-cinemeta.strem.io/manifest.json"
    assert res_base.value["transportUrl"] == "https://v3-cinemeta.strem.io"


def test_inspect_addon_manifest_preserves_query_and_rejects_bad_manifests() -> None:
    def _handler(
        _method: str, url: str, _hdrs: Mapping[str, str] | None
    ) -> Result[HttpResponse, NetworkError]:
        if "token=abc" in url:
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={"id": "org.custom", "name": "Custom", "resources": ["stream"], "types": ["movie"]},
                    text="",
                )
            )
        if "missing-fields" in url:
            return Ok(HttpResponse(status_code=200, data={"version": "1.0.0"}, text=""))
        if "not-found" in url:
            return Ok(HttpResponse(status_code=404, data={"error": "not found"}, text=""))
        return Err(NetworkError(message="DNS failure", url=url))

    client = FakeDoctorHttpClient(_handler)

    ok_q = inspect_addon_manifest("https://stream-weaver.dev/addon?token=abc", http_client=client)
    assert isinstance(ok_q, Ok)
    assert ok_q.value["manifestUrl"] == "https://stream-weaver.dev/addon/manifest.json?token=abc"
    assert ok_q.value["transportUrl"] == "https://stream-weaver.dev/addon/manifest.json?token=abc"

    assert isinstance(inspect_addon_manifest("", http_client=client), Err)
    assert isinstance(inspect_addon_manifest("ftp://stream-weaver.dev", http_client=client), Err)
    assert isinstance(
        inspect_addon_manifest("https://stream-weaver.dev/missing-fields", http_client=client), Err
    )
    assert isinstance(
        inspect_addon_manifest("https://stream-weaver.dev/not-found", http_client=client), Err
    )
    assert isinstance(
        inspect_addon_manifest("https://stream-weaver.dev/unreachable", http_client=client), Err
    )


def test_run_health_checks_all_green_across_all_categories() -> None:
    streams_uuid = "00000000-0000-4000-8000-000000000001"
    meta_uuid = "00000000-0000-4000-8000-000000000002"

    def _handler(
        method: str, url: str, hdrs: Mapping[str, str] | None
    ) -> Result[HttpResponse, NetworkError]:
        assert method == "GET"
        headers = dict(hdrs or {})
        if url == "https://aiostreams.elfhosted.com/manifest.json":
            return Ok(
                HttpResponse(
                    200, {"id": "com.aiostreams", "name": "AIOStreams", "version": "2.20.0"}, ""
                )
            )
        if url == f"https://aiostreams.elfhosted.com/stremio/{streams_uuid}/enc-pw/manifest.json":
            return Ok(
                HttpResponse(
                    200, {"id": "com.aiostreams.user", "name": "AIOStreams", "resources": ["stream"]}, ""
                )
            )
        if url == "https://aiometadata.elfhosted.com/manifest.json":
            return Ok(
                HttpResponse(
                    200, {"id": "com.aiometadata", "name": "AIOMetadata", "version": "1.9.0"}, ""
                )
            )
        if url == f"https://aiometadata.elfhosted.com/stremio/{meta_uuid}/manifest.json":
            return Ok(
                HttpResponse(
                    200,
                    {
                        "id": "com.aiometadata.user",
                        "name": "AIOMetadata",
                        "catalogs": [{"id": "tmdb.trending", "type": "movie"}],
                    },
                    "",
                )
            )
        if url == "https://api.torbox.app/v1/api/user/me":
            assert headers.get("Authorization") == "Bearer tb-secret"
            return Ok(HttpResponse(200, {"success": True, "data": {"email": "user@example.com"}}, ""))
        if url == "https://api.real-debrid.com/rest/1.0/user":
            assert headers.get("Authorization") == "Bearer rd-secret"
            return Ok(HttpResponse(200, {"username": "rd_user", "type": "premium"}, ""))
        if url == "https://api.alldebrid.com/v4/user?agent=streamweaver&apikey=ad-secret":
            return Ok(
                HttpResponse(200, {"status": "success", "data": {"user": {"username": "ad_user"}}}, "")
            )
        if url == "https://api.themoviedb.org/3/configuration?api_key=tmdb-secret":
            return Ok(HttpResponse(200, {"images": {"secure_base_url": "https://image.tmdb.org/"}}, ""))
        if url == "https://api.mdblist.com/user?apikey=mdb-secret":
            return Ok(HttpResponse(200, {"username": "mdb_user"}, ""))
        return Err(NetworkError(message=f"Unexpected URL: {url}", url=url))

    client = FakeDoctorHttpClient(_handler)
    env = {
        "AIOSTREAMS_URL": "https://aiostreams.elfhosted.com",
        "AIOSTREAMS_PASSWORD": "pw1",
        "AIOSTREAMS_UUID": streams_uuid,
        "SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD": "enc-pw",
        "AIOMETADATA_URL": "https://aiometadata.elfhosted.com",
        "AIOMETADATA_PASSWORD": "pw2",
        "AIOMETADATA_UUID": meta_uuid,
        "SECRET_TORBOX_API_KEY": "tb-secret",
        "SECRET_RD_API_KEY": "rd-secret",
        "SECRET_ALLDEBRID_API_KEY": "ad-secret",
        "SECRET_TMDB_API_KEY": "tmdb-secret",
        "SECRET_MDBLIST_API_KEY": "mdb-secret",
        "NUVIO_ACCESS_TOKEN": "acc",
        "NUVIO_REFRESH_TOKEN": "ref",
    }
    report = run_health_checks(
        env=env,
        required_keys=["AIOSTREAMS_URL", "AIOMETADATA_URL", "SECRET_TORBOX_API_KEY", "SECRET_TMDB_API_KEY"],
        http_client=client,
    )

    assert report["overall"] == "healthy"
    assert report["summary"]["error"] == 0
    assert report["summary"]["warn"] == 0
    assert sum(report["summary"].values()) == len(report["checks"])
    categories = {chk["category"] for chk in report["checks"]}
    assert categories == {"instances", "debrid", "metadata", "clients"}
    by_id = {chk["id"]: chk for chk in report["checks"]}
    assert by_id["env.required"]["status"] == "ok"
    assert by_id["aiostreams.instance"]["status"] == "ok"
    assert by_id["aiometadata.instance"]["status"] == "ok"
    assert by_id["debrid.torbox"]["status"] == "ok"
    assert by_id["debrid.realdebrid"]["status"] == "ok"
    assert by_id["debrid.alldebrid"]["status"] == "ok"
    assert by_id["metadata.tmdb"]["status"] == "ok"
    assert by_id["metadata.mdblist"]["status"] == "ok"
    assert by_id["clients.nuvio"]["status"] == "ok"


def test_run_health_checks_detects_missing_keys_and_rejected_tokens() -> None:
    def _handler(
        _method: str, url: str, _hdrs: Mapping[str, str] | None
    ) -> Result[HttpResponse, NetworkError]:
        if "alldebrid.com" in url:
            return Ok(
                HttpResponse(
                    200,
                    {"status": "error", "error": {"code": "AUTH_BAD_APIKEY", "message": "Bad key"}},
                    "",
                )
            )
        if "torbox.app" in url:
            return Ok(HttpResponse(401, {"success": False, "detail": "Invalid token"}, ""))
        if "themoviedb.org" in url:
            return Ok(HttpResponse(401, {"status_message": "Invalid API key"}, ""))
        return Err(NetworkError(message="Connection refused", url=url))

    client = FakeDoctorHttpClient(_handler)
    report = run_health_checks(
        env={
            "AIOSTREAMS_URL": "https://aiostreams.elfhosted.com",
            "SECRET_TORBOX_API_KEY": "bad-tb",
            "SECRET_ALLDEBRID_API_KEY": "bad-ad",
            "SECRET_TMDB_API_KEY": "bad-tmdb",
        },
        required_keys=["AIOSTREAMS_URL", "AIOMETADATA_URL", "SECRET_TORBOX_API_KEY"],
        http_client=client,
    )

    assert report["overall"] == "error"
    by_id = {chk["id"]: chk for chk in report["checks"]}
    assert by_id["env.required"]["status"] == "error"
    assert "AIOMETADATA_URL" in by_id["env.required"]["detail"]
    assert by_id["aiostreams.server"]["status"] == "error"
    assert by_id["debrid.torbox"]["status"] == "error"
    assert by_id["debrid.alldebrid"]["status"] == "error"
    assert by_id["metadata.tmdb"]["status"] == "error"


def test_run_health_checks_offline_mode_skips_network_and_reports_warnings() -> None:
    def _never_called(
        _method: str, url: str, _hdrs: Mapping[str, str] | None
    ) -> Result[HttpResponse, NetworkError]:
        raise AssertionError(f"Network should not be probed when probe_network=False: {url}")

    client = FakeDoctorHttpClient(_never_called)
    report = run_health_checks(
        env={
            "AIOSTREAMS_URL": "https://aiostreams.elfhosted.com",
            "AIOMETADATA_URL": "https://aiometadata.elfhosted.com",
            "SECRET_TMDB_API_KEY": "tmdb-key",
            "SECRET_EASYNEWS_USERNAME": "only-user",
            "NUVIO_EMAIL": "demo@example.com",
        },
        required_keys=["AIOSTREAMS_URL", "AIOMETADATA_URL"],
        http_client=client,
        probe_network=False,
    )

    assert client.calls == []
    assert report["overall"] == "warn"
    assert report["summary"]["error"] == 0
    assert report["summary"]["warn"] == 2
    assert report["summary"]["skipped"] >= 3
    by_id = {chk["id"]: chk for chk in report["checks"]}
    assert by_id["debrid.easynews"]["status"] == "warn"
    assert by_id["clients.nuvio"]["status"] == "warn"
