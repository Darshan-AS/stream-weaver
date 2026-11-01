"""Phase 5: a profile on its *own* Nuvio login (the GF case) scaffolds through the same path as a shared-login one."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from stream_weaver.cli.app import _compile_all_result, _try_context, app
from stream_weaver.core.aiomanager_accounts import apply_managed_accounts
from stream_weaver.core.result import Ok
from stream_weaver.core.tenant import TenantPaths

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"


def test_profile_on_its_own_nuvio_login(tmp_path: Path) -> None:
    t = tmp_path / "t"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    (t / ".env").write_text(
        "AIOSTREAMS_URL=https://aiostreams.example.com\nAIOSTREAMS_UUID=00000000-0000-4000-8000-000000000001\n"
        "AIOMETADATA_URL=https://aiometadata.example.com\nAIOMETADATA_UUID=00000000-0000-4000-8000-000000000002\n"
        "AIOMANAGER_URL=https://aiomanager.example.com\n"
        "SECRET_NUVIO_ACCESS_TOKEN=eyJa\nSECRET_NUVIO_REFRESH_TOKEN=r\nSECRET_NUVIO_EMAIL=demo@example.com\n"
        "SECRET_STREMIO_AUTH_KEY=k\n"
        # her own Nuvio account, as `sw login nuvio --login gf` would store it
        "SECRET_NUVIO_GF_ACCESS_TOKEN=eyJb\nSECRET_NUVIO_GF_REFRESH_TOKEN=r2\nSECRET_NUVIO_GF_EMAIL=gf@example.com\n",
        encoding="utf-8",
    )
    assert (
        runner.invoke(
            app,
            ["login", "adopt", "nuvio", "--login", "gf", "--prefix", "NUVIO_GF", "--dir", str(t)],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "instance",
                "new",
                "aiometadata",
                "gf",
                "--from",
                str(EXAMPLE / "aiometadata.json"),
                "--dir",
                str(t),
            ],
        ).exit_code
        == 0
    )
    res = runner.invoke(
        app,
        [
            "profile",
            "new",
            "gf",
            "--display-name",
            "GF",
            "--aiometadata",
            "gf",
            "--nuvio-login",
            "gf",
            "--nuvio-profile",
            "GF",
            "--extras-from",
            "don",
            "--dir",
            str(t),
        ],
    )
    assert res.exit_code == 0, res.output

    doc = json.loads((t / "profiles" / "gf" / "profile.json").read_text())
    assert doc["clients"]["nuvio"] == {
        "login": "gf",
        "profile": {"match": {"name": "GF"}},
        "platforms": ["tv", "desktop", "mobile"],
    }
    assert "stremio" not in doc["clients"]

    ctx = _try_context(TenantPaths(t))
    assert isinstance(ctx, Ok), ctx
    compiled = _compile_all_result(ctx.value)
    assert isinstance(compiled, Ok), compiled
    mgr = json.loads((t / "aiomanager.json").read_text())
    outcome = apply_managed_accounts(mgr, "t", [cp.resolved for cp in compiled.value])
    gf = next(a for a in outcome.state["accounts"] if a["name"] == "GF")
    (conn,) = gf["connections"]
    # her connection carries *her* login placeholders, not the shared ones
    assert conn["credentials"]["accessToken"] == "{{SECRET_NUVIO_GF_ACCESS_TOKEN}}"
    assert conn["credentials"]["refreshToken"] == "{{SECRET_NUVIO_GF_REFRESH_TOKEN}}"
    assert conn["credentials"]["email"] == "{{SECRET_NUVIO_GF_EMAIL}}"
    # and the Nuvio sync would group her separately from the shared login (distinct identity)
    don = next(a for a in outcome.state["accounts"] if a["name"] == "Demo")
    assert (
        don["connections"][0]["credentials"]["refreshToken"] != conn["credentials"]["refreshToken"]
    )
