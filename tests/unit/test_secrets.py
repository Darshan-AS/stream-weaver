"""Unit and Hypothesis property-based tests for declarative secret lenses and `.env` parsing."""

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from stream_weaver.core.result import Err, Ok
from stream_weaver.core.secrets import (
    harvest_and_scrub,
    inject_secrets,
    merge_env_text,
    parse_env_text,
)


def test_harvest_and_inject_aiostreams_roundtrip() -> None:
    raw_cfg: dict[str, Any] = {
        "tmdbApiKey": "tmdb_live_998877",
        "rpdbApiKey": "t0-free-rpdb",
        "services": [
            {
                "id": "torbox",
                "credentials": {"apiKey": "tb_secret_token_abcdef"},
            }
        ],
    }
    scrubbed, new_env = harvest_and_scrub("aiostreams", raw_cfg, {})
    assert scrubbed["tmdbApiKey"] == "{{SECRET_TMDB_API_KEY}}"
    assert scrubbed["rpdbApiKey"] == "{{SECRET_RPDB_API_KEY}}"
    assert scrubbed["services"][0]["credentials"]["apiKey"] == "{{SECRET_TORBOX_API_KEY}}"
    assert new_env["SECRET_TORBOX_API_KEY"] == "tb_secret_token_abcdef"

    reinjected = inject_secrets(scrubbed, new_env)
    assert isinstance(reinjected, Ok)
    assert reinjected.value == raw_cfg


def test_harvest_and_inject_aiomanager_watchly_bingecat_and_vault() -> None:
    raw_cfg: dict[str, Any] = {
        "apiKeys": {"7d210008-bcdb-4bc4": "mgr_key_123456"},
        "accounts": [
            {
                "id": "7d210008-bcdb-4bc4",
                "authKey": "stremio_master_auth",
                "apiKey": "mgr_key_123456",
                "deletedAddons": {
                    "https://watchly.elfhosted.com/old_deleted_watchly_tok": 1792092935456
                },
                "addons": [
                    {
                        "transportUrl": "https://watchly.elfhosted.com/old_deleted_watchly_tok/manifest.json"
                    },
                    {
                        "transportUrl": "https://bingecat.com/stremio/2ec3e4b9-31e3-48f0-935e-8896b1d8b012/manifest.json",
                        "manifestId": "com.aicat.2ec3e4b9-31e3-48f0-935e-8896b1d8b012.nuvio:1.28.0",
                        "manifest": {
                            "id": "com.aicat.2ec3e4b9-31e3-48f0-935e-8896b1d8b012.nuvio",
                            "version": "1.28.0",
                            "name": "BingeCat",
                        },
                    },
                    {
                        "transportUrl": "https://aiostreams.example.com/stremio/6d639290-199e-4c82-ab00-a8e28226a64a/enc_pw_payload_xyz",
                    },
                    {
                        "transportUrl": "https://aiometadata.example.com/stremio/351ba1f7-8129-4fee-a10b-8fa96ac06a15",
                    },
                ],
                "connections": [
                    {
                        "platform": "nuvio",
                        "credentials": {
                            "accessToken": "nuvio_acc_tok_123",
                            "refreshToken": "nuvio_ref_tok_456",
                            "email": "user@example.com",
                        },
                    }
                ],
            }
        ],
        "accountStates": {
            "7d210008-bcdb-4bc4": {
                "installedAddons": [
                    {
                        "addonId": "com.aicat.2ec3e4b9-31e3-48f0-935e-8896b1d8b012.nuvio",
                        "installUrl": "https://bingecat.com/stremio/2ec3e4b9-31e3-48f0-935e-8896b1d8b012/manifest.json",
                    }
                ]
            }
        },
        "addons": {
            "savedAddons": [
                {
                    "installUrl": "https://bingecat.com/stremio/2ec3e4b9-31e3-48f0-935e-8896b1d8b012/manifest.json",
                    "manifest": {
                        "id": "com.aicat.2ec3e4b9-31e3-48f0-935e-8896b1d8b012.nuvio",
                    },
                }
            ]
        },
        "vault": [
            {"id": "35c65899", "provider": "aiostreams", "value": "vault_secret_val_99"}
        ],
    }
    scrubbed, env = harvest_and_scrub("aiomanager", raw_cfg, {})
    assert (
        scrubbed["accounts"][0]["addons"][0]["transportUrl"]
        == "https://watchly.elfhosted.com/{{SECRET_WATCHLY_TOKEN}}/manifest.json"
    )
    assert (
        scrubbed["accounts"][0]["addons"][1]["transportUrl"]
        == "https://bingecat.com/stremio/{{SECRET_BINGECAT_TOKEN}}/manifest.json"
    )
    assert (
        scrubbed["accounts"][0]["addons"][1]["manifestId"]
        == "com.aicat.{{SECRET_BINGECAT_TOKEN}}.nuvio:1.28.0"
    )
    assert (
        scrubbed["accounts"][0]["addons"][1]["manifest"]["id"]
        == "com.aicat.{{SECRET_BINGECAT_TOKEN}}.nuvio"
    )
    assert (
        scrubbed["accountStates"]["7d210008-bcdb-4bc4"]["installedAddons"][0]["addonId"]
        == "com.aicat.{{SECRET_BINGECAT_TOKEN}}.nuvio"
    )
    assert (
        scrubbed["addons"]["savedAddons"][0]["installUrl"]
        == "https://bingecat.com/stremio/{{SECRET_BINGECAT_TOKEN}}/manifest.json"
    )
    assert scrubbed["accounts"][0]["deletedAddons"] == {
        "https://watchly.elfhosted.com/{{SECRET_WATCHLY_TOKEN}}": 1792092935456
    }
    assert (
        scrubbed["accounts"][0]["addons"][2]["transportUrl"]
        == "https://aiostreams.example.com/stremio/6d639290-199e-4c82-ab00-a8e28226a64a/{{SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD}}"
    )
    assert (
        scrubbed["accounts"][0]["addons"][3]["transportUrl"]
        == "https://aiometadata.example.com/stremio/{{SECRET_AIOMETADATA_TOKEN}}"
    )
    assert (
        scrubbed["accounts"][0]["connections"][0]["credentials"]["email"]
        == "{{SECRET_NUVIO_EMAIL}}"
    )
    assert env["SECRET_WATCHLY_TOKEN"] == "old_deleted_watchly_tok"
    assert env["SECRET_BINGECAT_TOKEN"] == "2ec3e4b9-31e3-48f0-935e-8896b1d8b012"
    assert env["SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD"] == "enc_pw_payload_xyz"
    assert env["SECRET_AIOMETADATA_TOKEN"] == "351ba1f7-8129-4fee-a10b-8fa96ac06a15"
    assert env["SECRET_NUVIO_ACCESS_TOKEN"] == "nuvio_acc_tok_123"
    assert env["SECRET_NUVIO_EMAIL"] == "user@example.com"
    assert env["SECRET_VAULT_AIOSTREAMS_35C658"] == "vault_secret_val_99"

    injected = inject_secrets(scrubbed, env)
    assert isinstance(injected, Ok)
    assert injected.value == raw_cfg


def test_short_secret_scrubs_at_lens_without_corrupting_unrelated_strings() -> None:
    raw_cfg: dict[str, Any] = {
        "apiKeys": {"rpdb": "free"},
        "catalogs": [{"name": "free movies"}],
    }
    scrubbed, env = harvest_and_scrub("aiometadata", raw_cfg, {})
    assert scrubbed["apiKeys"]["rpdb"] == "{{SECRET_RPDB_API_KEY}}"
    assert scrubbed["catalogs"][0]["name"] == "free movies"
    reinjected = inject_secrets(scrubbed, env)
    assert isinstance(reinjected, Ok)
    assert reinjected.value == raw_cfg


def test_inject_secrets_missing_returns_err() -> None:
    cfg = {"apiKeys": {"gemini": "{{SECRET_GEMINI_API_KEY}}"}}
    res = inject_secrets(cfg, {})
    assert isinstance(res, Err)
    assert res.error.missing_keys == ("SECRET_GEMINI_API_KEY",)


@given(
    tmdb_key=st.from_regex(r"[A-Za-z0-9_]{8,24}", fullmatch=True),
    gemini_key=st.from_regex(r"[A-Za-z0-9_]{8,24}", fullmatch=True),
)
def test_property_aiometadata_scrub_inject_roundtrip(
    tmdb_key: str,
    gemini_key: str,
) -> None:
    cfg: dict[str, Any] = {
        "apiKeys": {
            "tmdb": tmdb_key,
            "gemini": gemini_key,
        },
        "catalogs": [{"name": "Popular Movies"}],
    }
    scrubbed, env = harvest_and_scrub("aiometadata", cfg, {})
    injected = inject_secrets(scrubbed, env)
    assert isinstance(injected, Ok)
    assert injected.value == cfg


def test_parse_and_merge_env_preserves_comments() -> None:
    initial = "# Header comment\nAIOSTREAMS_URL=https://example.com\nSECRET_TMDB_API_KEY=old_val\n"
    merged = merge_env_text(
        initial,
        {"SECRET_TMDB_API_KEY": "new_val", "SECRET_TORBOX_API_KEY": "tb_1234"},
    )
    assert "# Header comment" in merged
    parsed = parse_env_text(merged)
    assert parsed["AIOSTREAMS_URL"] == "https://example.com"
    assert parsed["SECRET_TMDB_API_KEY"] == "new_val"
    assert parsed["SECRET_TORBOX_API_KEY"] == "tb_1234"
