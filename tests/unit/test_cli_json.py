"""Unit tests for `--json` output across CLI inspection commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.result import Ok
from stream_weaver.core.types import ConfigDiffOutcome
from stream_weaver.engine.workspace import DiffReport

runner = CliRunner()


def test_cli_json_flags_for_inspection_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "agent-stack"
    init_res = runner.invoke(app, ["init", str(ws), "--starter", "--use"])
    assert init_res.exit_code == 0, init_res.output

    # 1. `sw workspace show --json`
    ws_show = runner.invoke(app, ["workspace", "show", "--json"])
    assert ws_show.exit_code == 0, ws_show.output
    ws_data = json.loads(ws_show.stdout)
    assert ws_data["defaultWorkspace"] == str(ws.resolve())
    assert ws_data["activeWorkspace"] == str(ws.resolve())

    # 2. `sw validate --json` (valid + broken cases)
    val_ok = runner.invoke(app, ["validate", "--dir", str(ws), "--json"])
    assert val_ok.exit_code == 0, val_ok.output
    val_data = json.loads(val_ok.stdout)
    assert val_data["valid"] is True
    assert val_data["workspaces"][0]["name"] == "agent-stack"
    assert val_data["workspaces"][0]["failures"] == []

    (ws / "ARCHITECTURE.md").write_text("stale", encoding="utf-8")
    val_bad = runner.invoke(app, ["validate", "--dir", str(ws), "--json"])
    assert val_bad.exit_code == 1
    bad_data = json.loads(val_bad.stdout)
    assert bad_data["valid"] is False
    assert bad_data["workspaces"][0]["valid"] is False
    assert any("diagram out of sync" in item for item in bad_data["workspaces"][0]["failures"])

    # 3. `sw packs list --json`
    packs_res = runner.invoke(app, ["packs", "list", "--dir", str(ws), "--json"])
    assert packs_res.exit_code == 0, packs_res.output
    packs_data = json.loads(packs_res.stdout)
    assert packs_data["tenant"] == "agent-stack"
    assert packs_data["debrid"]["id"] == "torbox"
    assert "maxResolution" in packs_data["streamPolicy"]
    assert any(p["id"] == "core_debrid" for p in packs_data["packs"])

    # 4. `sw profile list --json`
    prof_res = runner.invoke(app, ["profile", "list", "--dir", str(ws), "--json"])
    assert prof_res.exit_code == 0, prof_res.output
    prof_data = json.loads(prof_res.stdout)
    assert prof_data["tenant"] == "agent-stack"
    assert [p["name"] for p in prof_data["profiles"]] == ["main"]
    assert prof_data["profiles"][0]["collectionCount"] == 7

    # 5. `sw library list --json`
    lib_res = runner.invoke(app, ["library", "list", "collection", "--dir", str(ws), "--json"])
    assert lib_res.exit_code == 0, lib_res.output
    lib_data = json.loads(lib_res.stdout)
    assert lib_data["tenant"] == "agent-stack"
    assert lib_data["recipes"]
    assert all(r["kind"] == "collection" for r in lib_data["recipes"])

    # 6. `sw diff --json`
    fake_report = DiffReport(
        outcomes=(
            ConfigDiffOutcome(
                target="aiostreams",
                in_sync=False,
                added_lines=2,
                removed_lines=1,
                changed_keys=("presets",),
                unified_diff="--- remote\n+++ local\n",
            ),
            ConfigDiffOutcome(target="aiometadata", in_sync=True),
        ),
        skipped=("aiomanager (no AIOMANAGER_UUID)",),
    )

    def _fake_diff(*args: Any, **kwargs: Any) -> Ok[DiffReport]:
        return Ok(fake_report)

    monkeypatch.setattr("stream_weaver.cli.app.diff_workspace", _fake_diff)
    diff_res = runner.invoke(app, ["diff", "--dir", str(ws), "--stat", "--json"])
    assert diff_res.exit_code == 0, diff_res.output
    diff_data = json.loads(diff_res.stdout)
    assert diff_data["inSync"] is False
    assert diff_data["skipped"] == ["aiomanager (no AIOMANAGER_UUID)"]
    assert diff_data["outcomes"][0]["target"] == "aiostreams"
    assert diff_data["outcomes"][0]["changedKeys"] == ["presets"]
    assert diff_data["outcomes"][0]["unifiedDiff"] == ""

    diff_exit = runner.invoke(app, ["diff", "--dir", str(ws), "--json", "--exit-code"])
    assert diff_exit.exit_code == 1
    assert "--- remote" in json.loads(diff_exit.stdout)["outcomes"][0]["unifiedDiff"]
