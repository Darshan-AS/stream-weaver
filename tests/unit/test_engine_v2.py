"""Unit tests for Workspace Engine v2 (`stream_weaver.engine`) — profiles, power controls, previews, raw docs, and unified sync."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.tenant import TenantPaths
from stream_weaver.effects.http import HttpResponse
from stream_weaver.engine import (
    DiffReport,
    apply_debrid_and_packs,
    apply_studio_mutation,
    diff_workspace,
    inspect_raw_document,
    inspect_tenant_state,
    inspect_workspace_addon_manifest,
    manage_studio_profile,
    preview_catalog_items,
    pull_workspace,
    push_workspace,
    save_raw_document,
    verify_workspace_health,
    warm_workspace_catalogs,
)

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"


class _FakeHttp:
    def __init__(
        self,
        handler: Callable[[str, str, Mapping[str, str], Any], Result[HttpResponse, NetworkError]],
    ) -> None:
        self._handler = handler
        self.urls: list[str] = []

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
        _ = raw_body, timeout
        self.urls.append(url)
        return self._handler(method, url, dict(headers or {}), json_body)


def test_multi_profile_household_manager_presets_dedicated_instance_and_delete(tmp_path: Path) -> None:
    t = tmp_path / "household"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    paths = TenantPaths(t)

    # Reject invalid slug
    bad_slug = manage_studio_profile(paths, {"action": "create", "name": "Bad Slug!", "preset": "minimal"})
    assert isinstance(bad_slug, Err)

    # Create Kids profile with dedicated AIOMetadata instance
    kids_res = manage_studio_profile(
        paths,
        {
            "action": "create",
            "name": "kids",
            "displayName": "Kids Room",
            "preset": "kids",
            "dedicatedMetadataInstance": True,
            "emoji": "🧸",
            "accentColor": "#f59e0b",
            "nuvioProfileName": "Kids",
            "stremioEnabled": False,
        },
    )
    assert isinstance(kids_res, Ok), kids_res
    snap = kids_res.value
    assert [p["name"] for p in snap["profiles"]] == ["main", "kids"]
    assert "kids" in snap["tenant"]["aiometadataInstances"]

    kids_prof = snap["profiles"][1]
    assert kids_prof["aiometadataInstance"] == "kids"
    assert kids_prof["emoji"] == "🧸"
    assert kids_prof["nuvio"]["profileName"] == "Kids"
    folder_ids = {f["id"] for c in kids_prof["collections"] for f in c["folders"]}
    assert "fld-genre-animation" in folder_ids
    assert "fld-genre-family" in folder_ids
    assert "fld-studio-pixar" in folder_ids
    assert "fld-franchise-ghibli" in folder_ids
    assert "fld-genre-horror" not in folder_ids
    assert "fld-shudder" not in folder_ids

    assert (t / "aiometadata.kids.json").is_file()
    assert "AIOMETADATA_KIDS_PASSWORD=" in (t / ".env.example").read_text(encoding="utf-8")
    env_by_key = {e["key"]: e for e in snap["env"]}
    assert env_by_key["AIOMETADATA_KIDS_PASSWORD"]["required"] is True
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0

    # Create Minimal, Standard & Clone profiles
    min_res = manage_studio_profile(paths, {"action": "create", "name": "guest", "preset": "minimal"})
    assert isinstance(min_res, Ok), min_res
    assert len(min_res.value["profiles"][2]["collections"]) == 3

    std_res = manage_studio_profile(paths, {"action": "create", "name": "roommate", "preset": "standard"})
    assert isinstance(std_res, Ok), std_res
    assert len(std_res.value["profiles"][3]["collections"]) == 7

    clone_res = manage_studio_profile(
        paths, {"action": "create", "name": "partner", "preset": "clone", "sourceProfile": "main"}
    )
    assert isinstance(clone_res, Ok), clone_res
    assert len(clone_res.value["profiles"][4]["collections"]) == 7
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0

    # Delete Kids, Guest, Roommate, Partner; verify dedicated instance cleanup & refusal on last profile
    del_kids = manage_studio_profile(paths, {"action": "delete", "name": "kids"})
    assert isinstance(del_kids, Ok), del_kids
    assert not (t / "profiles" / "kids").exists()
    assert not (t / "aiometadata.kids.json").exists()
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0

    assert isinstance(manage_studio_profile(paths, {"action": "delete", "name": "guest"}), Ok)
    assert isinstance(manage_studio_profile(paths, {"action": "delete", "name": "roommate"}), Ok)
    assert isinstance(manage_studio_profile(paths, {"action": "delete", "name": "partner"}), Ok)

    last_del = manage_studio_profile(paths, {"action": "delete", "name": "main"})
    assert isinstance(last_del, Err)
    assert "last remaining profile" in str(last_del.error)


def test_power_controls_and_onboarding_round_trip_and_patch_safety(tmp_path: Path) -> None:
    t = tmp_path / "power-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    paths = TenantPaths(t)

    init_res = inspect_tenant_state(paths)
    assert isinstance(init_res, Ok), init_res
    init_snap = init_res.value
    assert init_snap["onboarding"]["needsSetup"] is True
    assert init_snap["debridServices"]["active"][0]["id"] == "torbox"

    mut_res = apply_studio_mutation(
        paths,
        {
            "debridServices": ["torbox", "realdebrid", "easynews"],
            "customStreamAddons": [
                {
                    "name": "StremThru",
                    "manifestUrl": "https://stream-weaver.dev/stremthru/manifest.json",
                    "group": "primary",
                    "resources": ["stream"],
                    "timeout": 9000,
                }
            ],
            "streamFormatter": "compact_tv",
            "advancedStreamFilters": {
                "maxSizeGb": 35,
                "excludeQualities": ["CAM", "TS", "3D"],
                "excludeKeywords": ["sample"],
                "preferredCodecs": ["HEVC", "AV1"],
                "hdrPolicy": "prefer_dv_hdr",
                "minSeeders": 5,
                "rpdbPostersEnabled": True,
            },
            "metadataPower": {
                "movieProvider": "tvdb",
                "aiSearchEnabled": True,
                "aiSearchProvider": "openrouter",
                "posterProvider": "topposters",
                "catalogOverrides": [
                    {"id": "mal.schedule", "type": "anime", "enabled": True, "showInHome": True}
                ],
            },
            "aiomanagerPower": {
                "autopilotEnabled": True,
                "syncIntervalMinutes": 30,
                "webhookUrl": "https://stream-weaver.dev/webhook",
            },
            "profile": {
                "name": "main",
                "extraAddons": [
                    {
                        "name": "bingecat",
                        "transportUrl": "https://bingecat.com/stremio/{{SECRET_BINGECAT_TOKEN}}/manifest.json",
                        "customName": "BingeCat",
                    },
                    {
                        "name": "cinemeta",
                        "transportUrl": "https://v3-cinemeta.strem.io",
                        "customName": "Cinemeta",
                    },
                    {
                        "name": "anime-kitsu",
                        "transportUrl": "https://stream-weaver.dev/kitsu/manifest.json",
                        "customName": "Anime Kitsu",
                    },
                ],
            },
        },
    )
    assert isinstance(mut_res, Ok), mut_res
    snap = mut_res.value

    assert [s["id"] for s in snap["debridServices"]["active"]] == ["torbox", "realdebrid", "easynews"]
    assert len(snap["customStreamAddons"]) == 1
    assert snap["customStreamAddons"][0]["name"] == "StremThru"
    assert snap["streamFormatter"]["active"] == "compact_tv"
    assert snap["advancedStreamFilters"]["maxSizeGb"] == 35
    assert snap["advancedStreamFilters"]["hdrPolicy"] == "prefer_dv_hdr"
    assert snap["advancedStreamFilters"]["rpdbPostersEnabled"] is True
    assert snap["metadataPower"]["movieProvider"] == "tvdb"
    assert snap["metadataPower"]["aiSearchEnabled"] is True
    assert snap["metadataPower"]["aiSearchProvider"] == "openrouter"
    assert snap["metadataPower"]["posterProvider"] == "topposters"
    assert snap["aiomanagerPower"]["autopilotEnabled"] is True
    assert snap["aiomanagerPower"]["syncIntervalMinutes"] == 30
    assert snap["aiomanagerPower"]["webhookLinked"] is True

    # Cinemeta config preserved + new extra addon added
    extras_by_name = {a["name"]: a for a in snap["profiles"][0]["extraAddons"]}
    assert extras_by_name["cinemeta"]["metadata"]["cinemetaConfig"]["removeSearch"] is True
    assert "anime-kitsu" in extras_by_name

    # Dynamic power keys marked required
    env_by_key = {e["key"]: e for e in snap["env"]}
    for req_k in (
        "SECRET_TORBOX_API_KEY",
        "SECRET_RD_API_KEY",
        "SECRET_EASYNEWS_USERNAME",
        "SECRET_EASYNEWS_PASSWORD",
        "SECRET_OPENROUTER_API_KEY",
        "SECRET_TOP_POSTER_API_KEY",
    ):
        assert env_by_key[req_k]["required"] is True, req_k

    # Patch-safe: mutating ONLY displayName keeps all power settings intact
    patch_res = apply_studio_mutation(paths, {"profile": {"name": "main", "displayName": "CinemaPro"}})
    assert isinstance(patch_res, Ok)
    after = patch_res.value
    assert [s["id"] for s in after["debridServices"]["active"]] == ["torbox", "realdebrid", "easynews"]
    assert after["streamFormatter"]["active"] == "compact_tv"
    assert after["metadataPower"]["aiSearchProvider"] == "openrouter"

    val = runner.invoke(app, ["validate", "--dir", str(t)])
    assert val.exit_code == 0, val.output


def test_live_doctor_manifest_inspect_catalog_preview_and_warm(tmp_path: Path) -> None:
    t = tmp_path / "preview-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    paths = TenantPaths(t)

    # 1. Offline sample catalog preview (zero network calls)
    sample_res = preview_catalog_items(paths, {"catalogId": "tmdb.trending", "type": "movie"})
    assert isinstance(sample_res, Ok)
    assert sample_res.value["source"] == "sample"
    assert len(sample_res.value["items"]) == 10

    meta_uuid = "00000000-0000-4000-8000-000000000012"

    def _handler(
        _method: str, url: str, _hdrs: Mapping[str, str], _body: Any
    ) -> Result[HttpResponse, NetworkError]:
        if url == "https://stream-weaver.dev/addon/manifest.json":
            return Ok(
                HttpResponse(
                    200,
                    {"id": "org.sw.demo", "name": "Demo Addon", "version": "1.2.0", "resources": ["stream"], "types": ["movie"]},
                    "",
                )
            )
        if "api.themoviedb.org/3/trending/series" in url or "api.themoviedb.org/3/trending/tv" in url:
            return Ok(
                HttpResponse(
                    200,
                    {
                        "results": [
                            {
                                "id": 999,
                                "name": "TMDB Live Show",
                                "first_air_date": "2025-01-10",
                                "poster_path": "/live.jpg",
                                "vote_average": 8.4,
                            }
                        ]
                    },
                    "",
                )
            )
        if f"/stremio/{meta_uuid}/catalog/movie/tmdb.trending.json" in url:
            return Ok(
                HttpResponse(
                    200,
                    {
                        "metas": [
                            {
                                "id": "tt0000001",
                                "name": "Provisioned Movie",
                                "releaseInfo": "2024",
                                "poster": "https://image.tmdb.org/t/p/w500/prov.jpg",
                                "imdbRating": "8.9",
                                "type": "movie",
                            }
                        ]
                    },
                    "",
                )
            )
        if url.endswith("/manifest.json"):
            return Ok(
                HttpResponse(
                    200,
                    {
                        "id": "com.aiometadata",
                        "name": "AIOMetadata",
                        "version": "1.0.0",
                        "catalogs": [{"id": "tmdb.trending", "type": "movie"}],
                    },
                    "",
                )
            )
        if "/catalog/" in url and url.endswith(".json"):
            return Ok(HttpResponse(200, {"metas": [{"id": "tt1", "name": "Warm"}]}, ""))
        if "api.themoviedb.org/3/configuration" in url:
            return Ok(HttpResponse(200, {"images": {}}, ""))
        if "api.torbox.app/v1/api/user/me" in url:
            return Ok(HttpResponse(200, {"success": True, "data": {"email": "u@example.com"}}, ""))
        return Err(NetworkError(message=f"Unexpected URL {url}", url=url))

    fake_http = _FakeHttp(_handler)

    # 2. Manifest inspect (`stremio://`)
    mf_res = inspect_workspace_addon_manifest("stremio://stream-weaver.dev/addon", http_client=fake_http)
    assert isinstance(mf_res, Ok)
    assert mf_res.value["id"] == "org.sw.demo"

    # 3. TMDB preview fallback when AIOMetadata UUID is not provisioned yet
    tmdb_prev = preview_catalog_items(
        paths,
        {"catalogId": "tmdb.trending", "type": "series"},
        pending_env={"SECRET_TMDB_API_KEY": "tmdb-k"},
        http_client=fake_http,
    )
    assert isinstance(tmdb_prev, Ok)
    assert tmdb_prev.value["source"] == "tmdb"
    assert tmdb_prev.value["items"][0]["title"] == "TMDB Live Show"

    # 4. Live AIOMetadata catalog preview when provisioned
    aio_prev = preview_catalog_items(
        paths,
        {"catalogId": "tmdb.trending", "type": "movie"},
        pending_env={
            "AIOMETADATA_URL": "https://aiometadata.elfhosted.com",
            "AIOMETADATA_UUID": meta_uuid,
        },
        http_client=fake_http,
    )
    assert isinstance(aio_prev, Ok)
    assert aio_prev.value["source"] == "aiometadata"
    assert aio_prev.value["items"][0]["title"] == "Provisioned Movie"

    # 5. Live health doctor with pending_env
    doc_res = verify_workspace_health(
        paths,
        pending_env={
            "AIOSTREAMS_URL": "https://aiostreams.elfhosted.com",
            "AIOSTREAMS_PASSWORD": "pw",
            "AIOMETADATA_URL": "https://aiometadata.elfhosted.com",
            "AIOMETADATA_PASSWORD": "pw",
            "SECRET_TORBOX_API_KEY": "tb-k",
            "SECRET_TMDB_API_KEY": "tmdb-k",
            "SECRET_TMDB_ACCESS_TOKEN": "tmdb-v4",
            "SECRET_TVDB_API_KEY": "tvdb-k",
            "SECRET_MDBLIST_API_KEY": "mdb-k",
            "SECRET_TRAKT_TOKEN_ID": "trakt-k",
            "SECRET_SUBSOURCE_API_KEY": "sub-k",
            "SECRET_ITSOUT_TOKEN": "ito-k",
            "SECRET_BINGECAT_TOKEN": "bc-k",
        },
        probe_network=False,
        http_client=fake_http,
    )
    assert isinstance(doc_res, Ok)
    assert doc_res.value["summary"]["error"] == 0

    # 6. Warm catalogs
    (t / ".env").write_text(
        f"AIOMETADATA_URL=https://aiometadata.elfhosted.com\nAIOMETADATA_UUID={meta_uuid}\n",
        encoding="utf-8",
    )
    warm_res = warm_workspace_catalogs(paths, workers=4, http_client=fake_http)
    assert isinstance(warm_res, Ok), warm_res
    assert warm_res.value["warmed"] >= 1


def test_raw_json_escape_hatch_harvests_secrets_validates_and_rolls_back(tmp_path: Path) -> None:
    t = tmp_path / "raw-doc-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    paths = TenantPaths(t)

    inspected = inspect_raw_document(paths, "aiostreams")
    assert isinstance(inspected, Ok)
    doc_keys = [d["key"] for d in inspected.value["availableDocs"]]
    assert "aiostreams" in doc_keys
    assert "profile:main" in doc_keys

    # Paste a raw secret inside aiostreams.json -> auto-harvested to .env & scrubbed on disk
    raw_obj = json.loads(inspected.value["content"])
    raw_obj["tmdbApiKey"] = "pasted_super_secret_tmdb_key_999"
    saved = save_raw_document(paths, "aiostreams", json.dumps(raw_obj))
    assert isinstance(saved, Ok), saved
    disk_streams = (t / "aiostreams.json").read_text(encoding="utf-8")
    assert "pasted_super_secret_tmdb_key_999" not in disk_streams
    assert "{{SECRET_TMDB_API_KEY}}" in disk_streams
    assert "SECRET_TMDB_API_KEY=pasted_super_secret_tmdb_key_999" in (t / ".env").read_text(encoding="utf-8")

    # Malformed JSON rejected cleanly
    assert isinstance(save_raw_document(paths, "aiostreams", "{not-json"), Err)

    # Broken recipe ref in profile:main rolls back atomically
    orig_prof_bytes = (t / "profiles" / "main" / "profile.json").read_bytes()
    prof_obj = json.loads(orig_prof_bytes.decode("utf-8"))
    prof_obj["collections"] = [{"ref": "col-does-not-exist"}]
    bad_save = save_raw_document(paths, "profile:main", json.dumps(prof_obj))
    assert isinstance(bad_save, Err)
    assert (t / "profiles" / "main" / "profile.json").read_bytes() == orig_prof_bytes


def test_unified_push_pull_diff_and_apply_debrid_and_packs(tmp_path: Path) -> None:
    t = tmp_path / "sync-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    paths = TenantPaths(t)

    pack_res = apply_debrid_and_packs(
        paths,
        debrid="torbox",
        enabled_packs=["core_debrid", "core_subtitles"],
        max_resolution="1080p",
    )
    assert isinstance(pack_res, Ok), pack_res
    assert apply_studio_mutation(paths, {"profile": {"name": "main", "meta": {"enabled": False}}}).is_ok()

    (t / ".env").write_text(
        "AIOSTREAMS_URL=https://aiostreams.elfhosted.com\n"
        "AIOSTREAMS_PASSWORD=pw1\n"
        "AIOMETADATA_URL=https://aiometadata.elfhosted.com\n"
        "AIOMETADATA_PASSWORD=pw2\n"
        "SECRET_TORBOX_API_KEY=tb-tok\n"
        "SECRET_TMDB_API_KEY=tmdb-v3\n"
        "SECRET_TMDB_ACCESS_TOKEN=tmdb-v4\n"
        "SECRET_TVDB_API_KEY=tvdb-v4\n"
        "SECRET_TRAKT_TOKEN_ID=trakt-tok\n"
        "SECRET_BINGECAT_TOKEN=bc-tok\n",
        encoding="utf-8",
    )

    streams_uuid = "00000000-0000-4000-8000-000000000031"
    meta_uuid = "00000000-0000-4000-8000-000000000032"
    remote_streams_doc = json.loads((t / "aiostreams.json").read_text(encoding="utf-8"))

    def _sync_handler(
        method: str, url: str, _hdrs: Mapping[str, str], _body: Any
    ) -> Result[HttpResponse, NetworkError]:
        if method == "POST" and url.endswith("/api/v1/user"):
            return Ok(
                HttpResponse(
                    201,
                    {"data": {"uuid": streams_uuid, "encryptedPassword": "enc-created"}},
                    "",
                )
            )
        if method == "POST" and url.endswith("/api/config/save"):
            return Ok(HttpResponse(201, {"userUUID": meta_uuid}, ""))
        if method == "GET" and "/api/v1/user" in url:
            return Ok(HttpResponse(200, {"data": {"userData": remote_streams_doc}}, ""))
        if "aiometadata.elfhosted.com" in url and url.endswith("/manifest.json"):
            return Ok(
                HttpResponse(
                    200,
                    {"id": "com.aio", "name": "AIO", "catalogs": [{"id": "tmdb.trending", "type": "movie"}]},
                    "",
                )
            )
        if "/catalog/" in url:
            return Ok(HttpResponse(200, {"metas": [{"id": "tt1", "name": "Item"}]}, ""))
        return Err(NetworkError(message=f"Unhandled {method} {url}", url=url))

    client = _FakeHttp(_sync_handler)
    streamed_steps: list[str] = []
    push_res = push_workspace(
        paths,
        target="all",
        create=True,
        warm_catalogs=True,
        on_step=streamed_steps.append,
        http_client=client,
    )
    assert isinstance(push_res, Ok), push_res
    assert streamed_steps == list(push_res.value.steps)
    assert any("Created UUID" in s for s in streamed_steps)
    assert any("[warm]" in s for s in streamed_steps)
    env_text = (t / ".env").read_text(encoding="utf-8")
    assert f"AIOSTREAMS_UUID={streams_uuid}" in env_text
    assert f"AIOMETADATA_UUID={meta_uuid}" in env_text

    # Diff reports skipped services cleanly
    diff_res = diff_workspace(paths, target="aiostreams", http_client=client)
    assert isinstance(diff_res, Ok), diff_res
    assert isinstance(diff_res.value, DiffReport)
    assert len(diff_res.value.outcomes) == 1

    pull_steps: list[str] = []
    pull_res = pull_workspace(paths, target="aiostreams", on_step=pull_steps.append, http_client=client)
    assert isinstance(pull_res, Ok), pull_res
    assert any("Pulled & scrubbed" in s for s in pull_steps)

    # Helpful Stremio login hint when pushing configured aiomanager without Stremio authKey
    mgr_doc = json.loads((t / "aiomanager.json").read_text(encoding="utf-8"))
    mgr_doc["accounts"][0]["connections"].append(
        {
            "id": "00000000-0000-4000-8000-000000000034",
            "platform": "stremio",
            "enabled": True,
            "credentials": {"authKey": "{{SECRET_STREMIO_AUTH_KEY}}"},
        }
    )
    (t / "aiomanager.json").write_text(json.dumps(mgr_doc, indent=2) + "\n", encoding="utf-8")
    (t / ".env").write_text(
        env_text
        + "\nAIOMANAGER_URL=https://aiomanager.elfhosted.com"
        + "\nAIOMANAGER_UUID=00000000-0000-4000-8000-000000000033"
        + "\nAIOMANAGER_SYNC_PASSWORD=syncpw\n",
        encoding="utf-8",
    )
    mgr_err = push_workspace(paths, target="aiomanager", http_client=client)
    assert isinstance(mgr_err, Err)
    assert "sw login stremio" in str(mgr_err.error)
