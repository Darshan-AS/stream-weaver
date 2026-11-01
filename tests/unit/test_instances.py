"""Unit + property tests for named service instances (`core/instances.py`) and `sw instance`."""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.instances import (
    DEFAULT_INSTANCE,
    InstanceBinding,
    instance_env_keys,
    resolve_default_bindings,
    resolve_instance_binding,
)
from stream_weaver.core.result import Err, Ok
from stream_weaver.core.tenant import default_tenant_manifest, parse_tenant_manifest
from stream_weaver.core.types import ALL_SERVICES

runner = CliRunner()


def _manifest(extra: dict[str, dict[str, str]] | None = None):  # type: ignore[no-untyped-def]
    doc = default_tenant_manifest("t")
    for svc, members in (extra or {}).items():
        doc["instances"][svc].update(members)
    res = parse_tenant_manifest(doc)
    assert isinstance(res, Ok)
    return res.value


def test_default_binding_reads_legacy_flat_keys() -> None:
    env = {
        "AIOMETADATA_URL": "https://m.example.com/",
        "AIOMETADATA_UUID": "u1",
        "AIOMETADATA_PASSWORD": "p1",
        "AIOMETADATA_ADDON_PASSWORD": "ap",
        "AIOMANAGER_URL": "https://mgr.example.com",
        "AIOMANAGER_UUID": "u2",
        "AIOMANAGER_SYNC_PASSWORD": "sp",
    }
    m = _manifest()
    b = resolve_instance_binding(m, env, "aiometadata")
    assert isinstance(b, Ok)
    assert b.value.base_url == "https://m.example.com"
    assert b.value.addon_password == "ap"
    assert b.value.configured_for_pull()
    assert b.value.uuid_env_key == "AIOMETADATA_UUID"
    assert b.value.password_env_key == "AIOMETADATA_PASSWORD"

    mgr = resolve_instance_binding(m, env, "aiomanager")
    assert isinstance(mgr, Ok)
    assert mgr.value.password == "sp"
    assert mgr.value.password_env_key == "AIOMANAGER_SYNC_PASSWORD"
    assert mgr.value.addon_password == ""

    defaults = resolve_default_bindings(m, env)
    assert set(defaults) == set(ALL_SERVICES)
    assert not defaults["aiostreams"].configured_for_pull()
    assert not defaults["aiostreams"].configured_for_push(create=True)


def test_named_instance_inherits_default_url_only_when_unset() -> None:
    m = _manifest({"aiometadata": {"dad": "AIOMETADATA_DAD"}})
    env = {
        "AIOMETADATA_URL": "https://m.example.com",
        "AIOMETADATA_UUID": "u1",
        "AIOMETADATA_PASSWORD": "p1",
        "AIOMETADATA_DAD_PASSWORD": "p2",
    }
    dad = resolve_instance_binding(m, env, "aiometadata", "dad")
    assert isinstance(dad, Ok)
    assert dad.value.url == "https://m.example.com"
    assert dad.value.uuid == ""
    assert not dad.value.configured_for_pull()
    assert dad.value.configured_for_push(create=True)  # ready for `--create`
    assert dad.value.uuid_env_key == "AIOMETADATA_DAD_UUID"
    assert dad.value.url_env_key == "AIOMETADATA_URL"  # inherited → documents reference the default key

    env2 = {**env, "AIOMETADATA_DAD_URL": "https://other.example.com"}
    dad2 = resolve_instance_binding(m, env2, "aiometadata", "dad")
    assert isinstance(dad2, Ok)
    assert dad2.value.url == "https://other.example.com"
    assert dad2.value.url_env_key == "AIOMETADATA_DAD_URL"


def test_unknown_instance_is_an_error_with_hint() -> None:
    res = resolve_instance_binding(_manifest(), {}, "aiometadata", "ghost")
    assert isinstance(res, Err)
    assert "sw instance new aiometadata ghost" in res.error.message


def test_with_uuid_is_pure() -> None:
    b = InstanceBinding("aiostreams", "x", "AIOSTREAMS_X", "https://s", "", "pw")
    b2 = b.with_uuid("new")
    assert b.uuid == ""
    assert b2.uuid == "new"
    assert b2.password == b.password
    assert b2.prefix == b.prefix


@given(
    svc=st.sampled_from(ALL_SERVICES),
    prefix=st.from_regex(r"^[A-Z][A-Z0-9_]{0,20}$", fullmatch=True),
)
def test_instance_env_keys_are_prefixed_and_unique(svc, prefix) -> None:  # type: ignore[no-untyped-def]
    keys = instance_env_keys(svc, prefix)
    assert all(k.startswith(prefix + "_") for k in keys)
    assert len(set(keys)) == len(keys)
    assert f"{prefix}_URL" in keys
    assert f"{prefix}_UUID" in keys
    if svc == "aiomanager":
        assert f"{prefix}_SYNC_PASSWORD" in keys
        assert f"{prefix}_ADDON_PASSWORD" not in keys
    else:
        assert f"{prefix}_PASSWORD" in keys
        assert f"{prefix}_ADDON_PASSWORD" in keys


# ---------------------------------------------------------------------------
# sw instance new / list
# ---------------------------------------------------------------------------


def test_sw_instance_new_registers_scaffolds_env_and_copies_doc(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "home"
    t = tmp_path / "t"
    assert runner.invoke(app, ["init", str(t), "--from", str(example)]).exit_code == 0

    res = runner.invoke(
        app,
        [
            "instance",
            "new",
            "aiometadata",
            "dad",
            "--from",
            str(example / "aiometadata.json"),
            "--dir",
            str(t),
        ],
    )
    assert res.exit_code == 0, res.output

    manifest = json.loads((t / "tenant.json").read_text())
    assert manifest["instances"]["aiometadata"]["dad"] == "AIOMETADATA_DAD"
    assert (t / "aiometadata.dad.json").is_file()
    env_text = (t / ".env").read_text()
    assert "AIOMETADATA_DAD_UUID=" in env_text
    assert "AIOMETADATA_DAD_PASSWORD=" in env_text
    assert "AIOMETADATA_DAD_URL=" not in env_text  # inherits default URL
    assert "AIOMETADATA_DAD_UUID=" in (t / ".env.example").read_text()

    # idempotency guard
    dup = runner.invoke(app, ["instance", "new", "aiometadata", "dad", "--dir", str(t)])
    assert dup.exit_code == 1
    assert "already registered" in dup.output

    listing = runner.invoke(app, ["instance", "list", "--dir", str(t)], env={"COLUMNS": "220"})
    assert listing.exit_code == 0, listing.output
    assert "AIOMETADATA_DAD" in listing.output
    assert "aiometadata.dad.json" in listing.output

    # still a valid tenant
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0


def test_sw_instance_new_rejects_unknown_service(tmp_path: Path) -> None:
    t = tmp_path / "t"
    runner.invoke(app, ["init", str(t)])
    res = runner.invoke(app, ["instance", "new", "plex", "x", "--dir", str(t)])
    assert res.exit_code == 1
    assert "Unknown service" in res.output


def test_default_instance_name_constant() -> None:
    assert DEFAULT_INSTANCE == "default"
