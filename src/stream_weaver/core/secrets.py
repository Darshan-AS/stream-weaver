"""Declarative JSON-path secret lenses and pure `.env` transformations."""

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from stream_weaver.core.errors import SecretError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import EnvMap, JsonDict, ServiceId

SECRET_PLACEHOLDER_RE = re.compile(r"\{\{(SECRET_[A-Z0-9_]+)\}\}")
WATCHLY_URL_RE = re.compile(r"(watchly\.[^/]+/)([^/{}]+)(/manifest\.json)")
BINGECAT_URL_RE = re.compile(
    r"(bingecat\.[^/]+/(?:stremio/)?)((?!configure\b|static\b|public\b)[^/{}?]+)((?:/nuvio)?(?:/manifest\.json)?(?:\?[^\s\"']*)?)$"
)
BINGECAT_ID_RE = re.compile(r"^(com\.aicat\.)([^.:/{}]+)((?:\.[^:]+)?(?::.+)?)$")
MIN_SECRET_LEN = 4

ENV_OVERRIDE_PREFIXES: tuple[str, ...] = (
    "AIOSTREAMS_",
    "AIOMETADATA_",
    "AIOMANAGER_",
    "SECRET_",
    "ADDON_",
    "NUVIO_",
)


@dataclass(frozen=True, slots=True)
class SecretLens:
    """
    Declarative lens matching a JSON path pattern (with `*` wildcards)
    and computing the canonical `SECRET_*` environment variable name.
    """

    path: tuple[str, ...]
    derive_key: Callable[[tuple[str, ...], Mapping[str, Any]], str | None]
    regex_sub: tuple[re.Pattern[str], str] | None = None


def _sanitize_token(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", raw.upper()).strip("_")


def _camel_to_screaming_snake(name: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return _sanitize_token(spaced)


# ---------------------------------------------------------------------------
# Service-Specific Declarative Secret Lenses
# ---------------------------------------------------------------------------

_AIOSTREAMS_TOP_MAP: Mapping[str, str] = {
    "tmdbApiKey": "SECRET_TMDB_API_KEY",
    "tmdbAccessToken": "SECRET_TMDB_ACCESS_TOKEN",
    "tvdbApiKey": "SECRET_TVDB_API_KEY",
    "rpdbApiKey": "SECRET_RPDB_API_KEY",
    "topPosterApiKey": "SECRET_TOP_POSTER_API_KEY",
}

_AIOSTREAMS_SERVICE_CRED_MAP: Mapping[tuple[str, str], str] = {
    ("realdebrid", "apiKey"): "SECRET_RD_API_KEY",
    ("torbox", "apiKey"): "SECRET_TORBOX_API_KEY",
    ("alldebrid", "apiKey"): "SECRET_ALLDEBRID_API_KEY",
    ("premiumize", "apiKey"): "SECRET_PREMIUMIZE_API_KEY",
    ("easynews", "username"): "SECRET_EASYNEWS_USERNAME",
    ("easynews", "password"): "SECRET_EASYNEWS_PASSWORD",
}

def _const_key(env_key: str) -> Callable[[tuple[str, ...], Mapping[str, Any]], str | None]:
    def _inner(_path: tuple[str, ...], _parent: Mapping[str, Any]) -> str:
        return env_key

    return _inner


AIOSTREAMS_LENSES: tuple[SecretLens, ...] = (
    *(
        SecretLens(
            path=(field_name,),
            derive_key=_const_key(env_key),
        )
        for field_name, env_key in _AIOSTREAMS_TOP_MAP.items()
    ),
    SecretLens(
        path=("services", "*", "credentials", "*"),
        derive_key=lambda path, parent: _AIOSTREAMS_SERVICE_CRED_MAP.get(
            (str(parent.get("id", "")).lower(), path[-1]),
            f"SECRET_{_sanitize_token(str(parent.get('id', 'SVC')))}_{_sanitize_token(path[-1])}",
        ),
    ),
    SecretLens(
        path=("presets", "*", "options", "manifestUrl"),
        derive_key=lambda _, __: "SECRET_WATCHLY_TOKEN",
        regex_sub=(WATCHLY_URL_RE, r"\1{{SECRET_WATCHLY_TOKEN}}\3"),
    ),
    SecretLens(
        path=("presets", "*", "options", "manifestUrl"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_URL_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
)

_AIOMETADATA_KEY_MAP: Mapping[str, str] = {
    "tmdb": "SECRET_TMDB_API_KEY",
    "tvdb": "SECRET_TVDB_API_KEY",
    "rpdb": "SECRET_RPDB_API_KEY",
    "topPoster": "SECRET_TOP_POSTER_API_KEY",
    "fanart": "SECRET_FANART_API_KEY",
    "mdblist": "SECRET_MDBLIST_API_KEY",
    "gemini": "SECRET_GEMINI_API_KEY",
    "openrouter": "SECRET_OPENROUTER_API_KEY",
    "traktTokenId": "SECRET_TRAKT_TOKEN_ID",
    "simklTokenId": "SECRET_SIMKL_TOKEN_ID",
}

AIOMETADATA_LENSES: tuple[SecretLens, ...] = (
    SecretLens(
        path=("apiKeys", "*"),
        derive_key=lambda path, _: _AIOMETADATA_KEY_MAP.get(
            path[-1],
            f"SECRET_META_{_sanitize_token(path[-1])}",
        ),
    ),
)

_CREDENTIAL_FIELDS: frozenset[str] = frozenset(
    {"authKey", "accessToken", "refreshToken", "apiKey", "token", "password"}
)


def _aiomanager_account_auth_key(path: tuple[str, ...], _: Mapping[str, Any]) -> str:
    idx = int(path[1]) if path[1].isdigit() else 0
    return "SECRET_ACCOUNT_AUTH_KEY" if idx == 0 else f"SECRET_ACCOUNT_AUTH_KEY_{idx + 1}"


def _aiomanager_account_apikey(path: tuple[str, ...], parent: Mapping[str, Any]) -> str:
    raw_id = str(parent.get("id", path[1]))
    short_id = re.sub(r"[^A-Z0-9]+", "", raw_id.upper())[:8]
    return f"SECRET_AIOMANAGER_APIKEY_{short_id}"


def _aiomanager_top_apikey(path: tuple[str, ...], _: Mapping[str, Any]) -> str:
    short_id = re.sub(r"[^A-Z0-9]+", "", path[-1].upper())[:8]
    return f"SECRET_AIOMANAGER_APIKEY_{short_id}"


def _aiomanager_conn_cred(path: tuple[str, ...], parent: Mapping[str, Any]) -> str | None:
    cred_field = path[-1]
    if cred_field not in _CREDENTIAL_FIELDS:
        return None
    plat = _sanitize_token(str(parent.get("platform", "CONN")))
    return f"SECRET_{plat}_{_camel_to_screaming_snake(cred_field)}"


def _aiomanager_vault_key(path: tuple[str, ...], parent: Mapping[str, Any]) -> str:
    prov = _sanitize_token(str(parent.get("provider", "VAULT")))
    raw_id = str(parent.get("id", path[1]))
    short_id = re.sub(r"[^A-Z0-9]+", "", raw_id.upper())[:6]
    return f"SECRET_VAULT_{prov}_{short_id}"


AIOMANAGER_LENSES: tuple[SecretLens, ...] = (
    SecretLens(path=("apiKeys", "*"), derive_key=_aiomanager_top_apikey),
    SecretLens(path=("accounts", "*", "authKey"), derive_key=_aiomanager_account_auth_key),
    SecretLens(path=("accounts", "*", "apiKey"), derive_key=_aiomanager_account_apikey),
    SecretLens(
        path=("accounts", "*", "connections", "*", "credentials", "*"),
        derive_key=_aiomanager_conn_cred,
    ),
    SecretLens(path=("vault", "*", "value"), derive_key=_aiomanager_vault_key),
    SecretLens(
        path=("addons", "savedAddons", "*", "installUrl"),
        derive_key=lambda _, __: "SECRET_WATCHLY_TOKEN",
        regex_sub=(WATCHLY_URL_RE, r"\1{{SECRET_WATCHLY_TOKEN}}\3"),
    ),
    SecretLens(
        path=("accounts", "*", "addons", "*", "transportUrl"),
        derive_key=lambda _, __: "SECRET_WATCHLY_TOKEN",
        regex_sub=(WATCHLY_URL_RE, r"\1{{SECRET_WATCHLY_TOKEN}}\3"),
    ),
    SecretLens(
        path=("accountStates", "*", "installedAddons", "*", "installUrl"),
        derive_key=lambda _, __: "SECRET_WATCHLY_TOKEN",
        regex_sub=(WATCHLY_URL_RE, r"\1{{SECRET_WATCHLY_TOKEN}}\3"),
    ),
    SecretLens(
        path=("addons", "savedAddons", "*", "installUrl"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_URL_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
    SecretLens(
        path=("accounts", "*", "addons", "*", "transportUrl"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_URL_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
    SecretLens(
        path=("accountStates", "*", "installedAddons", "*", "installUrl"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_URL_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
    SecretLens(
        path=("addons", "savedAddons", "*", "manifest", "id"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_ID_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
    SecretLens(
        path=("accounts", "*", "addons", "*", "manifestId"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_ID_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
    SecretLens(
        path=("accounts", "*", "addons", "*", "manifest", "id"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_ID_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
    SecretLens(
        path=("accountStates", "*", "installedAddons", "*", "addonId"),
        derive_key=lambda _, __: "SECRET_BINGECAT_TOKEN",
        regex_sub=(BINGECAT_ID_RE, r"\1{{SECRET_BINGECAT_TOKEN}}\3"),
    ),
)

SERVICE_LENSES: Mapping[ServiceId, tuple[SecretLens, ...]] = {
    "aiostreams": AIOSTREAMS_LENSES,
    "aiometadata": AIOMETADATA_LENSES,
    "aiomanager": AIOMANAGER_LENSES,
}


# ---------------------------------------------------------------------------
# Pure Environment Parsing & Merging
# ---------------------------------------------------------------------------


def parse_env_text(text: str, process_env: EnvMap | None = None) -> dict[str, str]:
    """Purely parses `.env` file content and merges whitelisted process environment overrides."""
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and (
            (val.startswith('"') and val.endswith('"'))
            or (val.startswith("'") and val.endswith("'"))
        ):
            val = val[1:-1]
        env[key] = val

    if process_env:
        for k, v in process_env.items():
            if k.startswith(ENV_OVERRIDE_PREFIXES):
                env[k] = v
    return env


def merge_env_text(existing_text: str, updates: Mapping[str, str]) -> str:
    """
    Purely merges updated or newly harvested key-value pairs into `.env` file text,
    preserving all existing comments, blank lines, and ordering.
    """
    if not updates:
        return existing_text

    lines = existing_text.splitlines() if existing_text else []
    for secret_key, secret_val in updates.items():
        if not secret_val or secret_val.startswith("{{SECRET_"):
            continue
        pattern = re.compile(rf"^\s*{re.escape(secret_key)}\s*=")
        replaced = False
        for idx, line in enumerate(lines):
            if pattern.match(line):
                lines[idx] = f"{secret_key}={secret_val}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{secret_key}={secret_val}")

    return "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# Pure Tree Traversal for Secret Extraction, Scrubbing & Injection
# ---------------------------------------------------------------------------


def _path_matches(pattern: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    if len(pattern) != len(actual):
        return False
    return all(p == "*" or p == a for p, a in zip(pattern, actual, strict=True))


def _find_existing_secret_key(env: Mapping[str, str], secret_val: str) -> str | None:
    """If `secret_val` is already tracked under an existing `SECRET_*` key, reuse that key."""
    for existing_k, existing_v in env.items():
        if existing_k.startswith("SECRET_") and existing_v == secret_val:
            return existing_k
    return None


def harvest_and_scrub(
    service: ServiceId,
    config: Mapping[str, Any],
    env: EnvMap,
) -> tuple[JsonDict, dict[str, str]]:
    """
    Pure function: traverses `config` using the service's declarative `SecretLens` rules.
    Returns `(scrubbed_config, updated_env)` without mutating inputs or touching disk.
    """
    lenses = SERVICE_LENSES.get(service, ())
    working_env: dict[str, str] = dict(env)

    def _record_secret(proposed_key: str, raw_val: str) -> str:
        existing_key = _find_existing_secret_key(working_env, raw_val)
        if existing_key:
            return existing_key
        working_env[proposed_key] = raw_val
        return proposed_key

    def _walk(
        node: Any,
        path: tuple[str, ...],
        context_entity: Mapping[str, Any],
    ) -> Any:
        if isinstance(node, dict):
            next_context = (
                node
                if ("id" in node or "platform" in node or "provider" in node)
                else context_entity
            )
            return {
                k: _walk(v, (*path, str(k)), next_context)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [
                _walk(item, (*path, str(idx)), context_entity)
                for idx, item in enumerate(node)
            ]
        if isinstance(node, str) and node and not node.startswith("{{SECRET_"):
            for lens in lenses:
                if not _path_matches(lens.path, path):
                    continue
                derived_key = lens.derive_key(path, context_entity)
                if not derived_key:
                    continue
                if lens.regex_sub is not None:
                    regex, _ = lens.regex_sub
                    match = regex.search(node)
                    if match:
                        token_val = match.group(2)
                        if len(token_val) >= MIN_SECRET_LEN and not token_val.startswith("{{SECRET_"):
                            actual_key = _record_secret(derived_key, token_val)
                            return regex.sub(rf"\1{{{{{actual_key}}}}}\3", node)
                elif len(node) >= MIN_SECRET_LEN:
                    actual_key = _record_secret(derived_key, node)
                    return f"{{{{{actual_key}}}}}"
        return node

    scrubbed_tree = _walk(copy.deepcopy(dict(config)), (), config)

    # Second pass: scrub any remaining string leaves that contain known SECRET_* values
    known_secrets = sorted(
        (
            (k, v)
            for k, v in working_env.items()
            if k.startswith("SECRET_") and len(v) >= MIN_SECRET_LEN and not v.startswith("{{SECRET_")
        ),
        key=lambda item: len(item[1]),
        reverse=True,
    )

    def _scrub_known_leaves(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _scrub_known_leaves(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_scrub_known_leaves(item) for item in node]
        if isinstance(node, str) and node:
            out = node
            for s_key, s_val in known_secrets:
                if s_val in out:
                    out = out.replace(s_val, f"{{{{{s_key}}}}}")
            return out
        return node

    final_tree: JsonDict = _scrub_known_leaves(scrubbed_tree)
    return final_tree, working_env


def inject_secrets(
    config: Mapping[str, Any],
    env: EnvMap,
) -> Result[JsonDict, SecretError]:
    """
    Pure function: recursively substitutes all `{{SECRET_*}}` placeholders in `config`
    using values from `env`. Returns `Err(SecretError)` if any placeholders are missing or empty.
    """
    missing: set[str] = set()

    def _replace_str(text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            key = match.group(1)
            val = env.get(key, "")
            if not val:
                missing.add(key)
                return match.group(0)
            return val

        return SECRET_PLACEHOLDER_RE.sub(_repl, text)

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, str):
            return _replace_str(node)
        return node

    injected: JsonDict = _walk(copy.deepcopy(dict(config)))
    if missing:
        sorted_missing = tuple(sorted(missing))
        return Err(
            SecretError(
                message=f"Missing required secret(s) in .env: {', '.join(sorted_missing)}",
                missing_keys=sorted_missing,
            )
        )
    return Ok(injected)
