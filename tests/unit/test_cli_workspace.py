"""Unit tests for tenant resolution (`--dir` / `--tenant` / `STREAM_WEAVER_DIR` / CWD marker), `sw init`, and `sw validate`."""

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.result import Err, Ok
from stream_weaver.core.tenant import (
    TENANT_FILE,
    default_tenant_manifest,
    discover_tenants,
    parse_tenant_manifest,
    resolve_tenant_root,
)

runner = CliRunner()


def _mk_tenant(path: Path, name: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / TENANT_FILE).write_text(json.dumps(default_tenant_manifest(name or path.name)), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# resolve_tenant_root
# ---------------------------------------------------------------------------


def test_resolve_tenant_root_priority(tmp_path: Path) -> None:
    explicit = _mk_tenant(tmp_path / "explicit")
    named = _mk_tenant(tmp_path / "repo" / "tenants" / "dad", "dad")
    env_dir = _mk_tenant(tmp_path / "env_dir")
    cwd_tenant = _mk_tenant(tmp_path / "cwd_tenant")
    repo_root = tmp_path / "repo"

    # 1. --dir beats everything
    assert resolve_tenant_root(
        explicit, tenant_name="dad", cwd=repo_root, env_override=str(env_dir)
    ) == Ok(explicit.resolve())
    # 2. --tenant NAME → <cwd>/tenants/NAME beats env + cwd
    assert resolve_tenant_root(None, tenant_name="dad", cwd=repo_root, env_override=str(env_dir)) == Ok(
        named.resolve()
    )
    # 3. STREAM_WEAVER_DIR beats cwd
    assert resolve_tenant_root(None, cwd=cwd_tenant, env_override=str(env_dir)) == Ok(env_dir.resolve())
    # 4. cwd containing tenant.json
    assert resolve_tenant_root(None, cwd=cwd_tenant, env_override="") == Ok(cwd_tenant.resolve())


def test_resolve_tenant_root_has_no_repo_root_fallback(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    res = resolve_tenant_root(None, cwd=plain, env_override="")
    assert isinstance(res, Err)
    assert "sw init" in res.error.message


def test_resolve_tenant_root_rejects_dir_without_marker(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    res = resolve_tenant_root(bare)
    assert isinstance(res, Err)
    assert TENANT_FILE in res.error.message
    # …unless the caller is scaffolding
    assert resolve_tenant_root(bare, require_marker=False) == Ok(bare.resolve())


def test_discover_tenants(tmp_path: Path) -> None:
    _mk_tenant(tmp_path / "tenants" / "zed")
    _mk_tenant(tmp_path / "tenants" / "abe")
    (tmp_path / "tenants" / "not-a-tenant").mkdir()
    assert [p.name for p in discover_tenants(tmp_path)] == ["abe", "zed"]
    assert discover_tenants(tmp_path / "nowhere") == ()


# ---------------------------------------------------------------------------
# tenant.json schema
# ---------------------------------------------------------------------------


@given(st.from_regex(r"^[a-z][a-z0-9_-]{0,31}$", fullmatch=True))
def test_default_manifest_round_trips_any_valid_name(name: str) -> None:
    res = parse_tenant_manifest(default_tenant_manifest(name))
    assert isinstance(res, Ok)
    assert res.value.name == name
    assert res.value.instances["aiometadata"]["default"] == "AIOMETADATA"
    assert res.value.profiles == ()


def test_manifest_rejects_bad_version_name_and_registry() -> None:
    assert isinstance(parse_tenant_manifest({"version": 99, "name": "x"}), Err)
    assert isinstance(parse_tenant_manifest({"version": 1, "name": "Bad Name"}), Err)
    assert isinstance(
        parse_tenant_manifest({"version": 1, "name": "ok", "instances": {"aiometadata": {"dad": "lower_case"}}}),
        Err,
    )
    assert isinstance(
        parse_tenant_manifest({"version": 1, "name": "ok", "profiles": ["a", "a"]}),
        Err,
    )
    ok = parse_tenant_manifest(
        {
            "version": 1,
            "name": "ok",
            "instances": {"aiometadata": {"default": "AIOMETADATA", "dad": "AIOMETADATA_DAD"}},
            "logins": {"nuvio": {"don": "NUVIO_DON"}},
            "profiles": ["don", "dad"],
        }
    )
    assert isinstance(ok, Ok)
    assert ok.value.logins["nuvio"]["don"] == "NUVIO_DON"
    assert ok.value.profiles == ("don", "dad")


# ---------------------------------------------------------------------------
# sw init / sw validate / sw diagram
# ---------------------------------------------------------------------------


def test_sw_init_scaffolds_tenant_and_preserves_env(tmp_path: Path) -> None:
    target = tmp_path / "my-tenant"
    res = runner.invoke(app, ["init", str(target), "--name", "demo"])
    assert res.exit_code == 0, res.output
    assert (target / TENANT_FILE).is_file()
    assert json.loads((target / TENANT_FILE).read_text())["name"] == "demo"
    assert (target / "architecture").is_dir()
    for f in (".env", ".env.example", ".gitignore"):
        assert (target / f).is_file()

    custom_env = "AIOSTREAMS_URL=https://custom.example.com\nSECRET_RD_API_KEY=super_secret_token\n"
    (target / ".env").write_text(custom_env, encoding="utf-8")
    (target / ".env.example").write_text("# stale\n", encoding="utf-8")

    res_force = runner.invoke(app, ["init", str(target), "--force"])
    assert res_force.exit_code == 0, res_force.output
    assert (target / ".env").read_text(encoding="utf-8") == custom_env
    assert "AIOSTREAMS_URL=" in (target / ".env.example").read_text(encoding="utf-8")

    res_diag = runner.invoke(app, ["diagram", "--dir", str(target)])
    assert res_diag.exit_code == 0, res_diag.output
    assert (target / "ARCHITECTURE.md").is_file()
    assert (target / "architecture" / "topology.c4").is_file()

    res_val = runner.invoke(app, ["validate", "--dir", str(target)])
    assert res_val.exit_code == 0, res_val.output


def test_sw_init_from_template_copies_documents(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "home"
    target = tmp_path / "from-template"
    res = runner.invoke(app, ["init", str(target), "--from", str(example)])
    assert res.exit_code == 0, res.output
    for doc in ("aiostreams", "aiometadata", "aiomanager", "nuvio_collections"):
        assert (target / f"{doc}.json").is_file(), doc
    # template documents must carry no identity
    blob = "".join(p.read_text(encoding="utf-8") for p in target.glob("*.json"))
    assert "midnightignite" not in blob
    assert "swce3b0" not in blob


def test_commands_fail_cleanly_outside_a_tenant(tmp_path: Path, monkeypatch: object) -> None:
    import os

    bare = tmp_path / "bare"
    bare.mkdir()
    prev = os.getcwd()
    os.chdir(bare)
    try:
        os.environ.pop("STREAM_WEAVER_DIR", None)
        res = runner.invoke(app, ["diagram"])
    finally:
        os.chdir(prev)
    assert res.exit_code == 1
    assert "No tenant found" in res.output.replace("\n", " ")


def test_sw_validate_all_tenants_discovers_repo_layout(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "home"
    t1 = tmp_path / "tenants" / "one"
    runner.invoke(app, ["init", str(t1), "--from", str(example)])
    runner.invoke(app, ["diagram", "--dir", str(t1)])
    t2 = _mk_tenant(tmp_path / "tenants" / "two")
    runner.invoke(app, ["diagram", "--dir", str(t2)])

    res = runner.invoke(app, ["validate", "--all-tenants", "--dir", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "[one] tenant is valid" in res.output
    assert "[two] tenant is valid" in res.output

    # A stale diagram is a validation failure
    (t2 / "ARCHITECTURE.md").write_text("stale", encoding="utf-8")
    res_bad = runner.invoke(app, ["validate", "--all-tenants", "--dir", str(tmp_path)])
    assert res_bad.exit_code == 1
    assert "diagram out of sync" in res_bad.output
