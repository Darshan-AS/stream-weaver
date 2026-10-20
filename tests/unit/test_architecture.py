"""Unit tests for the pure LikeC4 (`architecture/topology.c4`) and `ARCHITECTURE.md` compiler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stream_weaver.cli.app import _sync_architecture_diagrams
from stream_weaver.core.architecture import compile_architecture_bundle
from stream_weaver.core.result import Ok


def test_compile_architecture_bundle_reflects_dynamic_plugins_and_views() -> None:
    aiomanager: dict[str, Any] = {
        "accounts": [
            {
                "name": "Don",
                "addons": [
                    {"flags": {"enabled": True}, "metadata": {"customName": "AIOStreams"}},
                    {"flags": {"enabled": True}, "metadata": {"customName": "AIOMetadata"}},
                    {"flags": {"enabled": True}, "metadata": {"customName": "BingeCat"}},
                ],
                "connections": [
                    {"platform": "nuvio", "enabled": True, "capabilities": ["addons", "plugins"]},
                    {"platform": "stremio", "enabled": True, "capabilities": ["addons"]},
                ],
            }
        ],
        "vault": [
            {"customProviderName": "TMDB", "value": "{{SECRET_TMDB_API_KEY}}"},
            {"customProviderName": "Gemini AI", "value": "{{SECRET_GEMINI_API_KEY}}"},
        ],
        "failover": {"rules": [{"id": "r1"}]},
    }
    aiostreams: dict[str, Any] = {
        "posterService": "rpdb",
        "preferredResolutions": ["2160p", "1080p"],
        "requiredLanguages": ["English", "Kannada"],
        "services": [
            {
                "id": "torbox",
                "enabled": True,
                "credentials": {"apiKey": "{{SECRET_TORBOX_API_KEY}}"},
            }
        ],
        "groups": {
            "enabled": True,
            "groupings": [
                {"addons": ["swc", "tio"], "condition": "true"},
                {"addons": ["hdh"], "condition": "count(cached(totalStreams)) < 3"},
            ],
        },
        "presets": [
            {
                "type": "custom",
                "instanceId": "swc",
                "enabled": True,
                "options": {
                    "name": "Contraband",
                    "timeout": 5000,
                    "resources": ["catalog", "meta", "stream"],
                },
            },
            {
                "type": "torrentio",
                "instanceId": "tio",
                "enabled": True,
                "options": {"name": "Torrentio", "timeout": 7500, "resources": ["stream"]},
            },
            {
                "type": "hdhub",
                "instanceId": "hdh",
                "enabled": True,
                "options": {"name": "HD Hub's Stream", "timeout": 7500, "resources": ["stream"]},
            },
        ],
    }
    aiometadata: dict[str, Any] = {
        "providers": {
            "movie": "tmdb",
            "series": "tmdb",
            "anime": "tvdb",
            "anime_id_provider": "kitsu",
        },
        "posterRatingProvider": "custom",
        "customPosterUrlPattern": "https://postersplus.slokker.cc/poster?id={id}",
        "search": {
            "enabled": True,
            "engineEnabled": {"tmdb.search": True, "gemini.search": True},
        },
        "apiKeys": {
            "gemini": "{{SECRET_GEMINI_API_KEY}}",
            "tmdb": "{{SECRET_TMDB_API_KEY}}",
        },
        "catalogs": [
            {"id": "trakt.upnext", "name": "Up Next", "source": "trakt", "enabled": True, "showInHome": True},
            {"id": "tmdb.top", "name": "Popular", "source": "tmdb", "enabled": True, "showInHome": True},
        ],
    }
    nuvio_collections: dict[str, Any] = {
        "collections": [
            {
                "id": "col-for-you",
                "title": "For You",
                "folders": [
                    {
                        "id": "f1",
                        "title": "AI Picks",
                        "sources": [{"addonId": "com.aicat.{{SECRET_BINGECAT_TOKEN}}"}],
                    },
                    {
                        "id": "f2",
                        "title": "Trending",
                        "sources": [{"addonId": "aio-metadata"}],
                    },
                ],
            }
        ]
    }

    bundle = compile_architecture_bundle(
        aiomanager=aiomanager,
        aiostreams=aiostreams,
        aiometadata=aiometadata,
        nuvio_collections=nuvio_collections,
    )

    assert "specification {" in bundle.likec4_dsl
    assert "model {" in bundle.likec4_dsl
    assert "views {" in bundle.likec4_dsl
    # Verify all 10 views
    assert "view index {" in bundle.likec4_dsl
    assert "view aiomanager_view of aiomanager {" in bundle.likec4_dsl
    assert "view nuvio_collections_view of aiomanager.nuvio_collections {" in bundle.likec4_dsl
    assert "view aiostreams_view of aiostreams {" in bundle.likec4_dsl
    assert "view aiometadata_view of aiometadata {" in bundle.likec4_dsl
    assert "view contraband_view of contraband {" in bundle.likec4_dsl
    assert "view secrets_view {" in bundle.likec4_dsl
    assert "dynamic view stream_resolution_flow {" in bundle.likec4_dsl
    assert "dynamic view gitops_sync_flow {" in bundle.likec4_dsl
    assert "dynamic view ai_search_flow {" in bundle.likec4_dsl

    # Check nested preset relationships and metadata blocks
    assert "aiostreams.group1_primary.preset_tio_torrentio -> aiostreams.svc_torbox" in bundle.likec4_dsl
    assert "timeout_ms '7500'" in bundle.likec4_dsl
    assert "HD Hub\\'s Stream [hdh]" in bundle.likec4_dsl
    # Check Nuvio collections child relationship
    assert "aiomanager.nuvio_collections.col_for_you -> bingecat" in bundle.likec4_dsl
    assert "aiomanager.nuvio_collections.col_for_you -> aiometadata" in bundle.likec4_dsl
    # Check secret placeholders are tracked as secretKey nodes
    assert "secretKey sec_secret_torbox_api_key 'SECRET_TORBOX_API_KEY'" in bundle.likec4_dsl
    assert "secretKey sec_secret_gemini_api_key 'SECRET_GEMINI_API_KEY'" in bundle.likec4_dsl
    # Check Markdown output
    assert "## 1. Overarching System Landscape" in bundle.architecture_markdown
    assert "## 4. Nuvio Curated Home Collections" in bundle.architecture_markdown
    assert "| `tio` | **Torrentio** |" in bundle.architecture_markdown


def test_committed_architecture_files_are_in_sync_with_configs() -> None:
    root_dir = Path(__file__).resolve().parents[2]
    configs_dir = root_dir / "configs"
    res = _sync_architecture_diagrams(root_dir, configs_dir, check_only=True)
    assert isinstance(res, Ok)
    assert res.value == (), (
        f"Architecture diagrams are out of sync with configs/*.json: {res.value}. "
        "Run `uv run sw diagram` to update them."
    )
