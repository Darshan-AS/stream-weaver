"""Phase 3: a second profile (own AIOMetadata instance, parameterised collections) composes end-to-end."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from stream_weaver.cli.app import _compile_all_result, _scope_to_profile, _try_context, app
from stream_weaver.core.aiomanager_accounts import apply_managed_accounts
from stream_weaver.core.compile import derive_instance_catalogs
from stream_weaver.core.result import Ok
from stream_weaver.core.tenant import TenantPaths

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"

DAD_PROFILE = {
    "$schema": "https://stream-weaver.dev/schemas/profile.v1.json",
    "version": 1,
    "name": "dad",
    "displayName": "Dad",
    "instances": {"aiostreams": "default", "aiometadata": "dad"},
    "clients": {"nuvio": {"login": "don", "profile": {"match": {"name": "Dad"}}, "platforms": ["tv", "desktop", "mobile"]}},
    "addons": {"extra": [{"name": "cinemeta", "transportUrl": "https://v3-cinemeta.strem.io", "customName": "Cinemeta"}]},
    "collections": [
        {"ref": "regional-cinema", "with": {"language": "kn", "slug": "kannada", "label": "Kannada"}},
        {"ref": "regional-cinema", "with": {"language": "hi", "slug": "hindi", "label": "Hindi"}},
        {"ref": "col-streaming-services"},
    ],
    "badges": {"ref": "default"},
    "meta": {"ref": "default"},
    "settings": {"ref": "default"},
}


def _second_profile_tenant(tmp_path: Path) -> Path:
    t = tmp_path / "t"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    (t / ".env").write_text(
        "AIOSTREAMS_URL=https://aiostreams.example.com\nAIOSTREAMS_UUID=00000000-0000-4000-8000-000000000001\n"
        "AIOMETADATA_URL=https://aiometadata.example.com\nAIOMETADATA_UUID=00000000-0000-4000-8000-000000000002\n"
        "AIOMANAGER_URL=https://aiomanager.example.com\n"
        "SECRET_NUVIO_ACCESS_TOKEN=eyJa\nSECRET_NUVIO_REFRESH_TOKEN=r\nSECRET_NUVIO_EMAIL=demo@example.com\n"
        "SECRET_STREMIO_AUTH_KEY=k\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        app, ["instance", "new", "aiometadata", "dad", "--from", str(EXAMPLE / "aiometadata.json"), "--dir", str(t)]
    )
    assert res.exit_code == 0, res.output
    (t / "profiles" / "dad").mkdir(parents=True)
    (t / "profiles" / "dad" / "profile.json").write_text(json.dumps(DAD_PROFILE, indent=2), encoding="utf-8")
    manifest = json.loads((t / "tenant.json").read_text())
    manifest["profiles"].append("dad")
    (t / "tenant.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return t


def test_second_profile_compiles_with_parameterised_recipes_and_own_instance(tmp_path: Path) -> None:
    t = _second_profile_tenant(tmp_path)
    ctx = _try_context(TenantPaths(t))
    assert isinstance(ctx, Ok), ctx
    compiled = _compile_all_result(ctx.value)
    assert isinstance(compiled, Ok), compiled
    by_name = {cp.name: cp for cp in compiled.value}
    dad = by_name["dad"]

    # Collections: Kannada → Hindi → Streaming; decades + classic series rows present
    titles = [c["title"] for c in dad.collections_doc]
    assert titles == ["Kannada", "Hindi", "Streaming Services"]
    kn_movies = dad.collections_doc[0]["folders"][0]
    assert kn_movies["id"] == "fld-kannada-movies"
    row_titles = [s["title"] for s in kn_movies["sources"]]
    assert row_titles == [
        "New Kannada Releases",
        "Popular Kannada Movies",
        "Top Rated Kannada Movies",
        "Kannada Classics — 80s",
        "Kannada Classics — 90s",
        "Kannada Classics — 2000s",
    ]
    kn_series = dad.collections_doc[0]["folders"][1]
    assert [s["catalogId"] for s in kn_series["sources"]] == [
        "tmdb.discover.kannada_series",
        "tmdb.discover.kannada_classic_series",
    ]
    assert all(s["addonId"] == "aio-metadata" for f in dad.collections_doc[0]["folders"] for s in f["sources"])

    # Dad's instance derives: only his catalogs enabled, decade catalogs materialised from the library,
    # anime / BingeCat-era extras disabled (he declares none)
    plan = derive_instance_catalogs("dad", json.loads((t / "aiometadata.dad.json").read_text()), compiled.value)
    assert isinstance(plan, Ok), plan
    enabled = {(c["id"], c["type"]) for c in plan.value.catalogs if c["enabled"]}
    assert ("tmdb.discover.kannada_80s_movies", "movie") in enabled
    assert ("tmdb.discover.hindi_classic_series", "series") in enabled
    assert ("streaming.nfx", "movie") in enabled
    assert not any(cid.startswith("mal.") for cid, _ in enabled)
    assert ("tmdb.discover.japanese_movies", "movie") not in enabled
    # Don's instance is untouched by Dad's profile
    don_plan = derive_instance_catalogs("default", json.loads((t / "aiometadata.json").read_text()), compiled.value)
    assert isinstance(don_plan, Ok)
    assert not don_plan.value.changed

    # Managed accounts: Dad gets his own account with his instance URL + Cinemeta only, same Nuvio login
    mgr = json.loads((t / "aiomanager.json").read_text())
    outcome = apply_managed_accounts(mgr, "t", [cp.resolved for cp in compiled.value])
    names = [a["name"] for a in outcome.state["accounts"]]
    assert names == ["Demo", "Dad"]
    dad_acc = outcome.state["accounts"][1]
    urls = [a["transportUrl"] for a in dad_acc["addons"]]
    assert urls[0] == "{{AIOMETADATA_URL}}/stremio/{{AIOMETADATA_DAD_UUID}}"
    assert "v3-cinemeta" in urls[-1]
    assert not any("bingecat" in u for u in urls)
    (conn,) = dad_acc["connections"]
    assert conn["credentials"]["accessToken"] == "{{SECRET_NUVIO_ACCESS_TOKEN}}"
    assert "profileId" not in conn["credentials"]  # resolved by name "Dad" at push

    # --profile scoping re-binds aiometadata to Dad's instance
    scoped, chosen = _scope_to_profile(ctx.value, compiled.value, "dad")
    assert [cp.name for cp in chosen] == ["dad"]
    assert scoped.bindings["aiometadata"].name == "dad"
    assert scoped.bindings["aiometadata"].uuid_env_key == "AIOMETADATA_DAD_UUID"
    assert scoped.bindings["aiostreams"].name == "default"


def test_validate_reports_what_a_new_profile_still_needs(tmp_path: Path) -> None:
    t = _second_profile_tenant(tmp_path)
    res = runner.invoke(app, ["validate", "--dir", str(t)])
    assert res.exit_code == 1
    out = res.output.replace("\n", " ")
    assert "accounts[] differs" in out  # Dad's account not yet pushed
    assert "aiometadata.dad.json: catalogs differ" in out  # Dad's instance not yet derived/pushed
    assert "diagram out of sync" in out

    listing = runner.invoke(app, ["profile", "list", "--dir", str(t)], env={"COLUMNS": "250"})
    assert listing.exit_code == 0, listing.output
    assert "name=Dad" in listing.output
