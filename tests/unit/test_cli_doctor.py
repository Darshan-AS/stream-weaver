"""Unit tests for `sw doctor` and unified CLI `push` / `pull` / `diff` / `packs` / `warm`."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from stream_weaver.cli.app import (
    HttpxClient,
    _compile_all_result,
    _scope_to_profile,
    _sync_diagrams_for_ctx,
    _try_context,
    app,
)
from stream_weaver.cli.nuvio import (
    nuvio_targets,
    profile_nuvio_connection,
    pull_nuvio,
    push_nuvio,
)
from stream_weaver.cli.packs import packs_app
from stream_weaver.core.errors import NetworkError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.tenant import TenantPaths
from stream_weaver.effects.http import HttpResponse

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"


class _FakeHttpCtx:
    def __init__(
        self,
        handler: Callable[[str, str, Mapping[str, str], Any], Result[HttpResponse, NetworkError]],
    ) -> None:
        self._handler = handler
        self.urls: list[str] = []

    def __enter__(self) -> _FakeHttpCtx:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

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
        self.urls.append(f"{method} {url}")
        return self._handler(method, url, dict(headers or {}), json_body)


def test_preserved_cli_and_nuvio_symbols_remain_importable(tmp_path: Path) -> None:
    assert callable(HttpxClient)
    assert callable(_try_context)
    assert callable(_compile_all_result)
    assert callable(_scope_to_profile)
    assert callable(_sync_diagrams_for_ctx)
    assert callable(nuvio_targets)
    assert callable(push_nuvio)
    assert callable(pull_nuvio)
    assert callable(profile_nuvio_connection)
    assert packs_app is not None

    t = tmp_path / "sym-tenant"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    ctx_res = _try_context(TenantPaths(t))
    assert isinstance(ctx_res, Ok)
    comp_res = _compile_all_result(ctx_res.value)
    assert isinstance(comp_res, Ok)
    scoped_ctx, chosen = _scope_to_profile(ctx_res.value, comp_res.value, "main")
    assert len(chosen) == 1
    assert isinstance(_sync_diagrams_for_ctx(scoped_ctx, check_only=True), Ok)


def test_sw_doctor_offline_json_and_live_probes(tmp_path: Path, monkeypatch: Any) -> None:
    t = tmp_path / "doc-tenant"
    assert (
        runner.invoke(
            app,
            [
                "init",
                str(t),
                "--from",
                str(EXAMPLE),
                "--packs",
                "core_debrid,core_subtitles",
            ],
        ).exit_code
        == 0
    )

    # Unconfigured .env in --offline mode: exits 0 by default, exits 1 with --exit-code
    off_res = runner.invoke(app, ["doctor", "--offline", "--dir", str(t)], env={"COLUMNS": "200"})
    assert off_res.exit_code == 0, off_res.output
    assert "Workspace Health Doctor" in off_res.output
    assert "ERROR" in off_res.output

    off_strict = runner.invoke(app, ["doctor", "--offline", "--exit-code", "--dir", str(t)])
    assert off_strict.exit_code == 1

    # Configure all required keys and verify live healthy report via `sw doctor --json --exit-code`
    streams_uuid = "00000000-0000-4000-8000-000000000041"
    meta_uuid = "00000000-0000-4000-8000-000000000042"
    (t / ".env").write_text(
        "AIOSTREAMS_URL=https://aiostreams.elfhosted.com\n"
        f"AIOSTREAMS_UUID={streams_uuid}\n"
        "AIOSTREAMS_PASSWORD=pw-streams\n"
        "SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD=enc-pw\n"
        "AIOMETADATA_URL=https://aiometadata.elfhosted.com\n"
        f"AIOMETADATA_UUID={meta_uuid}\n"
        "AIOMETADATA_PASSWORD=pw-meta\n"
        "SECRET_TORBOX_API_KEY=tb-valid\n"
        "SECRET_TMDB_API_KEY=tmdb-valid\n"
        "SECRET_TMDB_ACCESS_TOKEN=tmdb-v4-valid\n"
        "SECRET_TVDB_API_KEY=tvdb-valid\n"
        "SECRET_MDBLIST_API_KEY=mdb-valid\n"
        "SECRET_TRAKT_TOKEN_ID=trakt-valid\n"
        "SECRET_BINGECAT_TOKEN=bc-valid\n",
        encoding="utf-8",
    )

    healthy_mode = True

    def _doctor_handler(
        _method: str, url: str, _hdrs: Mapping[str, str], _body: Any
    ) -> Result[HttpResponse, NetworkError]:
        if "api.torbox.app" in url:
            if not healthy_mode:
                return Ok(HttpResponse(401, {"success": False, "detail": "Invalid token"}, ""))
            return Ok(HttpResponse(200, {"success": True, "data": {"email": "ok@example.com"}}, ""))
        if "api.themoviedb.org" in url:
            return Ok(HttpResponse(200, {"images": {"base_url": "https://image.tmdb.org/"}}, ""))
        if "api.mdblist.com" in url:
            return Ok(HttpResponse(200, {"username": "tester"}, ""))
        if url.endswith("/manifest.json"):
            return Ok(
                HttpResponse(
                    200,
                    {"id": "org.addon", "name": "Addon", "version": "1.0.0", "catalogs": []},
                    "",
                )
            )
        return Err(NetworkError(message=f"Unexpected URL: {url}", url=url))

    fake_ctx = _FakeHttpCtx(_doctor_handler)
    monkeypatch.setattr("stream_weaver.cli.app.HttpxClient", lambda: fake_ctx)

    json_res = runner.invoke(app, ["doctor", "--json", "--exit-code", "--dir", str(t)])
    assert json_res.exit_code == 0, json_res.output
    payload = json.loads(json_res.output)
    assert payload["overall"] == "healthy"
    assert payload["summary"]["error"] == 0
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["env.required"]["status"] == "ok"
    assert by_id["aiostreams.server"]["status"] == "ok"
    assert by_id["aiostreams.instance"]["status"] == "ok"
    assert by_id["aiometadata.server"]["status"] == "ok"
    assert by_id["aiometadata.instance"]["status"] == "ok"
    assert by_id["debrid.torbox"]["status"] == "ok"
    assert by_id["metadata.tmdb"]["status"] == "ok"
    assert by_id["metadata.mdblist"]["status"] == "ok"

    # Rich table rendering on `sw config doctor`
    table_res = runner.invoke(
        app, ["config", "doctor", "--exit-code", "--dir", str(t)], env={"COLUMNS": "200"}
    )
    assert table_res.exit_code == 0, table_res.output
    assert "HEALTHY" in table_res.output

    # Rejected upstream key fails `--exit-code`
    healthy_mode = False
    fail_res = runner.invoke(app, ["doctor", "--exit-code", "--dir", str(t)], env={"COLUMNS": "200"})
    assert fail_res.exit_code == 1
    assert "ERROR" in fail_res.output


def test_unified_cli_push_diff_pull_warm_and_packs(tmp_path: Path, monkeypatch: Any) -> None:
    t = tmp_path / "cli-sync"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0

    # `sw --dir <path> packs list` (global option) + `sw packs set` + invalid `--dual-subs`
    bad_subs = runner.invoke(app, ["packs", "set", "--dual-subs", "badtoken", "--dir", str(t)])
    assert bad_subs.exit_code == 1
    assert "expected MAIN:TRANS" in bad_subs.output

    set_res = runner.invoke(
        app,
        [
            "packs",
            "set",
            "--debrid",
            "torbox",
            "--disable",
            "dual_subtitles",
            "--disable",
            "release_alerts",
            "--max-resolution",
            "1080p",
            "--dir",
            str(t),
        ],
    )
    assert set_res.exit_code == 0, set_res.output

    list_res = runner.invoke(app, ["--dir", str(t), "packs", "list"], env={"COLUMNS": "200"})
    assert list_res.exit_code == 0, list_res.output
    assert "TorBox" in list_res.output
    assert "Max 1080p" in list_res.output

    # Disable Nuvio meta so MDBList key is optional for minimal push
    prof_path = t / "profiles" / "main" / "profile.json"
    prof_doc = json.loads(prof_path.read_text(encoding="utf-8"))
    prof_doc.pop("meta", None)
    prof_path.write_text(json.dumps(prof_doc, indent=2) + "\n", encoding="utf-8")

    (t / ".env").write_text(
        "AIOSTREAMS_URL=https://aiostreams.elfhosted.com\n"
        "AIOSTREAMS_PASSWORD=pw1\n"
        "AIOMETADATA_URL=https://aiometadata.elfhosted.com\n"
        "AIOMETADATA_PASSWORD=pw2\n"
        "SECRET_TORBOX_API_KEY=tb-secret-1111\n"
        "SECRET_TMDB_API_KEY=tmdb-secret-2222\n"
        "SECRET_TMDB_ACCESS_TOKEN=tmdb-v4-3333\n"
        "SECRET_TVDB_API_KEY=tvdb-secret-4444\n"
        "SECRET_RPDB_API_KEY=t0-free-rpdb\n"
        "SECRET_SUBSOURCE_API_KEY=sub-secret-7777\n"
        "SECRET_ITSOUT_TOKEN=ito-secret-8888\n"
        "SECRET_TRAKT_TOKEN_ID=trakt-secret-5555\n"
        "SECRET_BINGECAT_TOKEN=bc-secret-6666\n",
        encoding="utf-8",
    )

    streams_uuid = "00000000-0000-4000-8000-000000000051"
    meta_uuid = "00000000-0000-4000-8000-000000000052"
    pushed_streams_payload: dict[str, Any] = {}

    def _sync_handler(
        method: str, url: str, _hdrs: Mapping[str, str], body: Any
    ) -> Result[HttpResponse, NetworkError]:
        nonlocal pushed_streams_payload
        if method == "POST" and url.endswith("/api/v1/user"):
            if isinstance(body, Mapping) and isinstance(body.get("config"), Mapping):
                pushed_streams_payload = dict(body["config"])
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
            return Ok(HttpResponse(200, pushed_streams_payload, ""))
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

    fake_ctx = _FakeHttpCtx(_sync_handler)
    monkeypatch.setattr("stream_weaver.cli.app.HttpxClient", lambda: fake_ctx)

    # 1. `sw push --create --warm` provisions both services, warms Home rows, updates .env & diagrams
    push_res = runner.invoke(app, ["push", "--create", "--warm", "--dir", str(t)])
    assert push_res.exit_code == 0, push_res.output
    assert f"Created UUID {streams_uuid}" in push_res.output
    assert f"Created UUID {meta_uuid}" in push_res.output
    assert "[warm] Pre-warmed" in push_res.output
    assert "Updated architecture/topology.c4 & ARCHITECTURE.md" in push_res.output
    env_after = (t / ".env").read_text(encoding="utf-8")
    assert f"AIOSTREAMS_UUID={streams_uuid}" in env_after
    assert f"AIOMETADATA_UUID={meta_uuid}" in env_after

    # 2. `sw diff aiostreams --exit-code` reports in-sync when remote matches local
    diff_clean = runner.invoke(app, ["diff", "aiostreams", "--exit-code", "--dir", str(t)])
    assert diff_clean.exit_code == 0, diff_clean.output
    assert "In sync with remote" in diff_clean.output

    # 3. `sw push aiostreams --dry-run` previews diff without mutating remote
    pushed_streams_payload = {**pushed_streams_payload, "addonName": "RemoteEditedName"}
    dry_res = runner.invoke(app, ["push", "aiostreams", "--dry-run", "--dir", str(t)])
    assert dry_res.exit_code == 0, dry_res.output
    assert "Drift detected" in dry_res.output

    # 4. `sw diff aiostreams --stat --exit-code` exits 1 on drift
    diff_drift = runner.invoke(app, ["diff", "aiostreams", "--stat", "--exit-code", "--dir", str(t)])
    assert diff_drift.exit_code == 1
    assert "addonName" in diff_drift.output

    # 5. `sw pull aiostreams` reconciles remote change into scrubbed local JSON
    pull_res = runner.invoke(app, ["pull", "aiostreams", "--dir", str(t)])
    assert pull_res.exit_code == 0, pull_res.output
    assert f"Pulled & scrubbed UUID {streams_uuid}" in pull_res.output
    pulled_local = json.loads((t / "aiostreams.json").read_text(encoding="utf-8"))
    assert pulled_local["addonName"] == "RemoteEditedName"
    assert "{{SECRET_TORBOX_API_KEY}}" in json.dumps(pulled_local)

    # 6. `sw warm` runs cleanly against the workspace engine
    warm_res = runner.invoke(app, ["warm", "--workers", "2", "--dir", str(t)])
    assert warm_res.exit_code == 0, warm_res.output
    assert "[warm] Pre-warmed" in warm_res.output

    # 7. Unknown target exits 1 cleanly
    bad_target = runner.invoke(app, ["push", "unknown_target", "--dir", str(t)])
    assert bad_target.exit_code == 1
    assert "Unknown service target" in bad_target.output
