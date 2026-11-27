"""Shared pytest fixtures — keep test runs isolated from the developer's `~/.config/stream-weaver`."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_workspace_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Routes per-user workspace config (`STREAM_WEAVER_CONFIG`) into `tmp_path` for every test."""
    monkeypatch.setenv("STREAM_WEAVER_CONFIG", str(tmp_path / "_sw_user_config.json"))
