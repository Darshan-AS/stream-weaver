"""Tests for profiles (`core/profiles.py`), managed AIOManager accounts, and `sw profile`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.aiomanager_accounts import (
    ResolvedProfile,
    apply_managed_accounts,
    build_managed_account,
    derive_profile_from_account,
    instance_transport_url,
)
from stream_weaver.core.instances import InstanceBinding
from stream_weaver.core.logins import LoginBinding
from stream_weaver.core.profiles import (
    Appearance,
    ExtraAddon,
    NuvioClientSpec,
    Profile,
    StremioClientSpec,
    parse_profile,
    profile_to_doc,
    stable_account_id,
)
from stream_weaver.core.result import Err, Ok

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"

AIOMETA = InstanceBinding("aiometadata", "default", "AIOMETADATA", "https://m", "u-m", "p")
AIOMETA_DAD = InstanceBinding("aiometadata", "dad", "AIOMETADATA_DAD", "https://m", "u-d", "p")
AIOSTREAMS = InstanceBinding("aiostreams", "default", "AIOSTREAMS", "https://s", "u-s", "p")
NUVIO_DON = LoginBinding(
    "nuvio", "don", "NUVIO", {"accessToken": "a", "refreshToken": "r", "email": "e"}
)
NUVIO_GF = LoginBinding(
    "nuvio", "gf", "NUVIO_GF", {"accessToken": "a2", "refreshToken": "r2", "email": ""}
)
STREMIO_DON = LoginBinding("stremio", "don", "STREMIO", {"authKey": "k", "email": ""})


def _profile(**over: Any) -> Profile:
    base: dict[str, Any] = {
        "name": "don",
        "display_name": "Don",
        "instances": {"aiostreams": "default", "aiometadata": "default"},
        "nuvio": NuvioClientSpec(login="don", profile_id="pid-1"),
        "stremio": StremioClientSpec(login="don"),
        "extra_addons": (ExtraAddon("cinemeta", "https://v3-cinemeta.strem.io", "Cinemeta"),),
    }
    base.update(over)
    return Profile(**base)


# ---------------------------------------------------------------------------
# schema round trip
# ---------------------------------------------------------------------------


def test_profile_doc_round_trip() -> None:
    p = _profile(account_id="acc-1")
    doc = profile_to_doc(p)
    parsed = parse_profile(doc)
    assert isinstance(parsed, Ok)
    assert parsed.value == p
    assert profile_to_doc(parsed.value) == doc


@given(st.from_regex(r"^[a-z][a-z0-9_-]{0,31}$", fullmatch=True))
def test_stable_account_id_is_deterministic_and_distinct(name: str) -> None:
    assert stable_account_id("t", name) == stable_account_id("t", name)
    assert stable_account_id("t", name) != stable_account_id("other", name)


def test_parse_profile_rejects_bad_inputs() -> None:
    good = profile_to_doc(_profile())
    assert isinstance(parse_profile({**good, "version": 2}), Err)
    assert isinstance(parse_profile({**good, "name": "Bad"}), Err)
    assert isinstance(parse_profile({**good, "instances": {"aiomanager": "x"}}), Err)
    assert isinstance(parse_profile({**good, "clients": {"plex": {"login": "x"}}}), Err)
    bad_platforms = json.loads(json.dumps(good))
    bad_platforms["clients"]["nuvio"]["platforms"] = ["fridge"]
    assert isinstance(parse_profile(bad_platforms), Err)
    dup = json.loads(json.dumps(good))
    dup["addons"]["extra"].append(dup["addons"]["extra"][0])
    assert isinstance(parse_profile(dup), Err)
    minimal = parse_profile({"version": 1, "name": "x"})
    assert isinstance(minimal, Ok)
    assert minimal.value.instances == {"aiostreams": "default", "aiometadata": "default"}
    assert minimal.value.nuvio is None


# ---------------------------------------------------------------------------
# managed accounts
# ---------------------------------------------------------------------------


def test_instance_transport_urls_use_identity_placeholders() -> None:
    assert (
        instance_transport_url(AIOMETA_DAD)
        == "{{AIOMETADATA_DAD_URL}}/stremio/{{AIOMETADATA_DAD_UUID}}"
    )
    assert (
        instance_transport_url(AIOSTREAMS)
        == "{{AIOSTREAMS_URL}}/stremio/{{AIOSTREAMS_UUID}}/{{SECRET_AIOSTREAMS_ENCRYPTED_PASSWORD}}"
    )


def test_build_managed_account_from_scratch() -> None:
    rp = ResolvedProfile(
        profile=_profile(
            name="dad",
            display_name="Dad",
            instances={"aiostreams": "default", "aiometadata": "dad"},
            nuvio=NuvioClientSpec(login="don", profile_name="Dad"),
            stremio=None,
            extra_addons=(),
        ),
        instances={"aiometadata": AIOMETA_DAD, "aiostreams": AIOSTREAMS},
        logins={"nuvio": NUVIO_DON},
    )
    acc = build_managed_account("t", rp, None)
    assert acc["id"] == stable_account_id("t", "dad")
    assert acc["name"] == "Dad"
    assert [a["transportUrl"] for a in acc["addons"]] == [
        instance_transport_url(AIOMETA_DAD),
        instance_transport_url(AIOSTREAMS),
    ]
    assert acc["addons"][0]["metadata"]["customName"] == "AIOMetadata"
    (conn,) = acc["connections"]
    assert conn["platform"] == "nuvio"
    assert conn["credentials"]["accessToken"] == "{{SECRET_NUVIO_ACCESS_TOKEN}}"
    assert conn["credentials"]["email"] == "{{SECRET_NUVIO_EMAIL}}"
    assert "profileId" not in conn["credentials"]  # resolved by name at push time
    assert acc["primaryConnectionId"] == conn["id"]
    # deterministic
    assert build_managed_account("t", rp, None) == acc


def test_build_managed_account_merges_over_existing_preserving_server_fields() -> None:
    existing = {
        "id": "acc-1",
        "name": "Old Name",
        "authKey": "{{SECRET_ACCOUNT_AUTH_KEY}}",
        "apiKey": "{{SECRET_AIOMANAGER_APIKEY_ACC1}}",
        "createdAt": 123,
        "emoji": "💀",
        "deletedAddons": {"x": 1},
        "addons": [
            {  # same instance, has server manifest → must be kept, order moves to canonical
                "transportUrl": instance_transport_url(AIOSTREAMS),
                "manifestId": "s:1",
                "manifest": {"id": "s"},
                "flags": {"official": False, "enabled": True},
                "metadata": {"lastUpdated": 1, "customName": "AIOStreams"},
            },
            {"transportUrl": instance_transport_url(AIOMETA), "manifest": {"id": "aio-metadata"}},
            {
                "transportUrl": "https://v3-cinemeta.strem.io",
                "manifest": {"id": "cm"},
                "metadata": {"customName": "Cine"},
            },
            {"transportUrl": "https://stale.example.com/manifest.json"},  # not in profile → dropped
        ],
        "connections": [
            {
                "id": "conn-nuvio",
                "platform": "nuvio",
                "connectionType": "native",
                "enabled": True,
                "credentials": {
                    "accessToken": "{{SECRET_NUVIO_ACCESS_TOKEN}}",
                    "refreshToken": "{{SECRET_NUVIO_REFRESH_TOKEN}}",
                    "expiresAt": "999",
                    "profileId": "pid-old",
                    "profileIndex": "",
                    "email": "{{SECRET_NUVIO_EMAIL}}",
                },
                "capabilities": ["addons", "plugins", "profiles"],
            },
            {
                "id": "conn-stremio",
                "platform": "stremio",
                "enabled": True,
                "credentials": {
                    "authKey": "{{SECRET_STREMIO_AUTH_KEY}}",
                    "email": "{{SECRET_NUVIO_EMAIL}}",
                },
                "capabilities": ["addons"],
            },
        ],
        "primaryConnectionId": "conn-nuvio",
        "someUnknownKey": {"keep": True},
    }
    rp = ResolvedProfile(
        profile=_profile(account_id="acc-1"),
        instances={"aiometadata": AIOMETA, "aiostreams": AIOSTREAMS},
        logins={"nuvio": NUVIO_DON, "stremio": STREMIO_DON},
    )
    acc = build_managed_account("t", rp, existing)
    assert acc["id"] == "acc-1"
    assert acc["name"] == "Don"
    assert acc["authKey"] == "{{SECRET_ACCOUNT_AUTH_KEY}}"
    assert acc["createdAt"] == 123
    assert acc["someUnknownKey"] == {"keep": True}
    assert acc["deletedAddons"] == {"x": 1}
    urls = [a["transportUrl"] for a in acc["addons"]]
    assert urls == [
        instance_transport_url(AIOMETA),
        instance_transport_url(AIOSTREAMS),
        "https://v3-cinemeta.strem.io",
    ]
    assert acc["addons"][1]["manifest"] == {"id": "s"}  # preserved from existing
    assert acc["addons"][1]["metadata"]["lastUpdated"] == 1
    assert acc["addons"][2]["metadata"]["customName"] == "Cinemeta"  # profile wins on customName
    nuvio_conn, stremio_conn = acc["connections"]
    assert nuvio_conn["id"] == "conn-nuvio"
    assert nuvio_conn["credentials"]["expiresAt"] == "999"
    assert nuvio_conn["credentials"]["profileId"] == "pid-1"  # profile wins
    # stremio login has no email value → the existing (Nuvio) email placeholder is kept
    assert stremio_conn["credentials"]["email"] == "{{SECRET_NUVIO_EMAIL}}"
    assert stremio_conn["credentials"]["authKey"] == "{{SECRET_STREMIO_AUTH_KEY}}"
    assert acc["primaryConnectionId"] == "conn-nuvio"


def test_apply_managed_accounts_is_idempotent_and_keeps_unmanaged() -> None:
    rp = ResolvedProfile(
        profile=_profile(),
        instances={"aiometadata": AIOMETA, "aiostreams": AIOSTREAMS},
        logins={"nuvio": NUVIO_DON, "stremio": STREMIO_DON},
    )
    state = {
        "version": "2.0.0",
        "accounts": [{"id": "zzz", "name": "Someone Else", "addons": []}],
        "vault": [],
    }
    first = apply_managed_accounts(state, "t", [rp])
    assert first.changed
    assert first.unmanaged == ("Someone Else",)
    assert [a["name"] for a in first.state["accounts"]] == ["Don", "Someone Else"]
    assert first.state["vault"] == []
    second = apply_managed_accounts(first.state, "t", [rp])
    assert not second.changed
    assert second.state == first.state


def test_derive_profile_from_account_inverts_build() -> None:
    rp = ResolvedProfile(
        profile=_profile(
            account_id="acc-1", appearance=Appearance(emoji="🎬", accent_color="#fff")
        ),
        instances={"aiometadata": AIOMETA, "aiostreams": AIOSTREAMS},
        logins={"nuvio": NUVIO_DON, "stremio": STREMIO_DON},
    )
    acc = build_managed_account("t", rp, None)
    derived = derive_profile_from_account(
        acc,
        profile_name="don",
        instance_bindings={"aiometadata": [AIOMETA, AIOMETA_DAD], "aiostreams": [AIOSTREAMS]},
        login_bindings={"nuvio": [NUVIO_GF, NUVIO_DON], "stremio": [STREMIO_DON]},
    )
    assert derived.instances == {"aiostreams": "default", "aiometadata": "default"}
    assert derived.nuvio == NuvioClientSpec(login="don", profile_id="pid-1")
    assert derived.stremio == StremioClientSpec(login="don")
    assert [a.name for a in derived.extra_addons] == ["cinemeta"]
    assert derived.account_id == "acc-1"
    assert derived.appearance.emoji == "🎬"
    # and rebuilding from the derived profile reproduces the same account
    rp2 = ResolvedProfile(profile=derived, instances=rp.instances, logins=rp.logins)
    assert build_managed_account("t", rp2, acc) == acc


# ---------------------------------------------------------------------------
# sw profile import / new / list against the example tenant
# ---------------------------------------------------------------------------


def test_sw_profile_import_and_new_on_example_tenant(tmp_path: Path) -> None:
    t = tmp_path / "t"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    (t / ".env").write_text(
        "AIOSTREAMS_URL=https://aiostreams.example.com\nAIOMETADATA_URL=https://aiometadata.example.com\n"
        "AIOMANAGER_URL=https://aiomanager.example.com\n"
        "SECRET_NUVIO_ACCESS_TOKEN=eyJa\nSECRET_NUVIO_REFRESH_TOKEN=r\nSECRET_NUVIO_EMAIL=demo@example.com\n"
        "SECRET_STREMIO_AUTH_KEY=k\n",
        encoding="utf-8",
    )
    # The template registers logins nuvio/don=NUVIO and stremio/don=STREMIO already.
    # The template ships profile `don` (refs only). Importing the same account under a new name must
    # reproduce the identity half exactly.
    res = runner.invoke(app, ["profile", "import", "Demo", "--name", "demo", "--dir", str(t)])
    assert res.exit_code == 0, res.output
    doc = json.loads((t / "profiles" / "demo" / "profile.json").read_text())
    assert doc["clients"]["nuvio"]["login"] == "don"
    assert doc["clients"]["stremio"]["login"] == "don"
    assert doc["instances"] == {"aiostreams": "default", "aiometadata": "default"}
    assert [a["name"] for a in doc["addons"]["extra"]] == ["bingecat", "cinemeta"]
    assert json.loads((t / "tenant.json").read_text())["profiles"] == ["don", "demo"]
    shipped = json.loads((t / "profiles" / "don" / "profile.json").read_text())
    assert shipped["collections"]
    assert all("ref" in c for c in shipped["collections"])

    # Two profiles now claim the same display name → drop the duplicate again for the rest of the test
    (t / "profiles" / "demo" / "profile.json").unlink()
    manifest = json.loads((t / "tenant.json").read_text())
    manifest["profiles"] = ["don"]
    (t / "tenant.json").write_text(json.dumps(manifest))
    val = runner.invoke(app, ["validate", "--dir", str(t)])
    assert val.exit_code == 0, val.output

    # A second person on the same login, own AIOMetadata instance
    assert (
        runner.invoke(app, ["instance", "new", "aiometadata", "dad", "--dir", str(t)]).exit_code
        == 0
    )
    new = runner.invoke(
        app,
        [
            "profile",
            "new",
            "dad",
            "--aiometadata",
            "dad",
            "--nuvio-login",
            "don",
            "--extras-from",
            "don",
            "--dir",
            str(t),
        ],
    )
    assert new.exit_code == 0, new.output
    dad = json.loads((t / "profiles" / "dad" / "profile.json").read_text())
    assert dad["clients"]["nuvio"]["profile"] == {"match": {"name": "Dad"}}
    assert dad["instances"]["aiometadata"] == "dad"
    assert "stremio" not in dad["clients"]

    # now the local aiomanager.json lacks Dad's account → validate reports the managed drift
    val2 = runner.invoke(app, ["validate", "--dir", str(t)])
    assert val2.exit_code == 1
    assert "accounts[] differs" in val2.output

    listing = runner.invoke(app, ["profile", "list", "--dir", str(t)], env={"COLUMNS": "250"})
    assert listing.exit_code == 0, listing.output
    assert "name=Dad" in listing.output

    unknown = runner.invoke(app, ["profile", "new", "x", "--nuvio-login", "ghost", "--dir", str(t)])
    assert unknown.exit_code == 1
    assert "no `nuvio` login named `ghost`" in unknown.output
