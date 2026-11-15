"""
Nuvio session pool — one authenticated Supabase session per *login*, shared by every profile
that uses that login.

Why: Supabase rotates refresh tokens on use. If two AIOManager connections carry the same
refresh token and both independently hit a 401 during one `sw push`, the second refresh is
rejected and the persisted token is stale. The pool authenticates once per login (keyed by the
refresh token, falling back to the access token), serialises refreshes behind a lock, and reports
rotated tokens exactly once under the env keys they were loaded from.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from stream_weaver.core.errors import NetworkError, StreamWeaverError
from stream_weaver.core.logins import refreshed_token_env_keys
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import EnvMap
from stream_weaver.effects.http import HttpClientProtocol

NUVIO_API = "https://api.nuvio.tv"
NUVIO_PROFILES_RPC = f"{NUVIO_API}/rest/v1/rpc/sync_pull_profiles"
NUVIO_TOKEN_URL = f"{NUVIO_API}/auth/v1/token"
# Public Supabase `anon` client key shipped in Nuvio clients (RLS is enforced via user Bearer JWTs).
DEFAULT_NUVIO_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzgxNTIxMzQ2LCJleHAiOjE5MzkyMDEzNDZ9."
    "tmQaj682pwzehpqlgCDMnySOqiUvpgRbrE43T4VJpDI"
)


def resolve_nuvio_anon_key(env: EnvMap) -> str:
    """Returns `NUVIO_SUPABASE_ANON_KEY` from `env`, falling back to Nuvio's public client key."""
    return env.get("NUVIO_SUPABASE_ANON_KEY", "").strip() or DEFAULT_NUVIO_SUPABASE_ANON_KEY


@dataclass(frozen=True, slots=True)
class NuvioSession:
    """An authenticated Nuvio session: request headers plus the login's profile list."""

    login_key: str
    headers: Mapping[str, str]
    profiles: tuple[Mapping[str, Any], ...]
    refreshed_secrets: Mapping[str, str] = field(default_factory=dict)

    def profile_index(
        self, *, profile_id: str | None = None, name: str | None = None
    ) -> int | None:
        """Resolves a Nuvio `profile_index` by profile UUID or (case-insensitive) display name."""
        for p in self.profiles:
            pid = str(p.get("id") or "")
            pname = str(p.get("name") or p.get("profile_name") or "")
            if (profile_id and pid == str(profile_id)) or (name and pname.lower() == name.lower()):
                raw = p.get("profile_index") or p.get("profileIndex")
                try:
                    return int(raw) if raw is not None else None
                except (TypeError, ValueError):
                    return None
        return None


class NuvioSessionPool:
    """Thread-safe cache of `NuvioSession`s keyed by login identity (refresh token)."""

    def __init__(self, http_client: HttpClientProtocol, anon_key: str) -> None:
        self._http = http_client
        self._anon_key = anon_key
        self._lock = threading.Lock()
        self._sessions: dict[str, Result[NuvioSession]] = {}

    @property
    def anon_key(self) -> str:
        return self._anon_key

    def acquire(
        self, *, access_token: str, refresh_token: str, env: EnvMap
    ) -> Result[NuvioSession]:
        """Returns the (possibly cached) session for the login identified by these tokens."""
        if not self._anon_key:
            return Err(
                StreamWeaverError(
                    "NUVIO_SUPABASE_ANON_KEY is not set in .env; skipping direct Nuvio sync."
                )
            )
        if not access_token:
            return Err(StreamWeaverError("Nuvio login has no accessToken."))
        login_key = refresh_token or access_token
        with self._lock:
            cached = self._sessions.get(login_key)
            if cached is not None:
                return cached
            session = self._authenticate(access_token, refresh_token, env)
            self._sessions[login_key] = session
            return session

    def refreshed_secrets(self) -> dict[str, str]:
        """Every rotated token discovered so far (each login contributes at most one pair)."""
        out: dict[str, str] = {}
        with self._lock:
            for res in self._sessions.values():
                if isinstance(res, Ok):
                    out.update(res.value.refreshed_secrets)
        return out

    # ------------------------------------------------------------------ internals

    def _pull_profiles(self, headers: Mapping[str, str]) -> Result[tuple[int, Any]]:
        match self._http.request_json("POST", NUVIO_PROFILES_RPC, headers=headers, json_body={}):
            case Err() as err:
                return err
            case Ok(resp):
                return Ok((resp.status_code, resp.data))

    def _authenticate(
        self, access_token: str, refresh_token: str, env: EnvMap
    ) -> Result[NuvioSession]:
        headers: dict[str, str] = {
            "apikey": self._anon_key,
            "Authorization": f"Bearer {access_token}",
        }
        refreshed: dict[str, str] = {}

        match self._pull_profiles(headers):
            case Err() as err:
                return err
            case Ok((status, profiles)):
                pass

        if status == 401 and refresh_token:
            match self._http.request_json(
                "POST",
                f"{NUVIO_TOKEN_URL}?grant_type=refresh_token",
                headers={"apikey": self._anon_key},
                json_body={"refresh_token": refresh_token},
            ):
                case Ok(r) if (
                    r.status_code == 200 and isinstance(r.data, dict) and r.data.get("access_token")
                ):
                    new_access = str(r.data["access_token"])
                    new_refresh = str(r.data.get("refresh_token") or "")
                    access_key, refresh_key = refreshed_token_env_keys(
                        env, access_token, refresh_token
                    )
                    refreshed[access_key] = new_access
                    if new_refresh:
                        refreshed[refresh_key] = new_refresh
                    headers["Authorization"] = f"Bearer {new_access}"
                    match self._pull_profiles(headers):
                        case Err() as err:
                            return err
                        case Ok((status, profiles)):
                            pass
                case Ok(r):
                    return Err(
                        NetworkError(
                            message=f"Nuvio token refresh failed (HTTP {r.status_code}); re-run `sw login nuvio`",
                            status_code=r.status_code,
                            url=NUVIO_TOKEN_URL,
                        )
                    )
                case Err() as err:
                    return err

        if status != 200:
            return Err(
                NetworkError(
                    message=f"Nuvio profile listing returned HTTP {status}; re-run `sw login nuvio`",
                    status_code=status,
                    url=NUVIO_PROFILES_RPC,
                )
            )
        profile_list = (
            tuple(p for p in profiles if isinstance(p, dict)) if isinstance(profiles, list) else ()
        )
        return Ok(
            NuvioSession(
                login_key=refresh_token or access_token,
                headers=headers,
                profiles=profile_list,
                refreshed_secrets=refreshed,
            )
        )


def password_grant(
    http_client: HttpClientProtocol, anon_key: str, email: str, password: str
) -> Result[tuple[str, str]]:
    """Supabase email/password sign-in → `(access_token, refresh_token)`."""
    match http_client.request_json(
        "POST",
        f"{NUVIO_TOKEN_URL}?grant_type=password",
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        json_body={"email": email, "password": password},
    ):
        case Err() as err:
            return err
        case Ok(r) if (
            r.status_code == 200 and isinstance(r.data, dict) and r.data.get("access_token")
        ):
            return Ok((str(r.data["access_token"]), str(r.data.get("refresh_token") or "")))
        case Ok(r):
            detail = (
                r.data.get("error_description") or r.data.get("msg")
                if isinstance(r.data, dict)
                else r.text
            )
            return Err(
                NetworkError(
                    message=f"Nuvio sign-in failed (HTTP {r.status_code}): {str(detail)[:200]}",
                    status_code=r.status_code,
                    url=NUVIO_TOKEN_URL,
                )
            )
