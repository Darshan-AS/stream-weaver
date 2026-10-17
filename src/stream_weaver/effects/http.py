"""Typed HTTP client adapter using `httpx` with explicit `Result` returns."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from stream_weaver.core.errors import NetworkError
from stream_weaver.core.result import Err, Ok, Result

DEFAULT_USER_AGENT = "Stream-Weaver-CLI/2.0"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Immutable HTTP response wrapper."""

    status_code: int
    data: Any
    text: str


class HttpClientProtocol(Protocol):
    """Protocol for making HTTP requests from the imperative shell."""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        raw_body: bytes | None = None,
        timeout: float = 30.0,
    ) -> Result[HttpResponse, NetworkError]: ...


class HttpxClient:
    """Production `HttpClientProtocol` implementation backed by `httpx`."""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        raw_body: bytes | None = None,
        timeout: float = 30.0,
    ) -> Result[HttpResponse, NetworkError]:
        if not url.startswith(("https://", "http://")):
            return Err(NetworkError(message=f"Unsupported URL scheme for {url}", url=url))

        req_headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        if headers:
            req_headers.update(headers)

        content_bytes: bytes | None = raw_body
        if json_body is not None:
            content_bytes = json.dumps(json_body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    content=content_bytes,
                )
        except httpx.HTTPError as exc:
            return Err(
                NetworkError(
                    message=f"Network error connecting to {url}: {exc}",
                    url=url,
                )
            )

        raw_text = resp.text
        parsed: Any
        if raw_text.strip():
            try:
                parsed = resp.json()
            except ValueError:
                parsed = {"error": raw_text}
        else:
            parsed = {}

        return Ok(
            HttpResponse(
                status_code=resp.status_code,
                data=parsed,
                text=raw_text,
            )
        )
