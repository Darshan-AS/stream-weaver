"""Unit tests for Stream Weaver Studio (`stream_weaver.studio`) in both Local and Stateless modes."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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


class _FakeStudioUpstreamHttp:
    """Stubs Nuvio Supabase auth + AIOStreams/AIOMetadata `--create` provisioning."""

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
        if "grant_type=password" in url:
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={"access_token": "eyJstudio", "refresh_token": "r-studio"},
                    text="",
                )
            )
        if url.endswith("sync_pull_profiles"):
            return Ok(
                HttpResponse(
                    status_code=200,
                    data=[
                        {"id": "00000000-0000-4000-8000-000000000091", "name": "Main", "profile_index": 1},
                        {"id": "00000000-0000-4000-8000-000000000092", "name": "Kids", "profile_index": 2},
                    ],
                    text="",
                )
            )
        if url.endswith("/api/v1/user"):
            return Ok(
                HttpResponse(
                    status_code=201,
                    data={
                        "data": {
                            "uuid": "00000000-0000-4000-8000-000000000071",
                            "encryptedPassword": "enc-studio-pass",
                        }
                    },
                    text="",
                )
            )
        if url.endswith("/api/config/save"):
            return Ok(
                HttpResponse(
                    status_code=201,
                    data={"userUUID": "00000000-0000-4000-8000-000000000072"},
                    text="",
                )
            )
        raise AssertionError(f"Unexpected Studio upstream URL: {url}")


def test_local_gitops_studio_end_to_end(tmp_path: Path) -> None:
    t = tmp_path / "local-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    dispatcher = StudioDispatcher(
        tenant_paths=TenantPaths(t),
        shared_library_root=SHARED_LIB,
        http_client_factory=_FakeStudioUpstreamHttp,
    )

    # 1. Serves UI HTML
    html_resp = dispatcher.dispatch("GET", "/")
    assert html_resp.status == 200
    assert b"Stream Weaver Studio" in html_resp.body

    # 2. Inspects initial state
    state_resp = dispatcher.dispatch("GET", "/api/state")
    assert state_resp.status == 200
    init_data = json.loads(state_resp.body)
    assert init_data["mode"] == "local"
    assert len(init_data["state"]["profiles"][0]["collections"]) == 7

    # 3. Mutates Debrid, packs, stream filters, dual-subs, expanded collections & Nuvio theme/playback settings
    mut_payload = {
        "mutation": {
            "debrid": "realdebrid",
            "enabledPacks": ["core_debrid", "core_subtitles", "dual_subtitles"],
            "streamPreferences": {"maxResolution": "1080p", "cachedOnly": True},
            "dualSubtitlePairs": [["kor", "eng"], ["spa", "eng"]],
            "envUpdates": {
                "AIOSTREAMS_URL": "https://streams.example.org",
                "AIOSTREAMS_PASSWORD": "pw-streams",
                "AIOMETADATA_URL": "https://meta.example.org",
                "AIOMETADATA_PASSWORD": "pw-meta",
                "SECRET_RD_API_KEY": "rd-tok",
                "SECRET_TMDB_API_KEY": "tmdb-k",
                "SECRET_TMDB_ACCESS_TOKEN": "tmdb-jwt",
                "SECRET_TVDB_API_KEY": "tvdb-k",
                "SECRET_RPDB_API_KEY": "t0-free-rpdb",
                "SECRET_MDBLIST_API_KEY": "mdb-k",
                "SECRET_TRAKT_TOKEN_ID": "trakt-tok",
                "SECRET_SUBSOURCE_API_KEY": "sub-tok",
            },
            "profile": {
                "name": "don",
                "collections": [
                    {"ref": "col-trakt-personal"},
                    {"ref": "col-studios-auteurs"},
                    {"ref": "col-kdrama-global"},
                    {"ref": "col-streaming-services", "with": {"services": ["fld-netflix", "fld-crunchyroll", "fld-mubi"]}},
                    {"ref": "regional-cinema", "with": {"language": "ml", "slug": "malayalam", "label": "Malayalam"}},
                ],
                "meta": {"enabled": True, "ref": "default", "with": {"episodeRatings": "hideUnwatched", "blurUnwatchedEpisodes": True}},
                "settings": {
                    "enabled": True,
                    "ref": "default",
                    "with": {
                        "theme": "crimson",
                        "amoledBlack": False,
                        "homeLayout": "grid",
                        "streamSelectionMode": "firstStream",
                        "audioPreferred": "ja",
                        "blurUnwatchedEpisodes": True,
                    },
                },
            },
        }
    }
    mut_resp = dispatcher.dispatch("POST", "/api/mutate", json.dumps(mut_payload).encode("utf-8"))
    assert mut_resp.status == 200, mut_resp.body
    mut_state = json.loads(mut_resp.body)["state"]
    assert mut_state["streamPreferences"]["maxResolution"] == "1080p"
    assert mut_state["streamPreferences"]["cachedOnly"] is True
    assert mut_state["dualSubtitles"]["pairs"] == [["kor", "eng"], ["spa", "eng"]]
    assert mut_state["profiles"][0]["settings"]["with"]["theme"] == "crimson"
    assert mut_state["profiles"][0]["settings"]["with"]["streamSelectionMode"] == "firstStream"
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0

    # 4. Nuvio login + profile listing
    login_resp = dispatcher.dispatch(
        "POST",
        "/api/nuvio-login",
        json.dumps({"login": "don", "email": "user@example.org", "password": "pw"}).encode("utf-8"),
    )
    assert login_resp.status == 200, login_resp.body
    login_data = json.loads(login_resp.body)
    assert [p["name"] for p in login_data["nuvio"]["profiles"]] == ["Main", "Kids"]
    assert login_data["state"]["profiles"][0]["nuvio"]["loginReady"] is True

    # 5. One-click instance provisioning (`--create`)
    prov_resp = dispatcher.dispatch("POST", "/api/provision", b"{}")
    assert prov_resp.status == 200, prov_resp.body
    prov_data = json.loads(prov_resp.body)
    assert prov_data["state"]["tenant"]["aiostreamsProvisioned"] is True
    assert prov_data["state"]["tenant"]["aiometadataProvisioned"] is True
    assert "SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD=enc-studio-pass" in (t / ".env").read_text(encoding="utf-8")


def test_stateless_hosted_studio_persists_nothing_on_server() -> None:
    dispatcher = StudioDispatcher(
        tenant_paths=None,
        template_root=EXAMPLE,
        shared_library_root=SHARED_LIB,
        http_client_factory=_FakeStudioUpstreamHttp,
    )

    # 1. Initial load returns starter bundle
    r1 = dispatcher.dispatch("GET", "/api/state")
    assert r1.status == 200
    d1 = json.loads(r1.body)
    assert d1["mode"] == "stateless"
    bundle = d1["bundle"]
    assert "tenant.json" in bundle["files"]

    # 2. Mutating with browser bundle returns updated bundle without touching `examples/home`
    before_profile = (EXAMPLE / "profiles" / "don" / "profile.json").read_text(encoding="utf-8")
    r2 = dispatcher.dispatch(
        "POST",
        "/api/mutate",
        json.dumps(
            {
                "bundle": bundle,
                "mutation": {
                    "debrid": "alldebrid",
                    "enabledPacks": ["core_debrid"],
                    "envUpdates": {"SECRET_ALLDEBRID_API_KEY": "ad-secret-7777"},
                    "profile": {
                        "name": "don",
                        "displayName": "BrowserUser",
                        "collections": [{"ref": "col-genres-moods"}],
                    },
                },
            }
        ).encode("utf-8"),
    )
    assert r2.status == 200, r2.body
    d2 = json.loads(r2.body)
    assert d2["state"]["debrid"]["active"] == "alldebrid"
    assert d2["state"]["profiles"][0]["displayName"] == "BrowserUser"
    assert d2["bundle"]["env"]["SECRET_ALLDEBRID_API_KEY"] == "ad-secret-7777"
    assert (EXAMPLE / "profiles" / "don" / "profile.json").read_text(encoding="utf-8") == before_profile

    # 3. Export zip from stateless bundle includes customized profile.json & excludes `.env`
    r3 = dispatcher.dispatch(
        "POST", "/api/export-zip", json.dumps({"bundle": d2["bundle"]}).encode("utf-8")
    )
    assert r3.status == 200
    with zipfile.ZipFile(io.BytesIO(r3.body)) as zf:
        exported_prof = json.loads(zf.read("home/profiles/don/profile.json").decode("utf-8"))
        assert exported_prof["displayName"] == "BrowserUser"
        assert "home/.env" not in zf.namelist()
