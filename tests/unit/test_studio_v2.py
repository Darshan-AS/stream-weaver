"""Unit tests for Stream Weaver Studio v2 (`stream_weaver.studio`) routes, static assets, and power flows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.result import Ok, Result
from stream_weaver.core.tenant import TenantPaths
from stream_weaver.effects.http import HttpResponse
from stream_weaver.studio import StudioDispatcher

runner = CliRunner()
REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "home"
SHARED_LIB = REPO / "library"


class _FakeStudioV2Http:
    """Stubs upstream HTTP endpoints for Studio v2 Doctor, Manifest Inspect, Previews, Warm & Push."""

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
        _ = method, headers, json_body, raw_body, timeout

        # Custom addon manifest inspection
        if url == "https://custom.addon.example/manifest.json":
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={
                        "id": "org.example.customstreams",
                        "name": "Custom Streams Pro",
                        "version": "2.4.0",
                        "description": "Fast custom stream provider",
                        "resources": ["stream", {"name": "subtitles"}],
                        "types": ["movie", "series"],
                        "catalogs": [{"id": "top", "type": "movie"}],
                    },
                    text="",
                )
            )

        # Doctor server & API probes
        if url.endswith("/manifest.json") and ("streams.example.org" in url or "meta.example.org" in url or "elfhosted.com" in url):
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={"id": "org.aio.test", "name": "AIO Test", "version": "1.0.0", "catalogs": []},
                    text="",
                )
            )
        if "api.themoviedb.org/3/configuration" in url:
            return Ok(HttpResponse(status_code=200, data={"images": {}}, text=""))
        if "api.torbox.app/v1/api/user/me" in url:
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={"success": True, "data": {"email": "studio@example.org"}},
                    text="",
                )
            )
        if "api.mdblist.com/user" in url:
            return Ok(
                HttpResponse(status_code=200, data={"username": "studio_tester"}, text="")
            )

        # Live AIOMetadata catalog preview
        if "/catalog/movie/tmdb.trending.json" in url:
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={
                        "metas": [
                            {
                                "id": "tt15239678",
                                "name": "Dune: Part Two",
                                "releaseInfo": "2024",
                                "poster": "https://image.tmdb.org/t/p/w500/dune2.jpg",
                                "imdbRating": "8.6",
                                "type": "movie",
                            }
                        ]
                    },
                    text="",
                )
            )

        # Catalog pre-warm / generic catalog probes
        if "/catalog/" in url and url.endswith(".json"):
            return Ok(HttpResponse(status_code=200, data={"metas": [{"id": "tt1"}]}, text=""))

        # Provisioning & Push endpoints for AIOStreams / AIOMetadata
        if url.endswith("/api/v1/user"):
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={
                        "data": {
                            "uuid": "00000000-0000-4000-8000-000000000081",
                            "encryptedPassword": "enc-v2-password",
                        }
                    },
                    text="",
                )
            )
        if "/api/config/save" in url or "/api/config/" in url:
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={"userUUID": "00000000-0000-4000-8000-000000000082", "status": "ok"},
                    text="",
                )
            )
        if url.endswith("manifest.json"):
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={"id": "org.generic.addon", "name": "Generic", "version": "1.0.0", "catalogs": []},
                    text="",
                )
            )

        raise AssertionError(f"Unexpected Studio v2 upstream URL: {url}")


def _make_local_dispatcher(tmp_path: Path) -> tuple[Path, StudioDispatcher]:
    tenant_dir = tmp_path / "studio-v2-tenant"
    res = runner.invoke(app, ["init", str(tenant_dir), "--from", str(EXAMPLE)])
    assert res.exit_code == 0, res.output
    dispatcher = StudioDispatcher(
        tenant_paths=TenantPaths(tenant_dir),
        shared_library_root=SHARED_LIB,
        http_client_factory=_FakeStudioV2Http,
    )
    return tenant_dir, dispatcher


def test_static_assets_served_and_path_traversal_rejected(tmp_path: Path) -> None:
    _, dispatcher = _make_local_dispatcher(tmp_path)

    index_resp = dispatcher.dispatch("GET", "/")
    assert index_resp.status == 200
    assert index_resp.content_type == "text/html; charset=utf-8"
    html_text = index_resp.body.decode("utf-8")
    assert "Stream Weaver Studio" in html_text
    assert f'<meta name="sw-csrf-token" content="{dispatcher.csrf_token}" />' in html_text
    assert '/static/styles.css' in html_text
    assert '/static/app.js' in html_text

    css_resp = dispatcher.dispatch("GET", "/static/styles.css")
    assert css_resp.status == 200
    assert css_resp.content_type == "text/css; charset=utf-8"
    assert b"--accent" in css_resp.body

    for js_asset in ("app.js", "wizard.js", "home.js", "streams.js", "aiomanager.js", "nuvio.js", "keys.js"):
        js_resp = dispatcher.dispatch("GET", f"/static/{js_asset}")
        assert js_resp.status == 200, js_asset
        assert js_resp.content_type == "application/javascript; charset=utf-8"
        assert len(js_resp.body) > 100

    # Rejects path traversal attempts
    for malicious in (
        "/static/../server.py",
        "/static/%2e%2e/server.py",
        "/static/..%2F__init__.py",
        "/static/nested/../../server.py",
        "/static//etc/passwd",
        "/static/..\\server.py",
        "/static",
    ):
        bad_resp = dispatcher.dispatch("GET", malicious)
        assert bad_resp.status == 403, f"Expected 403 for {malicious}, got {bad_resp.status}"

    missing_resp = dispatcher.dispatch("GET", "/static/does-not-exist.js")
    assert missing_resp.status == 404


@pytest.mark.parametrize(
    "api_route",
    [
        "/api/profile",
        "/api/doctor",
        "/api/inspect-manifest",
        "/api/preview-catalog",
        "/api/raw-doc",
        "/api/warm",
        "/api/push-stream",
    ],
)
def test_csrf_enforced_on_all_new_v2_api_routes(tmp_path: Path, api_route: str) -> None:
    _, dispatcher = _make_local_dispatcher(tmp_path)

    blocked = dispatcher.dispatch("POST", api_route, b"{}", enforce_csrf=True)
    assert blocked.status == 403

    blocked_cross_origin = dispatcher.dispatch(
        "POST",
        api_route,
        b"{}",
        headers={
            "X-Studio-Token": dispatcher.csrf_token,
            "Host": "127.0.0.1:8787",
            "Origin": "https://attacker.example",
        },
        enforce_csrf=True,
    )
    assert blocked_cross_origin.status == 403


def test_household_multi_profile_and_power_mutations(tmp_path: Path) -> None:
    tenant_dir, dispatcher = _make_local_dispatcher(tmp_path)

    # 1. Create a Kids profile with its own dedicated AIOMetadata instance
    create_resp = dispatcher.dispatch(
        "POST",
        "/api/profile",
        json.dumps(
            {
                "action": "create",
                "name": "kids",
                "displayName": "Kids Room",
                "preset": "kids",
                "dedicatedMetadataInstance": True,
                "emoji": "🧸",
                "accentColor": "#f59e0b",
            }
        ).encode("utf-8"),
    )
    assert create_resp.status == 200, create_resp.body
    created_state = json.loads(create_resp.body)["state"]
    prof_names = [p["name"] for p in created_state["profiles"]]
    assert prof_names == ["main", "kids"]
    assert (tenant_dir / "aiometadata.kids.json").is_file()

    # 2. Apply v2 power controls across AIOStreams, AIOMetadata, AIOManager, and Standalone Addons
    power_mut = {
        "mutation": {
            "debridServices": [
                {"id": "torbox", "enabled": True, "priority": 1},
                {"id": "realdebrid", "enabled": True, "priority": 2},
                {"id": "easynews", "enabled": True, "priority": 3},
            ],
            "customStreamAddons": [
                {
                    "instanceId": "cst_pro_1",
                    "name": "Custom Streams Pro",
                    "manifestUrl": "https://custom.addon.example/manifest.json",
                    "enabled": True,
                    "group": "fallback",
                    "resources": ["stream"],
                    "timeout": 9000,
                }
            ],
            "streamFormatter": "compact_tv",
            "advancedStreamFilters": {
                "maxSizeGb": 65,
                "minSeeders": 5,
                "hdrPolicy": "prefer_dv_hdr",
                "preferredCodecs": ["AV1", "HEVC"],
                "excludeQualities": ["CAM", "TS", "3D"],
                "excludeKeywords": ["hardcoded"],
                "rpdbPostersEnabled": True,
            },
            "metadataPower": {
                "movieProvider": "tmdb",
                "seriesProvider": "tvdb",
                "animeProvider": "mal",
                "aiSearchEnabled": True,
                "aiSearchProvider": "gemini",
                "posterProvider": "rpdb",
                "catalogOverrides": [
                    {"id": "tmdb.trending", "type": "movie", "name": "Trending Cinema", "enabled": True, "showInHome": True}
                ],
            },
            "aiomanagerPower": {
                "autopilotEnabled": True,
                "syncIntervalMinutes": 30,
            },
            "profile": {
                "name": "main",
                "extraAddons": [
                    {
                        "name": "bingecat",
                        "transportUrl": "https://bingecat.com/stremio/{{SECRET_BINGECAT_TOKEN}}/manifest.json",
                        "customName": "BingeCat",
                        "enabled": True,
                    },
                    {
                        "name": "cinemeta",
                        "transportUrl": "https://v3-cinemeta.strem.io",
                        "customName": "Cinemeta",
                        "enabled": True,
                    },
                    {
                        "name": "custom-extra",
                        "transportUrl": "https://custom.addon.example/manifest.json",
                        "customName": "Custom Standalone",
                        "enabled": True,
                    },
                ],
            },
        }
    }
    mut_resp = dispatcher.dispatch("POST", "/api/mutate", json.dumps(power_mut).encode("utf-8"))
    assert mut_resp.status == 200, mut_resp.body
    snap = json.loads(mut_resp.body)["state"]

    assert [d["id"] for d in snap["debridServices"]["active"]] == ["torbox", "realdebrid", "easynews"]
    assert snap["streamFormatter"]["active"] == "compact_tv"
    assert snap["advancedStreamFilters"]["maxSizeGb"] == 65
    assert snap["advancedStreamFilters"]["hdrPolicy"] == "prefer_dv_hdr"
    assert snap["customStreamAddons"][0]["instanceId"] == "cst_pro_1"
    assert snap["metadataPower"]["aiSearchEnabled"] is True
    assert snap["aiomanagerPower"]["autopilotEnabled"] is True
    assert snap["aiomanagerPower"]["syncIntervalMinutes"] == 30

    # Cinemeta metadata is preserved
    main_prof_doc = json.loads((tenant_dir / "profiles" / "main" / "profile.json").read_text("utf-8"))
    cinemeta_entry = next(a for a in main_prof_doc["addons"]["extra"] if a["name"] == "cinemeta")
    assert cinemeta_entry["metadata"]["cinemetaConfig"]["removeSearch"] is True
    assert runner.invoke(app, ["validate", "--dir", str(tenant_dir)]).exit_code == 0

    # 3. Delete Kids profile and ensure last profile deletion is blocked
    del_resp = dispatcher.dispatch(
        "POST", "/api/profile", json.dumps({"action": "delete", "name": "kids"}).encode("utf-8")
    )
    assert del_resp.status == 200
    assert [p["name"] for p in json.loads(del_resp.body)["state"]["profiles"]] == ["main"]

    refuse_last = dispatcher.dispatch(
        "POST", "/api/profile", json.dumps({"action": "delete", "name": "main"}).encode("utf-8")
    )
    assert refuse_last.status == 400


def test_doctor_manifest_preview_raw_doc_warm_and_ndjson_push_stream(tmp_path: Path) -> None:
    tenant_dir, dispatcher = _make_local_dispatcher(tmp_path)

    # 1. Inspect arbitrary stremio:// addon manifest
    insp_resp = dispatcher.dispatch(
        "POST",
        "/api/inspect-manifest",
        json.dumps({"url": "stremio://custom.addon.example"}).encode("utf-8"),
    )
    assert insp_resp.status == 200, insp_resp.body
    insp_data = json.loads(insp_resp.body)
    assert insp_data["manifest"]["id"] == "org.example.customstreams"
    assert "stream" in insp_data["resources"]

    # 2. Offline sample catalog preview + Live AIOMetadata catalog preview
    sample_prev = dispatcher.dispatch(
        "POST",
        "/api/preview-catalog",
        json.dumps({"spec": {"catalogId": "tmdb.trending", "type": "movie"}}).encode("utf-8"),
    )
    assert sample_prev.status == 200
    sample_data = json.loads(sample_prev.body)
    assert sample_data["source"] == "sample"
    assert len(sample_data["items"]) == 10

    live_prev = dispatcher.dispatch(
        "POST",
        "/api/preview-catalog",
        json.dumps(
            {
                "spec": {"catalogId": "tmdb.trending", "type": "movie"},
                "pendingEnv": {
                    "AIOMETADATA_URL": "https://meta.example.org",
                    "AIOMETADATA_UUID": "00000000-0000-4000-8000-000000000082",
                },
            }
        ).encode("utf-8"),
    )
    assert live_prev.status == 200
    live_data = json.loads(live_prev.body)
    assert live_data["source"] == "aiometadata"
    assert live_data["items"][0]["title"] == "Dune: Part Two"

    # 3. Live Doctor check with Wizard Step 2 pendingEnv
    doc_resp = dispatcher.dispatch(
        "POST",
        "/api/doctor",
        json.dumps(
            {
                "pendingEnv": {
                    "AIOSTREAMS_URL": "https://streams.example.org",
                    "AIOMETADATA_URL": "https://meta.example.org",
                    "SECRET_TORBOX_API_KEY": "tb-live-key",
                    "SECRET_TMDB_API_KEY": "tmdb-live-key",
                    "SECRET_MDBLIST_API_KEY": "mdb-live-key",
                }
            }
        ).encode("utf-8"),
    )
    assert doc_resp.status == 200, doc_resp.body
    doc_data = json.loads(doc_resp.body)
    by_id = {c["id"]: c["status"] for c in doc_data["checks"]}
    assert by_id["debrid.torbox"] == "ok"
    assert by_id["metadata.tmdb"] == "ok"
    assert by_id["metadata.mdblist"] == "ok"

    # 4. Raw JSON Escape Hatch auto-harvests & scrubs pasted plaintext secrets!
    get_raw = dispatcher.dispatch(
        "POST", "/api/raw-doc", json.dumps({"action": "get", "doc": "aiostreams"}).encode("utf-8")
    )
    assert get_raw.status == 200
    raw_payload = json.loads(get_raw.body)
    streams_obj = json.loads(raw_payload["content"])
    for svc in streams_obj.get("services", []):
        if svc.get("id") == "torbox":
            svc["credentials"]["apiKey"] = "pasted-plaintext-torbox-secret-42"

    save_raw = dispatcher.dispatch(
        "POST",
        "/api/raw-doc",
        json.dumps(
            {
                "action": "save",
                "doc": "aiostreams",
                "content": json.dumps(streams_obj),
            }
        ).encode("utf-8"),
    )
    assert save_raw.status == 200, save_raw.body
    disk_streams = (tenant_dir / "aiostreams.json").read_text(encoding="utf-8")
    assert "pasted-plaintext-torbox-secret-42" not in disk_streams
    assert "{{SECRET_TORBOX_API_KEY}}" in disk_streams
    assert "SECRET_TORBOX_API_KEY=pasted-plaintext-torbox-secret-42" in (
        tenant_dir / ".env"
    ).read_text(encoding="utf-8")

    # Invalid JSON rejected cleanly
    bad_raw = dispatcher.dispatch(
        "POST",
        "/api/raw-doc",
        json.dumps({"action": "save", "doc": "aiostreams", "content": "{broken-json"}).encode("utf-8"),
    )
    assert bad_raw.status == 400

    # 5. Pre-warm Home catalog rows
    warm_resp = dispatcher.dispatch("POST", "/api/warm", json.dumps({"workers": 4}).encode("utf-8"))
    assert warm_resp.status == 200, warm_resp.body
    warm_data = json.loads(warm_resp.body)
    assert warm_data["ok"] is True
    assert "warmed" in warm_data
    assert "total" in warm_data

    # 6. NDJSON Streaming Push (`POST /api/push-stream`)
    stream_resp = dispatcher.dispatch(
        "POST",
        "/api/push-stream",
        json.dumps(
            {
                "target": "aiostreams",
                "create": True,
                "mutation": {
                    "enabledPacks": ["core_debrid"],
                    "envUpdates": {
                        "AIOSTREAMS_URL": "https://streams.example.org",
                        "AIOSTREAMS_PASSWORD": "pw-stream-push",
                        "SECRET_TMDB_API_KEY": "tmdb-v3",
                        "SECRET_TMDB_ACCESS_TOKEN": "tmdb-v4",
                        "SECRET_TVDB_API_KEY": "tvdb-v4",
                    },
                },
            }
        ).encode("utf-8"),
    )
    assert stream_resp.status == 200, stream_resp.body
    assert stream_resp.content_type == "application/x-ndjson"
    ndjson_lines = [
        json.loads(line)
        for line in stream_resp.body.decode("utf-8").splitlines()
        if line.strip()
    ]
    assert len(ndjson_lines) >= 2
    assert any(evt["type"] == "step" for evt in ndjson_lines)
    done_evt = ndjson_lines[-1]
    assert done_evt["type"] == "done"
    assert done_evt["ok"] is True
    assert done_evt["state"]["tenant"]["aiostreamsProvisioned"] is True


def test_stateless_push_stream_and_profile_return_updated_bundle() -> None:
    dispatcher = StudioDispatcher(
        tenant_paths=None,
        template_root=EXAMPLE,
        shared_library_root=SHARED_LIB,
        http_client_factory=_FakeStudioV2Http,
    )
    init_bundle = json.loads(dispatcher.dispatch("GET", "/api/state").body)["bundle"]

    prof_resp = dispatcher.dispatch(
        "POST",
        "/api/profile",
        json.dumps(
            {
                "bundle": init_bundle,
                "action": "create",
                "name": "family",
                "preset": "minimal",
            }
        ).encode("utf-8"),
    )
    assert prof_resp.status == 200, prof_resp.body
    prof_data = json.loads(prof_resp.body)
    assert "profiles/family/profile.json" in prof_data["bundle"]["files"]

    streamed_chunks: list[bytes] = []
    push_resp = dispatcher.dispatch_push_stream(
        json.dumps(
            {
                "bundle": prof_data["bundle"],
                "target": "aiostreams",
                "create": True,
                "mutation": {
                    "enabledPacks": ["core_debrid"],
                    "envUpdates": {
                        "AIOSTREAMS_URL": "https://streams.example.org",
                        "AIOSTREAMS_PASSWORD": "stateless-pw",
                        "SECRET_TORBOX_API_KEY": "stateless-tb",
                        "SECRET_TMDB_API_KEY": "tmdb-v3",
                        "SECRET_TMDB_ACCESS_TOKEN": "tmdb-v4",
                        "SECRET_TVDB_API_KEY": "tvdb-v4",
                    },
                },
            }
        ).encode("utf-8"),
        on_chunk=streamed_chunks.append,
    )
    assert push_resp.status == 200
    assert len(streamed_chunks) >= 2
    last_frame = json.loads(streamed_chunks[-1].decode("utf-8"))
    assert last_frame["type"] == "done"
    assert last_frame["bundle"]["env"]["AIOSTREAMS_UUID"] == "00000000-0000-4000-8000-000000000081"
