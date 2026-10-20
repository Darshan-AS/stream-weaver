"""Filesystem and `.env` side-effect adapters for Stream Weaver GitOps."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stream_weaver.core.errors import StorageError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.secrets import merge_env_text, parse_env_text
from stream_weaver.core.types import JsonDict, is_json_dict


def load_env_file(env_path: Path) -> dict[str, str]:
    """Reads `.env` from disk and overlays any `SECRET_` / `AIO` variables from `os.environ`."""
    file_env: dict[str, str] = {}
    if env_path.exists():
        file_env = parse_env_text(env_path.read_text(encoding="utf-8"))
    for k, v in os.environ.items():
        if k.startswith(("SECRET_", "AIO", "NUVIO_")):
            file_env[k] = v
    return file_env


def save_env_updates(env_path: Path, updates: Mapping[str, str]) -> Result[None, StorageError]:
    """Merges `updates` into `env_path` while preserving comments and key ordering."""
    if not updates:
        return Ok(None)
    try:
        existing_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        merged_text = merge_env_text(existing_text, updates)
        env_path.write_text(merged_text, encoding="utf-8")
        return Ok(None)
    except OSError as exc:
        return Err(StorageError(message=f"Failed to update {env_path}: {exc}", path=str(env_path)))


def read_json_file(path: Path) -> Result[JsonDict, StorageError]:
    """Reads and parses a JSON object from `path`."""
    if not path.exists():
        return Err(StorageError(message=f"File not found: {path}", path=str(path)))
    try:
        raw = path.read_text(encoding="utf-8")
        parsed: Any = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return Err(StorageError(message=f"Failed to read JSON from {path}: {exc}", path=str(path)))

    if not is_json_dict(parsed):
        return Err(StorageError(message=f"Expected JSON object in {path}", path=str(path)))
    return Ok(parsed)


def write_json_file(path: Path, data: Mapping[str, Any]) -> Result[Path, StorageError]:
    """Formats `data` as 2-space indented JSON and writes it to `path`."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        formatted = json.dumps(dict(data), indent=2, ensure_ascii=False) + "\n"
        path.write_text(formatted, encoding="utf-8")
        return Ok(path)
    except OSError as exc:
        return Err(StorageError(message=f"Failed to write JSON to {path}: {exc}", path=str(path)))


def read_text_file(path: Path) -> Result[str, StorageError]:
    """Reads UTF-8 text from `path`."""
    if not path.exists():
        return Err(StorageError(message=f"File not found: {path}", path=str(path)))
    try:
        return Ok(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return Err(StorageError(message=f"Failed to read text from {path}: {exc}", path=str(path)))


def write_text_file(path: Path, content: str) -> Result[Path, StorageError]:
    """Writes UTF-8 text `content` to `path`, creating parent directories if needed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return Ok(path)
    except OSError as exc:
        return Err(StorageError(message=f"Failed to write text to {path}: {exc}", path=str(path)))

