"""Unit tests for the UI-agnostic Studio/GitOps workspace engine (`stream_weaver.engine`)."""

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
from stream_weaver.engine import (
    apply_studio_mutation,
    build_custom_folder_sources,
    export_tenant_zip_bytes,
    inspect_tenant_state,
)

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"


def test_engine_inspect_and_mutate_synchronizes_entire_tenant(tmp_path: Path) -> None:
    t = tmp_path / "studio-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    paths = TenantPaths(t)

    snap_res = inspect_tenant_state(paths)
    assert isinstance(snap_res, Ok), snap_res
    snap = snap_res.value
    assert snap["debrid"]["active"] == "torbox"
    assert len(snap["profiles"][0]["collections"]) == 7
    assert snap["profiles"][0]["collections"][0]["folders"][0]["coverUrl"].startswith("https://")

    env_by_key = {item["key"]: item for item in snap["env"]}
    assert env_by_key["SECRET_TORBOX_API_KEY"]["required"] is True
    assert env_by_key["SECRET_SUBSOURCE_API_KEY"]["required"] is True
    assert env_by_key["SECRET_ITSOUT_TOKEN"]["required"] is True

    # Apply a multi-layer Studio edit:
    # - Switch Debrid to Real-Debrid & disable dual_subtitles + release_alerts
    # - Replace Anime collection with parameterised Tamil Cinema
    # - Customise subtitle preferences + disable Stremio client
    # - Save a masked secret into .env
    mut_res = apply_studio_mutation(
        paths,
        {
            "debrid": "realdebrid",
            "enabledPacks": ["core_debrid", "http_fallbacks", "core_subtitles"],
            "envUpdates": {"SECRET_RD_API_KEY": "rd_super_secret_9999"},
            "profile": {
                "name": "don",
                "displayName": "Arjun",
                "stremioEnabled": False,
                "collections": [
                    {"ref": "regional-cinema", "with": {"language": "ta", "slug": "tamil", "label": "Tamil"}},
                    {"ref": "col-streaming-services"},
                    {"ref": "col-genres-moods"},
                ],
                "settings": {
                    "enabled": True,
                    "ref": "default",
                    "with": {
                        "subtitlePreferred": "en",
                        "subtitleSecondary": "ta",
                        "subtitleSizeSp": 20,
                        "tvSubtitleSizePercent": 115,
                    },
                },
            },
        },
    )
    assert isinstance(mut_res, Ok), mut_res
    updated = mut_res.value

    assert updated["debrid"]["active"] == "realdebrid"
    prof = updated["profiles"][0]
    assert prof["displayName"] == "Arjun"
    assert prof["stremioEnabled"] is False
    assert [c["title"] for c in prof["collections"]] == ["Tamil", "Streaming Services", "Genres & Moods"]
    assert [f["id"] for f in prof["collections"][0]["folders"]] == ["fld-tamil-movies", "fld-tamil-series"]

    # Dynamic secret requirements updated automatically; secret is masked in snapshot
    env_after = {item["key"]: item for item in updated["env"]}
    assert env_after["SECRET_RD_API_KEY"]["required"] is True
    assert env_after["SECRET_RD_API_KEY"]["isSet"] is True
    assert env_after["SECRET_RD_API_KEY"]["value"] == ""
    assert env_after["SECRET_RD_API_KEY"]["masked"].endswith("9999")
    assert env_after["SECRET_TORBOX_API_KEY"]["required"] is False
    assert env_after["SECRET_SUBSOURCE_API_KEY"]["required"] is False
    assert env_after["SECRET_ITSOUT_TOKEN"]["required"] is False

    # AIOMetadata catalogs were automatically derived (Tamil materialised, anime collection rows disabled)
    meta_doc = json.loads((t / "aiometadata.json").read_text(encoding="utf-8"))
    enabled_cats = {(c["id"], c["type"]) for c in meta_doc["catalogs"] if c["enabled"]}
    assert ("tmdb.discover.tamil_90s_movies", "movie") in enabled_cats
    assert ("mal.airing", "anime") not in enabled_cats
    assert ("tmdb.discover.japanese_movies", "movie") not in enabled_cats

    # Full offline `sw validate` passes (schema + derived catalogs + managed accounts + diagrams)
    val = runner.invoke(app, ["validate", "--dir", str(t)])
    assert val.exit_code == 0, val.output

    # Exported GitOps zip contains tenant JSONs & ARCHITECTURE.md, but never leaks `.env` by default
    zip_bytes = export_tenant_zip_bytes(paths)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
    assert "studio-tenant/tenant.json" in names
    assert "studio-tenant/ARCHITECTURE.md" in names
    assert "studio-tenant/.env.example" in names
    assert "studio-tenant/.env" not in names


class _FakeMdbListHttp:
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
        assert "api.mdblist.com/lists/alice/cyberpunk-essentials?apikey=mdb-test-key" in url
        return Ok(
            HttpResponse(
                status_code=200,
                data=[
                    {"id": 77001, "name": "Cyberpunk Essentials", "mediatype": "movie"},
                    {"id": 77002, "name": "Cyberpunk Essentials", "mediatype": "show"},
                ],
                text="",
            )
        )


def test_diy_custom_collection_mdblist_slug_and_patch_safe_mutation(tmp_path: Path) -> None:
    t = tmp_path / "diy-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    paths = TenantPaths(t)

    snap_res = inspect_tenant_state(paths)
    assert isinstance(snap_res, Ok)
    snap = snap_res.value
    assert len(snap["availableCatalogs"]) >= 80
    assert "Spanish" in snap["streamPreferences"]["audioLanguageOptions"]

    # Resolve an MDBList URL slug (mixed movie + show list) and a custom TMDB filter
    mdb_built = build_custom_folder_sources(
        paths,
        {"kind": "mdblist", "input": "https://mdblist.com/lists/alice/cyberpunk-essentials"},
        pending_env={"SECRET_MDBLIST_API_KEY": "mdb-test-key"},
        http_client=_FakeMdbListHttp(),
    )
    assert isinstance(mdb_built, Ok), mdb_built
    assert mdb_built.value["title"] == "Cyberpunk Essentials"
    assert [s["catalog"]["id"] for s in mdb_built.value["sources"]] == ["mdblist.77001", "mdblist.77002"]

    tmdb_built = build_custom_folder_sources(
        paths,
        {"kind": "tmdb", "title": "80s Sci-Fi", "media": "movie", "genre": "scifi", "decade": "1980s"},
    )
    assert isinstance(tmdb_built, Ok), tmdb_built

    # Simulate a power user having 2 debrid services in `aiostreams.json`
    streams_file = t / "aiostreams.json"
    s_doc = json.loads(streams_file.read_text(encoding="utf-8"))
    s_doc["services"].append(
        {"id": "realdebrid", "enabled": True, "credentials": {"apiKey": "{{SECRET_RD_API_KEY}}"}}
    )
    streams_file.write_text(json.dumps(s_doc, indent=2) + "\n", encoding="utf-8")

    # Mutating ONLY `profile` (adding DIY inline collection) preserves multi-debrid `services[]`
    diy_col = {
        "id": "col-my-diy",
        "title": "My DIY Shelf",
        "backdropImageUrl": "https://image.tmdb.org/t/p/original/diy.jpg",
        "folders": [
            {
                "id": "fld-cyberpunk",
                "title": mdb_built.value["title"],
                "coverImageUrl": "https://image.tmdb.org/t/p/w780/cyber.jpg",
                "sources": mdb_built.value["sources"],
            },
            {
                "id": "fld-80s-scifi",
                "title": tmdb_built.value["title"],
                "sources": tmdb_built.value["sources"],
            },
        ],
    }
    mut_res = apply_studio_mutation(
        paths,
        {"profile": {"name": "don", "meta": {"enabled": False}, "collections": [{"raw": diy_col}]}},
    )
    assert isinstance(mut_res, Ok), mut_res

    # Multi-debrid `services[]` was NOT clobbered
    after_streams = json.loads(streams_file.read_text(encoding="utf-8"))
    assert [svc["id"] for svc in after_streams["services"]] == ["torbox", "realdebrid"]

    # Both MDBList catalogs and the custom TMDB catalog materialized & enabled in `aiometadata.json`
    after_meta = json.loads((t / "aiometadata.json").read_text(encoding="utf-8"))
    enabled = {(c["id"], c["type"]) for c in after_meta["catalogs"] if c["enabled"]}
    assert ("mdblist.77001", "movie") in enabled
    assert ("mdblist.77002", "series") in enabled
    assert (tmdb_built.value["sources"][0]["catalog"]["id"], "movie") in enabled

    # Even with `nuvio_meta` disabled, `SECRET_MDBLIST_API_KEY` is marked required because of `mdblist.*`
    env_by_key = {e["key"]: e for e in mut_res.value["env"]}
    assert env_by_key["SECRET_MDBLIST_API_KEY"]["required"] is True
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0
