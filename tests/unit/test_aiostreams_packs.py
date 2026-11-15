"""Unit tests for modular AIOStreams Debrid switching and toggleable addon packs."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from stream_weaver.cli.app import app
from stream_weaver.core.aiostreams_packs import (
    AIOSTREAMS_PACKS,
    active_debrid_provider,
    apply_aiostreams_packs,
    apply_debrid_provider,
    apply_dual_subtitle_pairs,
    apply_stream_preferences,
    inspect_aiostreams_packs,
    inspect_dual_subtitle_pairs,
    inspect_stream_preferences,
    prepare_aiostreams_for_push,
    required_aiostreams_secrets,
)
from stream_weaver.core.result import Err, Ok
from stream_weaver.core.secrets import harvest_and_scrub, inject_secrets

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "home"


def test_pack_toggle_is_lossless_and_drops_unused_secret_requirements() -> None:
    raw = json.loads((EXAMPLE / "aiostreams.json").read_text(encoding="utf-8"))
    all_ids = [p.id for p in AIOSTREAMS_PACKS]
    assert [st.spec.id for st in inspect_aiostreams_packs(raw) if st.enabled] == all_ids
    assert "SECRET_ITSOUT_TOKEN" in required_aiostreams_secrets(raw)
    assert "SECRET_SUBSOURCE_API_KEY" in required_aiostreams_secrets(raw)

    # Toggle off `release_alerts` and `dual_subtitles`
    minimal_res = apply_aiostreams_packs(raw, ("core_debrid", "core_subtitles"))
    assert isinstance(minimal_res, Ok)
    minimal = minimal_res.value
    assert "SECRET_ITSOUT_TOKEN" not in required_aiostreams_secrets(minimal)
    assert "SECRET_SUBSOURCE_API_KEY" not in required_aiostreams_secrets(minimal)

    # `prepare_aiostreams_for_push` strips disabled unkeyed presets so `inject_secrets` succeeds cleanly
    core_env = {
        "SECRET_TORBOX_API_KEY": "tb-123",
        "SECRET_TMDB_API_KEY": "tmdb-123",
        "SECRET_TMDB_ACCESS_TOKEN": "tmdb-jwt",
        "SECRET_TVDB_API_KEY": "tvdb-123",
        "SECRET_RPDB_API_KEY": "t0-free-rpdb",
    }
    prepared = prepare_aiostreams_for_push(minimal, core_env)
    injected = inject_secrets(prepared, core_env)
    assert isinstance(injected, Ok), injected

    # Re-enabling all packs restores every preset and group order losslessly
    restored_res = apply_aiostreams_packs(minimal, all_ids)
    assert isinstance(restored_res, Ok)
    assert restored_res.value == raw

    # On a fully-keyed config (even with `tdb` disabled), `prepare_aiostreams_for_push` is a no-op
    full_env = {
        **core_env,
        "SECRET_ITSOUT_TOKEN": "ito-123",
        "SECRET_SUBSOURCE_API_KEY": "sub-123",
    }
    assert prepare_aiostreams_for_push(raw, full_env) == raw


def test_debrid_switch_and_sw_packs_cli_validate_cleanly(tmp_path: Path) -> None:
    t = tmp_path / "rd-user"
    res = runner.invoke(
        app,
        [
            "init",
            str(t),
            "--from",
            str(EXAMPLE),
            "--debrid",
            "realdebrid",
            "--packs",
            "core_debrid,core_subtitles",
        ],
    )
    assert res.exit_code == 0, res.output

    streams_doc = json.loads((t / "aiostreams.json").read_text(encoding="utf-8"))
    assert active_debrid_provider(streams_doc) == "realdebrid"
    assert required_aiostreams_secrets(streams_doc) == (
        "SECRET_RD_API_KEY",
        "SECRET_RPDB_API_KEY",
        "SECRET_TMDB_ACCESS_TOKEN",
        "SECRET_TMDB_API_KEY",
        "SECRET_TVDB_API_KEY",
    )
    # Offline tenant validation passes immediately (watchlist recipe + cached manifest synced)
    val = runner.invoke(app, ["validate", "--dir", str(t)])
    assert val.exit_code == 0, val.output

    # Enable `anime` pack via `sw packs set` and verify validation remains clean
    set_res = runner.invoke(app, ["packs", "set", "--enable", "anime", "--dir", str(t)])
    assert set_res.exit_code == 0, set_res.output
    list_res = runner.invoke(app, ["packs", "list", "--dir", str(t)], env={"COLUMNS": "200"})
    assert list_res.exit_code == 0, list_res.output
    assert "Real-Debrid" in list_res.output
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0

    assert isinstance(apply_debrid_provider(streams_doc, "unknown_debrid"), Err)
    assert isinstance(apply_aiostreams_packs(streams_doc, ["unknown_pack"]), Err)


def test_stream_preferences_and_strelingo_pair_builder_round_trip(tmp_path: Path) -> None:
    raw = json.loads((EXAMPLE / "aiostreams.json").read_text(encoding="utf-8"))

    # Default inspection & zero-drift identity
    prefs = inspect_stream_preferences(raw)
    assert prefs.max_resolution == "2160p"
    assert prefs.cached_only is False
    assert prefs.audio_languages == ("English", "Kannada", "Hindi", "Japanese")
    default_pairs = inspect_dual_subtitle_pairs(raw)
    assert default_pairs == (("jpn", "eng"), ("eng", "kan"), ("hin", "kan"))

    same_prefs = apply_stream_preferences(
        raw,
        max_resolution="2160p",
        cached_only=False,
        audio_languages=prefs.audio_languages,
    )
    assert isinstance(same_prefs, Ok)
    assert same_prefs.value == raw

    same_pairs = apply_dual_subtitle_pairs(raw, default_pairs)
    assert isinstance(same_pairs, Ok)
    assert same_pairs.value == raw

    # Cap at 1080p + Cached-only + Custom Stream Audio Languages
    capped_res = apply_stream_preferences(
        raw,
        max_resolution="1080p",
        cached_only=True,
        audio_languages=["English", "Spanish", "German", "Korean"],
    )
    assert isinstance(capped_res, Ok)
    capped = capped_res.value
    assert capped["excludedResolutions"] == ["2160p", "1440p"]
    assert capped["excludeUncachedFromStreamTypes"] == ["debrid"]
    assert capped["requiredLanguages"] == [
        "Original",
        "English",
        "Spanish",
        "German",
        "Korean",
        "Dual Audio",
        "Multi",
        "Dubbed",
        "Unknown",
    ]
    assert inspect_stream_preferences(capped).max_resolution == "1080p"
    assert inspect_stream_preferences(capped).cached_only is True
    assert inspect_stream_preferences(capped).audio_languages == (
        "English",
        "Spanish",
        "German",
        "Korean",
    )
    assert isinstance(apply_stream_preferences(raw, max_resolution="480p"), Err)
    assert isinstance(apply_stream_preferences(raw, audio_languages=[]), Err)
    assert isinstance(apply_stream_preferences(raw, audio_languages=["Klingon"]), Err)

    # Custom 4-pair Strelingo setup + SubSource language sync + secret scrubbing round-trip
    custom_pairs = [("kor", "eng"), ("spa", "eng"), ("fra", "deu"), ("ara", "eng")]
    rebuilt_res = apply_dual_subtitle_pairs(capped, custom_pairs)
    assert isinstance(rebuilt_res, Ok)
    rebuilt = rebuilt_res.value
    assert inspect_dual_subtitle_pairs(rebuilt) == tuple(custom_pairs)

    dual_pack = next(st for st in inspect_aiostreams_packs(rebuilt) if st.spec.id == "dual_subtitles")
    assert dual_pack.present_presets == ("ssc", "sl1", "sl2", "sl3", "sl4")

    ssc_preset = next(p for p in rebuilt["presets"] if p["instanceId"] == "ssc")
    assert set(ssc_preset["options"]["language"]) == {
        "English",
        "Korean",
        "Spanish",
        "French",
        "German",
        "Arabic",
    }

    env = {
        "SECRET_TORBOX_API_KEY": "tb-secret-1234",
        "SECRET_TMDB_API_KEY": "tmdb-key-1234",
        "SECRET_TMDB_ACCESS_TOKEN": "tmdb-jwt-1234",
        "SECRET_TVDB_API_KEY": "tvdb-key-1234",
        "SECRET_RPDB_API_KEY": "t0-free-rpdb",
        "SECRET_ITSOUT_TOKEN": "itsout-token-1234",
        "SECRET_SUBSOURCE_API_KEY": "subsource-secret-9999",
    }
    inj = inject_secrets(rebuilt, env)
    assert isinstance(inj, Ok)
    scrubbed_doc, _ = harvest_and_scrub("aiostreams", inj.value, dict(env))
    assert scrubbed_doc == rebuilt

    assert isinstance(apply_dual_subtitle_pairs(raw, [("eng", "eng")]), Err)
    assert isinstance(apply_dual_subtitle_pairs(raw, [("klingon", "eng")]), Err)

    # CLI flags on `sw packs set`
    t = tmp_path / "polyglot"
    assert runner.invoke(app, ["init", str(t), "--from", str(EXAMPLE)]).exit_code == 0
    cli_res = runner.invoke(
        app,
        [
            "packs",
            "set",
            "--max-resolution",
            "720p",
            "--cached-only",
            "--audio-langs",
            "English,Spanish",
            "--dual-subs",
            "kor:eng,spa:eng",
            "--dir",
            str(t),
        ],
    )
    assert cli_res.exit_code == 0, cli_res.output
    list_res = runner.invoke(app, ["packs", "list", "--dir", str(t)], env={"COLUMNS": "200"})
    assert "Max 720p" in list_res.output
    assert "Cached Only" in list_res.output
    assert "Audio: English, Spanish" in list_res.output
    assert "KO→EN, ES→EN" in list_res.output
    assert runner.invoke(app, ["validate", "--dir", str(t)]).exit_code == 0
