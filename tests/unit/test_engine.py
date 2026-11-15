"""Unit tests for the UI-agnostic Studio/GitOps workspace engine (`stream_weaver.engine`)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.result import Ok
from stream_weaver.core.tenant import TenantPaths
from stream_weaver.engine import (
    apply_studio_mutation,
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
