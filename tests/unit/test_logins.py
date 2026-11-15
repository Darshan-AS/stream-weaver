"""Tests for named logins (`core/logins.py`), the Nuvio session pool, and `sw login adopt|list`."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.logins import (
    LoginBinding,
    login_env_keys,
    login_for_identity,
    refreshed_token_env_keys,
    resolve_login_binding,
)
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.tenant import default_tenant_manifest, parse_tenant_manifest
from stream_weaver.effects.http import HttpResponse
from stream_weaver.providers.services.nuvio_session import (
    DEFAULT_NUVIO_SUPABASE_ANON_KEY,
    NuvioSessionPool,
    password_grant,
    resolve_nuvio_anon_key,
)

runner = CliRunner()


def _manifest(logins: dict[str, dict[str, str]]):  # type: ignore[no-untyped-def]
    doc = default_tenant_manifest("t")
    doc["logins"] = logins
    res = parse_tenant_manifest(doc)
    assert isinstance(res, Ok)
    return res.value


# ---------------------------------------------------------------------------
# core/logins.py
# ---------------------------------------------------------------------------


def test_legacy_prefix_maps_to_existing_platform_level_keys() -> None:
    m = _manifest({"nuvio": {"don": "NUVIO"}, "stremio": {"don": "STREMIO"}})
    env = {
        "SECRET_NUVIO_ACCESS_TOKEN": "eyJa",
        "SECRET_NUVIO_REFRESH_TOKEN": "r1",
        "SECRET_NUVIO_EMAIL": "d@x",
        "SECRET_STREMIO_AUTH_KEY": "ak",
    }
    n = resolve_login_binding(m, env, "nuvio", "don")
    assert isinstance(n, Ok)
    assert n.value.credentials == {"accessToken": "eyJa", "refreshToken": "r1", "email": "d@x"}
    assert n.value.identity_value == "r1"
    assert n.value.configured()
    assert n.value.env_key("refreshToken") == "SECRET_NUVIO_REFRESH_TOKEN"
    assert n.value.placeholders()["accessToken"] == "{{SECRET_NUVIO_ACCESS_TOKEN}}"

    s = resolve_login_binding(m, env, "stremio", "don")
    assert isinstance(s, Ok)
    assert s.value.identity_value == "ak"
    assert login_env_keys("stremio", "STREMIO_GF") == (
        "SECRET_STREMIO_GF_AUTH_KEY",
        "SECRET_STREMIO_GF_EMAIL",
    )


def test_unknown_login_and_platform_errors() -> None:
    m = _manifest({})
    assert isinstance(resolve_login_binding(m, {}, "nuvio", "ghost"), Err)
    assert isinstance(resolve_login_binding(m, {}, "plex", "x"), Err)


def test_login_for_identity_finds_shared_login() -> None:
    m = _manifest({"nuvio": {"don": "NUVIO", "gf": "NUVIO_GF"}})
    env = {
        "SECRET_NUVIO_ACCESS_TOKEN": "a1",
        "SECRET_NUVIO_REFRESH_TOKEN": "r1",
        "SECRET_NUVIO_GF_ACCESS_TOKEN": "a2",
        "SECRET_NUVIO_GF_REFRESH_TOKEN": "r2",
    }
    hit = login_for_identity(m, env, "nuvio", "r2")
    assert isinstance(hit, LoginBinding)
    assert hit.name == "gf"
    assert login_for_identity(m, env, "nuvio", "nope") is None
    assert login_for_identity(m, env, "nuvio", "") is None


def test_refreshed_token_env_keys_are_login_aware() -> None:
    env = {"SECRET_NUVIO_GF_ACCESS_TOKEN": "a2", "SECRET_NUVIO_GF_REFRESH_TOKEN": "r2"}
    assert refreshed_token_env_keys(env, "a2", "r2") == (
        "SECRET_NUVIO_GF_ACCESS_TOKEN",
        "SECRET_NUVIO_GF_REFRESH_TOKEN",
    )
    # only the refresh token is known (access token already rotated in memory)
    assert refreshed_token_env_keys(env, "stale", "r2") == (
        "SECRET_NUVIO_GF_ACCESS_TOKEN",
        "SECRET_NUVIO_GF_REFRESH_TOKEN",
    )
    # nothing known → legacy platform-level keys
    assert refreshed_token_env_keys({}, "x", "y") == (
        "SECRET_NUVIO_ACCESS_TOKEN",
        "SECRET_NUVIO_REFRESH_TOKEN",
    )


# ---------------------------------------------------------------------------
# NuvioSessionPool
# ---------------------------------------------------------------------------


class _FakeNuvioHttp:
    """Simulates Supabase: the first access token is expired; refresh rotates tokens exactly once."""

    def __init__(self) -> None:
        self.refresh_calls = 0
        self.profile_calls = 0
        self.lock = threading.Lock()

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
        _ = method, raw_body, timeout
        hdrs = headers or {}
        if "grant_type=refresh_token" in url:
            with self.lock:
                self.refresh_calls += 1
                n = self.refresh_calls
            if (json_body or {}).get("refresh_token") != "r-old":
                return Ok(HttpResponse(status_code=400, data={"error": "invalid_grant"}, text=""))
            return Ok(
                HttpResponse(
                    status_code=200,
                    data={"access_token": f"a-new-{n}", "refresh_token": f"r-new-{n}"},
                    text="",
                )
            )
        if "grant_type=password" in url:
            if (json_body or {}).get("password") == "pw":
                return Ok(
                    HttpResponse(
                        status_code=200,
                        data={"access_token": "a-pw", "refresh_token": "r-pw"},
                        text="",
                    )
                )
            return Ok(
                HttpResponse(status_code=400, data={"error_description": "Invalid login"}, text="")
            )
        if url.endswith("sync_pull_profiles"):
            with self.lock:
                self.profile_calls += 1
            if hdrs.get("Authorization") == "Bearer a-old":
                return Ok(HttpResponse(status_code=401, data={"message": "JWT expired"}, text=""))
            return Ok(
                HttpResponse(
                    status_code=200,
                    data=[
                        {"id": "p1", "name": "Don", "profile_index": 1},
                        {"id": "p2", "name": "Dad", "profile_index": 2},
                    ],
                    text="",
                )
            )
        raise AssertionError(f"unexpected url {url}")


def test_pool_authenticates_once_per_login_and_refreshes_exactly_once() -> None:
    http = _FakeNuvioHttp()
    pool = NuvioSessionPool(http, anon_key="anon")
    env = {"SECRET_NUVIO_ACCESS_TOKEN": "a-old", "SECRET_NUVIO_REFRESH_TOKEN": "r-old"}

    results: list[Result[Any]] = []

    def _acquire() -> None:
        results.append(pool.acquire(access_token="a-old", refresh_token="r-old", env=env))

    threads = [threading.Thread(target=_acquire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(isinstance(r, Ok) for r in results)
    assert http.refresh_calls == 1, "refresh token must be used exactly once per login"
    sess = results[0].value  # type: ignore[union-attr]
    assert sess.profile_index(profile_id="p2") == 2
    assert sess.profile_index(name="dad") == 2
    assert sess.profile_index(name="nobody") is None
    assert pool.refreshed_secrets() == {
        "SECRET_NUVIO_ACCESS_TOKEN": "a-new-1",
        "SECRET_NUVIO_REFRESH_TOKEN": "r-new-1",
    }


def test_pool_requires_anon_key_and_access_token() -> None:
    http = _FakeNuvioHttp()
    assert isinstance(
        NuvioSessionPool(http, "").acquire(access_token="a", refresh_token="r", env={}), Err
    )
    assert isinstance(
        NuvioSessionPool(http, "anon").acquire(access_token="", refresh_token="r", env={}), Err
    )
    assert resolve_nuvio_anon_key({}) == DEFAULT_NUVIO_SUPABASE_ANON_KEY
    assert resolve_nuvio_anon_key({"NUVIO_SUPABASE_ANON_KEY": "custom-anon"}) == "custom-anon"


def test_password_grant() -> None:
    http = _FakeNuvioHttp()
    assert password_grant(http, "anon", "e", "pw") == Ok(("a-pw", "r-pw"))
    bad = password_grant(http, "anon", "e", "nope")
    assert isinstance(bad, Err)
    assert "Invalid login" in bad.error.message


# ---------------------------------------------------------------------------
# sw login adopt / list
# ---------------------------------------------------------------------------


def test_sw_login_adopt_registers_existing_keys(tmp_path: Path) -> None:
    t = tmp_path / "t"
    assert runner.invoke(app, ["init", str(t)]).exit_code == 0
    (t / ".env").write_text(
        "SECRET_NUVIO_ACCESS_TOKEN=eyJx\nSECRET_NUVIO_REFRESH_TOKEN=r\nSECRET_NUVIO_EMAIL=me@x\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        app, ["login", "adopt", "nuvio", "--login", "don", "--prefix", "NUVIO", "--dir", str(t)]
    )
    assert res.exit_code == 0, res.output
    assert json.loads((t / "tenant.json").read_text())["logins"] == {"nuvio": {"don": "NUVIO"}}

    listing = runner.invoke(app, ["login", "list", "--dir", str(t)], env={"COLUMNS": "200"})
    assert listing.exit_code == 0, listing.output
    assert "me@x" in listing.output

    missing = runner.invoke(
        app, ["login", "adopt", "stremio", "--login", "don", "--prefix", "STREMIO", "--dir", str(t)]
    )
    assert missing.exit_code == 1
    assert "SECRET_STREMIO_AUTH_KEY" in missing.output

    (t / ".env").write_text(
        (t / ".env").read_text() + "SECRET_OTHER_ACCESS_TOKEN=eyJy\nSECRET_OTHER_REFRESH_TOKEN=r2\n",
        encoding="utf-8",
    )
    conflict = runner.invoke(
        app, ["login", "adopt", "nuvio", "--login", "don", "--prefix", "OTHER", "--dir", str(t)]
    )
    assert conflict.exit_code == 1
    assert "already registered" in conflict.output


def test_sw_login_nuvio_uses_default_anon_key_and_reuses_template_prefix(
    tmp_path: Path, monkeypatch: Any
) -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "home"
    t = tmp_path / "t"
    assert runner.invoke(app, ["init", str(t), "--from", str(example)]).exit_code == 0
    # Leave NUVIO_SUPABASE_ANON_KEY blank and omit --prefix; `don -> NUVIO` in tenant.json is reused.
    monkeypatch.setattr("stream_weaver.cli.app.HttpxClient", lambda: _FakeClientCtx(_FakeNuvioHttp()))
    res = runner.invoke(
        app,
        [
            "login",
            "nuvio",
            "--login",
            "don",
            "--email",
            "cloner@example.com",
            "--password",
            "pw",
            "--dir",
            str(t),
        ],
    )
    assert res.exit_code == 0, res.output
    env_text = (t / ".env").read_text(encoding="utf-8")
    assert "SECRET_NUVIO_ACCESS_TOKEN=a-pw" in env_text
    assert "SECRET_NUVIO_REFRESH_TOKEN=r-pw" in env_text
    assert "SECRET_NUVIO_EMAIL=cloner@example.com" in env_text


def test_nuvio_targets_work_without_aiomanager_or_stremio_and_skip_missing_meta_key(
    tmp_path: Path,
) -> None:
    from stream_weaver.cli.app import _compile_all_result, _try_context
    from stream_weaver.cli.nuvio import nuvio_targets, push_nuvio
    from stream_weaver.core.tenant import TenantPaths

    example = Path(__file__).resolve().parents[2] / "examples" / "home"
    t = tmp_path / "t"
    assert runner.invoke(app, ["init", str(t), "--from", str(example)]).exit_code == 0
    # Nuvio-only cloner: NO AIOMANAGER_SYNC_PASSWORD, NO SECRET_STREMIO_AUTH_KEY, NO SECRET_MDBLIST_API_KEY
    (t / ".env").write_text(
        "AIOSTREAMS_URL=https://aiostreams.example.com\n"
        "AIOSTREAMS_UUID=00000000-0000-4000-8000-000000000001\n"
        "SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD=enc-pw\n"
        "AIOMETADATA_URL=https://aiometadata.example.com\n"
        "AIOMETADATA_UUID=00000000-0000-4000-8000-000000000002\n"
        "SECRET_BINGECAT_TOKEN=bc-tok\n"
        "SECRET_TMDB_API_KEY=tmdb-k\n"
        "SECRET_NUVIO_ACCESS_TOKEN=eyJvalid\n"
        "SECRET_NUVIO_REFRESH_TOKEN=r-valid\n"
        "SECRET_NUVIO_EMAIL=cloner@example.com\n",
        encoding="utf-8",
    )
    ctx_res = _try_context(TenantPaths(t))
    assert isinstance(ctx_res, Ok), ctx_res
    comp_res = _compile_all_result(ctx_res.value)
    assert isinstance(comp_res, Ok), comp_res
    targets_res = nuvio_targets(
        tenant_name=ctx_res.value.manifest.name,
        scrubbed_aiomanager=json.loads((t / "aiomanager.json").read_text(encoding="utf-8")),
        env=ctx_res.value.env,
        compiled_profiles=comp_res.value,
    )
    assert isinstance(targets_res, Ok), targets_res
    assert len(targets_res.value) == 1
    assert targets_res.value[0].master_password == ""

    # Multi-doc push skips `meta` with a message when SECRET_MDBLIST_API_KEY is missing; single-doc `meta` errors
    multi_res = push_nuvio(targets_res.value, ("meta", "settings"), ctx_res.value.env, _NoopNuvioSettingsHttp())
    assert isinstance(multi_res, Ok), multi_res
    msgs, _ = multi_res.value
    assert any("meta: skipped" in m and "SECRET_MDBLIST_API_KEY" in m for m in msgs)
    single_res = push_nuvio(targets_res.value, ("meta",), ctx_res.value.env, _NoopNuvioSettingsHttp())
    assert isinstance(single_res, Err)


class _FakeClientCtx:
    def __init__(self, client: Any) -> None:
        self._client = client

    def __enter__(self) -> Any:
        return self._client

    def __exit__(self, *_: Any) -> None:
        return None


class _NoopNuvioSettingsHttp:
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
        if url.endswith("sync_pull_profiles"):
            return Ok(HttpResponse(status_code=200, data=[{"id": "p1", "name": "Main", "profile_index": 1}], text=""))
        if url.endswith("sync_pull_profile_settings_blob"):
            return Ok(HttpResponse(status_code=200, data={}, text="{}"))
        if url.endswith("sync_push_profile_settings_blob"):
            return Ok(HttpResponse(status_code=200, data={"ok": True}, text="{}"))
        raise AssertionError(f"unexpected url {url}")
