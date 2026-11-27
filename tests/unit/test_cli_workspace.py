"""Unit tests for tenant resolution (`--dir` / `--tenant` / `STREAM_WEAVER_DIR` / CWD marker), `sw init`, and `sw validate`."""

import json
from pathlib import Path

import pytest
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
    saved_default = _mk_tenant(tmp_path / "saved_default")
    repo_root = tmp_path / "repo"
    bare_cwd = tmp_path / "bare_cwd"
    bare_cwd.mkdir()

    # 1. --dir beats everything
    assert resolve_tenant_root(
        explicit, tenant_name="dad", cwd=repo_root, env_override=str(env_dir), default_dir=saved_default
    ) == Ok(explicit.resolve())
    # 2. --tenant NAME → <cwd>/tenants/NAME beats env + cwd + default
    assert resolve_tenant_root(
        None, tenant_name="dad", cwd=repo_root, env_override=str(env_dir), default_dir=saved_default
    ) == Ok(named.resolve())
    # 3. STREAM_WEAVER_DIR beats cwd + default
    assert resolve_tenant_root(
        None, cwd=cwd_tenant, env_override=str(env_dir), default_dir=saved_default
    ) == Ok(env_dir.resolve())
    # 4. cwd containing tenant.json beats default
    assert resolve_tenant_root(
        None, cwd=cwd_tenant, env_override="", default_dir=saved_default
    ) == Ok(cwd_tenant.resolve())
    # 5. Saved default workspace resolves when CWD has no tenant.json
    assert resolve_tenant_root(
        None, cwd=bare_cwd, env_override="", default_dir=saved_default
    ) == Ok(saved_default.resolve())
    # 6. With require_marker=False (e.g. `sw init`), bare CWD wins over default_dir
    assert resolve_tenant_root(
        None, cwd=bare_cwd, env_override="", default_dir=saved_default, require_marker=False
    ) == Ok(bare_cwd.resolve())


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
    import re

    example = Path(__file__).resolve().parents[2] / "examples" / "home"
    shared_library = example.parents[1] / "library"
    target = tmp_path / "from-template"
    res = runner.invoke(app, ["init", str(target), "--from", str(example)])
    assert res.exit_code == 0, res.output
    for doc in ("aiostreams", "aiometadata", "aiomanager"):
        assert (target / f"{doc}.json").is_file(), doc
    prof_file = target / "profiles" / "main" / "profile.json"
    assert prof_file.is_file()
    prof_doc = json.loads(prof_file.read_text(encoding="utf-8"))
    # All 7 curated collections ship in the template, and Nuvio defaults cleanly to profile index 1
    assert [c["ref"] for c in prof_doc["collections"]] == [
        "col-for-you-discovery",
        "col-streaming-services",
        "col-indian-cinema",
        "col-anime-japanese",
        "col-courses-docs",
        "col-genres-moods",
        "col-franchises",
    ]
    assert prof_doc["clients"]["nuvio"]["profile"] == {}
    assert (target / "library" / "collections").is_dir()  # tenant-local recipes travel with the template
    libs = json.loads((target / TENANT_FILE).read_text())["libraries"]
    assert libs == ["library"]  # portable across machines — bundled shared library resolves automatically
    # External directory validates cleanly without needing `../../library` relative paths
    val_res = runner.invoke(app, ["validate", "--dir", str(target)])
    assert val_res.exit_code == 0, val_res.output
    # template documents must carry no identity or stale personal vault/account placeholders
    blob = "".join(p.read_text(encoding="utf-8") for p in target.glob("*.json"))
    assert "swce3b0" not in blob
    for stale_marker in (
        "SECRET_ACCOUNT_AUTH_KEY",
        "SECRET_AIOMANAGER_APIKEY",
        "SECRET_VAULT_",
        "SECRET_WATCHLY_TOKEN",
        "AIOMANAGER_URL",
        "SECRET_GEMINI_API_KEY",
    ):
        assert stale_marker not in blob, stale_marker
    # Every {{...}} placeholder in the scaffolded tenant + shared library must exist in the seeded .env
    placeholders: set[str] = set()
    for p in (*target.rglob("*.json"), *shared_library.rglob("*.json")):
        placeholders.update(re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", p.read_text(encoding="utf-8")))
    env_keys = {
        ln.split("=", 1)[0].strip()
        for ln in (target / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in ln and not ln.lstrip().startswith("#")
    }
    assert not (placeholders - env_keys), f"Seeded .env is missing keys: {sorted(placeholders - env_keys)}"


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


def test_default_workspace_config_and_workspace_commands(tmp_path: Path) -> None:
    import os

    ws1 = tmp_path / "external-stack"
    ws2 = tmp_path / "second-stack"
    bare = tmp_path / "elsewhere"
    bare.mkdir()

    # 1. `sw init <dir> --starter --use` saves the workspace as the remembered default
    init1 = runner.invoke(app, ["init", str(ws1), "--starter", "--use"])
    assert init1.exit_code == 0, init1.output
    assert json.loads((ws1 / TENANT_FILE).read_text())["libraries"] == ["library"]

    init2 = runner.invoke(app, ["init", str(ws2), "--starter"])
    assert init2.exit_code == 0, init2.output

    prev = os.getcwd()
    os.chdir(bare)
    try:
        os.environ.pop("STREAM_WEAVER_DIR", None)
        # Running `sw validate` from an unrelated directory uses ws1 automatically
        val1 = runner.invoke(app, ["validate"])
        assert val1.exit_code == 0, val1.output
        assert "[external-stack] tenant is valid" in val1.output

        show1 = runner.invoke(app, ["workspace", "show"], env={"COLUMNS": "300"})
        assert show1.exit_code == 0, show1.output
        assert str(ws1.resolve()) in show1.output.replace("\n", "")

        # Switch default workspace to ws2 via `sw use` alias
        use2 = runner.invoke(app, ["use", str(ws2)])
        assert use2.exit_code == 0, use2.output
        val2 = runner.invoke(app, ["validate"])
        assert val2.exit_code == 0, val2.output
        assert "[second-stack] tenant is valid" in val2.output

        # Clear default workspace → commands from bare CWD fail cleanly again
        clr = runner.invoke(app, ["workspace", "clear"])
        assert clr.exit_code == 0, clr.output
        no_ws = runner.invoke(app, ["validate"])
        assert no_ws.exit_code == 1
        assert "No tenant found" in no_ws.output
    finally:
        os.chdir(prev)

    # Rejects pointing `sw workspace use` at a directory without tenant.json
    bad_use = runner.invoke(app, ["workspace", "use", str(bare)])
    assert bad_use.exit_code == 1
    assert TENANT_FILE in bad_use.output


def test_load_default_workspace_handles_unreadable_config_and_skips_when_explicit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stream_weaver.effects import storage

    cfg = tmp_path / "unreadable" / "config.json"
    monkeypatch.setenv("STREAM_WEAVER_CONFIG", str(cfg))

    orig_is_file = Path.is_file

    def _deny_cfg(self: Path) -> bool:
        if self == cfg:
            raise PermissionError("sandbox denied")
        return orig_is_file(self)

    monkeypatch.setattr(Path, "is_file", _deny_cfg)
    assert storage.load_default_workspace() is None

    ws = tmp_path / "explicit-ws"
    init_res = runner.invoke(app, ["init", str(ws), "--starter"])
    assert init_res.exit_code == 0, init_res.output
    val_res = runner.invoke(app, ["validate", "--dir", str(ws)])
    assert val_res.exit_code == 0, val_res.output
