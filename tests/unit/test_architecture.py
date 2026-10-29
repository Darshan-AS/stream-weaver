"""Unit tests for the pure LikeC4 (`architecture/topology.c4`) and `ARCHITECTURE.md` compiler."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stream_weaver.cli.app import _sync_architecture_diagrams
from stream_weaver.core.architecture import compile_architecture_bundle
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.effects.http import HttpResponse
from stream_weaver.providers.services import AIOManagerService


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


def test_compile_architecture_bundle_minimal_realdebrid_has_no_personal_leakage() -> None:
    aiomanager: dict[str, Any] = {
        "accounts": [
            {
                "name": "Alice",
                "addons": [
                    {
                        "flags": {"enabled": True},
                        "transportUrl": "https://streams.example.org/stremio/u1/p1/manifest.json",
                        "metadata": {"customName": "AIOStreams"},
                        "manifest": {"id": "com.viren070.aiostreams"},
                    },
                    {
                        "flags": {"enabled": True},
                        "transportUrl": "https://meta.example.org/stremio/u2/manifest.json",
                        "metadata": {"customName": "AIOMetadata"},
                        "manifest": {"id": "aio-metadata"},
                    },
                ],
                "connections": [
                    {"platform": "stremio", "enabled": True, "capabilities": ["addons"]},
                ],
            }
        ],
        "vault": [
            {"customProviderName": "TMDB", "value": "{{SECRET_TMDB_API_KEY}}"},
        ],
        "failover": {"rules": []},
    }
    aiostreams: dict[str, Any] = {
        "preferredResolutions": ["1080p"],
        "requiredLanguages": ["English"],
        "services": [
            {
                "id": "realdebrid",
                "enabled": True,
                "credentials": {"apiKey": "{{SECRET_RD_API_KEY}}"},
            }
        ],
        "groups": {"enabled": False, "groupings": []},
        "presets": [
            {
                "type": "torrentio",
                "instanceId": "tio",
                "enabled": True,
                "options": {"name": "Torrentio", "timeout": 6000, "resources": ["stream"]},
            }
        ],
    }
    aiometadata: dict[str, Any] = {
        "providers": {"movie": "tmdb", "series": "tmdb"},
        "search": {"enabled": True, "engineEnabled": {"tmdb.search": True}},
        "apiKeys": {"tmdb": "{{SECRET_TMDB_API_KEY}}"},
        "catalogs": [
            {"id": "tmdb.top", "name": "Trending", "source": "tmdb", "enabled": True, "showInHome": True},
        ],
    }

    bundle = compile_architecture_bundle(
        aiomanager=aiomanager,
        aiostreams=aiostreams,
        aiometadata=aiometadata,
        nuvio_collections={},
    )

    assert "realdebrid_srv" in bundle.likec4_dsl
    assert "svc_realdebrid" in bundle.likec4_dsl
    assert "streams.example.org" in bundle.likec4_dsl
    assert "meta.example.org" in bundle.likec4_dsl

    for personal_token in (
        "Darshan-AS",
        "contraband",
        "Peterson Academy",
        "bingecat",
        "torbox_srv",
        "midnightignite.me",
        "Kannada",
        "JioHotstar",
    ):
        assert personal_token not in bundle.likec4_dsl
        assert personal_token not in bundle.architecture_markdown


class _StubManifestHttpClient:
    def __init__(self, responses: Mapping[str, dict[str, Any]]) -> None:
        self._responses = dict(responses)
        self.requested_urls: list[str] = []

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
        _ = (method, headers, json_body, raw_body, timeout)
        self.requested_urls.append(url)
        payload = self._responses.get(url)
        if payload is None:
            return Err(NetworkError(message="Not found", status_code=404, url=url))
        return Ok(HttpResponse(status_code=200, data=payload, text="{}"))


def test_refresh_manifests_updates_custom_addons_and_skips_cinemeta() -> None:
    custom_url = "https://cdn.example.org/my-addon/manifest.json"
    stub_http = _StubManifestHttpClient(
        {
            custom_url: {
                "id": "org.example.custom",
                "name": "Example Custom Addon",
                "version": "2.4.0",
                "catalogs": [{"type": "movie", "id": "indie", "name": "Indie Films"}],
            }
        }
    )
    svc = AIOManagerService(stub_http)
    state: dict[str, Any] = {
        "accounts": [
            {
                "name": "Main",
                "addons": [
                    {
                        "flags": {"enabled": True},
                        "transportUrl": "https://v3-cinemeta.strem.io/manifest.json",
                        "manifest": {"id": "com.linvo.cinemeta", "version": "3.0.0"},
                    },
                    {
                        "flags": {"enabled": True},
                        "transportUrl": "https://cdn.example.org/my-addon",
                        "manifest": {"id": "org.example.custom", "version": "1.0.0"},
                    },
                ],
            }
        ],
        "addons": {
            "savedAddons": [
                {
                    "manifest": {"id": "org.example.custom", "version": "1.0.0"},
                }
            ]
        },
    }

    refreshed = svc._refresh_manifests(state)
    assert stub_http.requested_urls == [custom_url]
    custom_addon = refreshed["accounts"][0]["addons"][1]
    assert custom_addon["manifestId"] == "org.example.custom:2.4.0"
    assert custom_addon["manifest"]["version"] == "2.4.0"
    assert refreshed["addons"]["savedAddons"][0]["manifest"]["version"] == "2.4.0"

