#!/usr/bin/env python3
"""
AIO Configs GitOps CLI (AIOStreams, AIOMetadata, AIOManager)

Supports:
  - pull:     Download live configs from public/self-hosted instances, auto-harvest & scrub secrets
  - push:     Inject secrets from .env & push configs to instances (in-place or --create)
  - sanitize: Auto-harvest & scrub secrets from local JSON files in configs/
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"
CONFIGS_DIR = ROOT_DIR / "configs"

SERVICES = ("aiostreams", "aiometadata", "aiomanager")


def load_env(env_path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            env[key] = val
    # Allow process environment variables to override .env
    for k, v in os.environ.items():
        if k.startswith(("AIOSTREAMS_", "AIOMETADATA_", "AIOMANAGER_", "SECRET_")):
            env[k] = v
    return env


def persist_secret_to_env(env_path: Path, env: Dict[str, str], secret_key: str, secret_val: str) -> None:
    """Saves a newly discovered secret into .env so the user doesn't have to copy-paste it manually."""
    if not secret_val or not isinstance(secret_val, str) or secret_val.startswith("{{SECRET_"):
        return
    if len(secret_val) < 4:
        return

    # If this exact secret value is already tracked under another SECRET_* key, reuse that
    for existing_k, existing_v in env.items():
        if existing_k.startswith("SECRET_") and existing_v == secret_val:
            return

    env[secret_key] = secret_val
    if not env_path.exists():
        env_path.write_text(f"{secret_key}={secret_val}\n", encoding="utf-8")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(secret_key)}\s*=")
    updated = False
    for idx, line in enumerate(lines):
        if pattern.match(line):
            lines[idx] = f"{secret_key}={secret_val}"
            updated = True
            break

    if not updated:
        lines.append(f"{secret_key}={secret_val}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def auto_harvest_secrets(service: str, cfg: Dict[str, Any], env: Dict[str, str]) -> None:
    """Automatically detects API keys in pulled/local configs and records them in .env."""
    if service == "aiostreams":
        top_map = {
            "tmdbApiKey": "SECRET_TMDB_API_KEY",
            "tmdbAccessToken": "SECRET_TMDB_ACCESS_TOKEN",
            "tvdbApiKey": "SECRET_TVDB_API_KEY",
            "rpdbApiKey": "SECRET_RPDB_API_KEY",
            "topPosterApiKey": "SECRET_TOP_POSTER_API_KEY",
        }
        for field, env_key in top_map.items():
            val = cfg.get(field)
            if isinstance(val, str) and val:
                persist_secret_to_env(ENV_FILE, env, env_key, val)

        service_key_map = {
            ("realdebrid", "apiKey"): "SECRET_RD_API_KEY",
            ("torbox", "apiKey"): "SECRET_TORBOX_API_KEY",
            ("alldebrid", "apiKey"): "SECRET_ALLDEBRID_API_KEY",
            ("premiumize", "apiKey"): "SECRET_PREMIUMIZE_API_KEY",
            ("easynews", "username"): "SECRET_EASYNEWS_USERNAME",
            ("easynews", "password"): "SECRET_EASYNEWS_PASSWORD",
        }
        for svc in cfg.get("services", []):
            if not isinstance(svc, dict):
                continue
            sid = str(svc.get("id", "")).lower()
            creds = svc.get("credentials")
            if isinstance(creds, dict):
                for ck, cv in creds.items():
                    if isinstance(cv, str) and cv:
                        env_key = service_key_map.get(
                            (sid, ck),
                            f"SECRET_{re.sub(r'[^A-Z0-9]+', '_', sid.upper())}_{re.sub(r'[^A-Z0-9]+', '_', ck.upper())}",
                        )
                        persist_secret_to_env(ENV_FILE, env, env_key, cv)

    elif service == "aiometadata":
        meta_map = {
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
        api_keys = cfg.get("apiKeys")
        if isinstance(api_keys, dict):
            for k, v in api_keys.items():
                if isinstance(v, str) and v:
                    env_key = meta_map.get(
                        k, f"SECRET_META_{re.sub(r'[^A-Z0-9]+', '_', k.upper())}"
                    )
                    persist_secret_to_env(ENV_FILE, env, env_key, v)

    elif service == "aiomanager":
        api_keys = cfg.get("apiKeys")
        if isinstance(api_keys, dict):
            for acc_id, key_val in api_keys.items():
                if isinstance(key_val, str) and key_val:
                    short_id = re.sub(r"[^A-Z0-9]+", "", acc_id.upper())[:8]
                    persist_secret_to_env(
                        ENV_FILE, env, f"SECRET_AIOMANAGER_APIKEY_{short_id}", key_val
                    )

        for idx, acc in enumerate(cfg.get("accounts", [])):
            if not isinstance(acc, dict):
                continue
            auth_key = acc.get("authKey")
            if isinstance(auth_key, str) and auth_key:
                key_name = "SECRET_ACCOUNT_AUTH_KEY" if idx == 0 else f"SECRET_ACCOUNT_AUTH_KEY_{idx + 1}"
                persist_secret_to_env(ENV_FILE, env, key_name, auth_key)
            acc_api_key = acc.get("apiKey")
            if isinstance(acc_api_key, str) and acc_api_key:
                short_id = re.sub(r"[^A-Z0-9]+", "", str(acc.get("id", idx)).upper())[:8]
                persist_secret_to_env(
                    ENV_FILE, env, f"SECRET_AIOMANAGER_APIKEY_{short_id}", acc_api_key
                )
            for conn in acc.get("connections", []):
                if not isinstance(conn, dict):
                    continue
                plat = re.sub(r"[^A-Z0-9]+", "_", str(conn.get("platform", "CONN")).upper()).strip("_")
                creds = conn.get("credentials")
                if isinstance(creds, dict):
                    for ck in ("authKey", "accessToken", "refreshToken", "apiKey", "token", "password"):
                        cv = creds.get(ck)
                        if isinstance(cv, str) and cv:
                            ck_upper = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", ck).upper()
                            persist_secret_to_env(
                                ENV_FILE, env, f"SECRET_{plat}_{ck_upper}", cv
                            )

        for idx, v_item in enumerate(cfg.get("vault", [])):
            if not isinstance(v_item, dict):
                continue
            val = v_item.get("value")
            if isinstance(val, str) and val:
                prov = re.sub(r"[^A-Z0-9]+", "_", str(v_item.get("provider", "VAULT")).upper()).strip("_")
                v_id = re.sub(r"[^A-Z0-9]+", "", str(v_item.get("id", idx)).upper())[:6]
                persist_secret_to_env(
                    ENV_FILE, env, f"SECRET_VAULT_{prov}_{v_id}", val
                )


def get_secrets_map(env: Dict[str, str]) -> Dict[str, str]:
    """Returns mapping of SECRET_KEY_NAME -> actual_secret_value for non-empty secrets."""
    secrets: Dict[str, str] = {}
    for k, v in env.items():
        if k.startswith("SECRET_") and v and len(v) >= 4:
            secrets[k] = v
    return secrets


def scrub_secrets_in_text(text: str, env: Dict[str, str]) -> str:
    """Replaces raw secret values with {{SECRET_NAME}} placeholders."""
    secrets = get_secrets_map(env)
    # Sort by secret length descending so longer secrets match before substrings
    for key, val in sorted(secrets.items(), key=lambda item: len(item[1]), reverse=True):
        escaped_val = json.dumps(val)[1:-1]
        text = text.replace(escaped_val, f"{{{{{key}}}}}")
        text = text.replace(val, f"{{{{{key}}}}}")
    return text


def inject_secrets_in_text(text: str, env: Dict[str, str]) -> str:
    """Replaces {{SECRET_NAME}} placeholders with raw secret values from .env."""
    pattern = re.compile(r"\{\{(SECRET_[A-Z0-9_]+)\}\}")
    missing: List[str] = []

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        val = env.get(key, "")
        if not val:
            missing.append(key)
            return match.group(0)
        return json.dumps(val)[1:-1]

    result = pattern.sub(replacer, text)
    if missing:
        unique_missing = sorted(set(missing))
        raise RuntimeError(
            f"Missing required secret(s) in .env for placeholder(s): {', '.join(unique_missing)}"
        )
    return result


def derive_aiomanager_sync_token(password: str, salt: Optional[str] = None) -> str:
    """
    Matches AIOManager's client-side deriveSyncToken(password, salt) in src/lib/crypto.ts:
      SHA-256(password + (salt ? `:sync-auth-token:${salt}` : ':sync-auth-token'))
    """
    pepper = f":sync-auth-token:{salt}" if salt else ":sync-auth-token"
    data = (str(password) + pepper).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def aiomanager_decrypt_envelope(envelope: Dict[str, Any], password: str) -> Dict[str, Any]:
    """
    Decrypts AIOManager's client-encrypted Cloud Sync payload using Node's built-in crypto & zlib
    (PBKDF2-HMAC-SHA256 600,000 iterations + AES-256-GCM + raw deflate).
    """
    if not (envelope.get("isEncrypted") and envelope.get("data")):
        return dict(envelope)

    node_script = r"""
const crypto = require('crypto');
const zlib = require('zlib');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const { dataB64, password, syncSaltB64, compressed } = input;

const salt = syncSaltB64
  ? Buffer.from(syncSaltB64, 'base64')
  : Buffer.from('aiomanager-sync-key-v2', 'utf8');

const key = crypto.pbkdf2Sync(String(password), salt, 600000, 32, 'sha256');
const combined = Buffer.from(dataB64, 'base64');
const iv = combined.subarray(0, 12);
const tag = combined.subarray(combined.length - 16);
const ciphertext = combined.subarray(12, combined.length - 16);

const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
decipher.setAuthTag(tag);
const decryptedBuf = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
const decryptedStr = decryptedBuf.toString('utf8');

let jsonStr = decryptedStr;
if (compressed) {
  const compBuf = Buffer.from(decryptedStr, 'base64');
  try {
    jsonStr = zlib.inflateRawSync(compBuf).toString('utf8');
  } catch {
    jsonStr = zlib.inflateSync(compBuf).toString('utf8');
  }
}
process.stdout.write(jsonStr);
"""
    payload = json.dumps(
        {
            "dataB64": envelope["data"],
            "password": password,
            "syncSaltB64": envelope.get("syncSalt"),
            "compressed": bool(envelope.get("compressed")),
        }
    )
    proc = subprocess.run(
        ["node", "-e", "const fs = require('fs');\n" + node_script],
        input=payload,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to decrypt AIOManager sync payload: {proc.stderr.strip()}")

    decrypted_obj = json.loads(proc.stdout)
    if envelope.get("syncSalt"):
        decrypted_obj["_syncSalt"] = envelope["syncSalt"]
    return decrypted_obj


def aiomanager_encrypt_envelope(state_obj: Dict[str, Any], password: str) -> Dict[str, Any]:
    """
    Encrypts a readable AIOManager JSON state back into the AES-256-GCM + PBKDF2 envelope
    expected by POST /api/sync/:id.
    """
    clean_state = dict(state_obj)
    sync_salt_b64 = clean_state.pop("_syncSalt", None)
    for k in ("force", "contentHint"):
        clean_state.pop(k, None)

    node_script = r"""
const crypto = require('crypto');
const zlib = require('zlib');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const { stateObj, password, syncSaltB64 } = input;

const syncedAt = new Date().toISOString();
const stampedObj = { ...stateObj, syncedAt };
const stampedStr = JSON.stringify(stampedObj);

const salt = syncSaltB64
  ? Buffer.from(syncSaltB64, 'base64')
  : Buffer.from('aiomanager-sync-key-v2', 'utf8');

let payloadStr = stampedStr;
let isCompressed = false;
if (Buffer.byteLength(stampedStr, 'utf8') > 51200) {
  payloadStr = zlib.deflateRawSync(Buffer.from(stampedStr, 'utf8')).toString('base64');
  isCompressed = true;
}

const key = crypto.pbkdf2Sync(String(password), salt, 600000, 32, 'sha256');
const iv = crypto.randomBytes(12);
const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
const ct = Buffer.concat([cipher.update(payloadStr, 'utf8'), cipher.final()]);
const tag = cipher.getAuthTag();
const combined = Buffer.concat([iv, ct, tag]).toString('base64');

const apiKeys = {};
if (Array.isArray(stateObj.accounts)) {
  for (const acc of stateObj.accounts) {
    if (acc && acc.id && acc.apiKey) {
      apiKeys[acc.id] = acc.apiKey;
    }
  }
}

const out = {
  data: combined,
  isEncrypted: true,
  compressed: isCompressed,
  ...(syncSaltB64 ? { syncSalt: syncSaltB64 } : {}),
  syncedAt,
  force: true,
  apiKeys,
};
process.stdout.write(JSON.stringify(out));
"""
    payload = json.dumps(
        {
            "stateObj": clean_state,
            "password": password,
            "syncSaltB64": sync_salt_b64,
        }
    )
    proc = subprocess.run(
        ["node", "-e", "const fs = require('fs');\n" + node_script],
        input=payload,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to encrypt AIOManager sync payload: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def normalize_aiostreams_config(data: Any) -> Dict[str, Any]:
    """Unwraps API/UI wrappers and strips volatile server fields for clean Git diffs."""
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object for AIOStreams config.")
    if "data" in data and isinstance(data["data"], dict) and "userData" in data["data"]:
        cfg = dict(data["data"]["userData"])
    elif "userData" in data and isinstance(data["userData"], dict):
        cfg = dict(data["userData"])
    elif "config" in data and isinstance(data["config"], dict):
        cfg = dict(data["config"])
    else:
        cfg = dict(data)

    # Strip volatile fields so Git diffs stay clean and portable across instances
    for volatile_key in ("uuid", "encryptedPassword", "accessKey", "ip"):
        cfg.pop(volatile_key, None)
    return cfg


def normalize_aiometadata_config(data: Any) -> Dict[str, Any]:
    """Unwraps API/UI wrappers and strips volatile timestamps for clean Git diffs."""
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object for AIOMetadata config.")
    if "config" in data and isinstance(data["config"], dict):
        cfg = dict(data["config"])
    else:
        cfg = dict(data)

    for volatile_key in ("lastModified", "configVersion"):
        cfg.pop(volatile_key, None)
    return cfg


def normalize_aiomanager_config(data: Any) -> Dict[str, Any]:
    """Strips volatile sync timestamps from AIOManager state."""
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object for AIOManager config.")
    cfg = dict(data)
    for volatile_key in ("syncedAt", "contentHint", "force"):
        cfg.pop(volatile_key, None)
    return cfg


def http_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Any]:
    req_headers = {
        "Accept": "application/json",
        "User-Agent": "AIO-GitOps-CLI/1.0",
    }
    if headers:
        req_headers.update(headers)

    data_bytes = None
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip() else {"error": err.reason}
        except json.JSONDecodeError:
            parsed = {"error": raw or err.reason}
        return err.code, parsed
    except urllib.error.URLError as err:
        raise RuntimeError(f"Network error connecting to {url}: {err.reason}") from err


def save_clean_json(service: str, raw_obj: Dict[str, Any], env: Dict[str, str]) -> Path:
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    auto_harvest_secrets(service, raw_obj, env)
    target = CONFIGS_DIR / f"{service}.json"
    formatted = json.dumps(raw_obj, indent=2, ensure_ascii=False)
    scrubbed = scrub_secrets_in_text(formatted, env)
    target.write_text(scrubbed + "\n", encoding="utf-8")
    return target


def load_injected_json(service: str, env: Dict[str, str]) -> Dict[str, Any]:
    target = CONFIGS_DIR / f"{service}.json"
    if not target.exists():
        raise FileNotFoundError(f"Config file not found: {target}")
    raw_text = target.read_text(encoding="utf-8")
    injected_text = inject_secrets_in_text(raw_text, env)
    return json.loads(injected_text)


# ==============================================================================
# PULL IMPLEMENTATIONS
# ==============================================================================


def pull_aiostreams(env: Dict[str, str]) -> None:
    base_url = env.get("AIOSTREAMS_URL", "").rstrip("/")
    uuid = env.get("AIOSTREAMS_UUID", "")
    password = env.get("AIOSTREAMS_PASSWORD", "")
    if not (base_url and uuid and password):
        print("⏭️  [AIOStreams] Skipping pull: AIOSTREAMS_URL, AIOSTREAMS_UUID, or AIOSTREAMS_PASSWORD not set in .env")
        return

    token = base64.b64encode(f"{uuid}:{password}".encode("utf-8")).decode("ascii")
    url = f"{base_url}/api/v1/user?raw=true"
    print(f"⬇️  [AIOStreams] Pulling config for UUID {uuid} from {base_url}...")
    status, resp = http_json("GET", url, headers={"Authorization": f"Basic {token}"})
    if status != 200:
        raise RuntimeError(f"[AIOStreams] Pull failed (HTTP {status}): {json.dumps(resp)}")

    clean_cfg = normalize_aiostreams_config(resp)
    out_path = save_clean_json("aiostreams", clean_cfg, env)
    print(f"✅ [AIOStreams] Saved scrubbed config to {out_path.relative_to(ROOT_DIR)}")


def pull_aiometadata(env: Dict[str, str]) -> None:
    base_url = env.get("AIOMETADATA_URL", "").rstrip("/")
    uuid = env.get("AIOMETADATA_UUID", "")
    password = env.get("AIOMETADATA_PASSWORD", "")
    addon_password = env.get("AIOMETADATA_ADDON_PASSWORD", "")
    if not (base_url and uuid and password):
        print("⏭️  [AIOMetadata] Skipping pull: AIOMETADATA_URL, AIOMETADATA_UUID, or AIOMETADATA_PASSWORD not set in .env")
        return

    url = f"{base_url}/api/config/load/{uuid}"
    body: Dict[str, Any] = {"password": password}
    if addon_password:
        body["addonPassword"] = addon_password

    print(f"⬇️  [AIOMetadata] Pulling config for UUID {uuid} from {base_url}...")
    status, resp = http_json("POST", url, body=body)
    if status != 200:
        raise RuntimeError(f"[AIOMetadata] Pull failed (HTTP {status}): {json.dumps(resp)}")

    clean_cfg = normalize_aiometadata_config(resp)
    out_path = save_clean_json("aiometadata", clean_cfg, env)
    print(f"✅ [AIOMetadata] Saved scrubbed config to {out_path.relative_to(ROOT_DIR)}")


def pull_aiomanager(env: Dict[str, str]) -> None:
    base_url = env.get("AIOMANAGER_URL", "").rstrip("/")
    uuid = env.get("AIOMANAGER_UUID", "")
    password = env.get("AIOMANAGER_SYNC_PASSWORD", "")
    if not (base_url and uuid and password):
        print("⏭️  [AIOManager] Skipping pull: AIOMANAGER_URL, AIOMANAGER_UUID, or AIOMANAGER_SYNC_PASSWORD not set in .env")
        return

    url = f"{base_url}/api/sync/{uuid}"
    print(f"⬇️  [AIOManager] Pulling sync state for UUID {uuid} from {base_url}...")

    # AIOManager hashes the master password client-side via deriveSyncToken(password)
    derived_token = derive_aiomanager_sync_token(password)
    status, resp = http_json("GET", url, headers={"x-sync-password": derived_token})
    if status == 401:
        # Fallback in case the user placed a pre-derived token or legacy password in .env
        status, resp = http_json("GET", url, headers={"x-sync-password": password})

    if status != 200:
        raise RuntimeError(f"[AIOManager] Pull failed (HTTP {status}): {json.dumps(resp)}")

    decrypted_state = aiomanager_decrypt_envelope(resp, password)
    clean_cfg = normalize_aiomanager_config(decrypted_state)
    out_path = save_clean_json("aiomanager", clean_cfg, env)
    print(f"✅ [AIOManager] Decrypted & saved scrubbed config to {out_path.relative_to(ROOT_DIR)}")


# ==============================================================================
# PUSH IMPLEMENTATIONS
# ==============================================================================


def push_aiostreams(env: Dict[str, str], create: bool = False) -> None:
    cfg_file = CONFIGS_DIR / "aiostreams.json"
    if not cfg_file.exists():
        print(f"⏭️  [AIOStreams] Skipping push: {cfg_file.relative_to(ROOT_DIR)} does not exist")
        return

    base_url = env.get("AIOSTREAMS_URL", "").rstrip("/")
    uuid = env.get("AIOSTREAMS_UUID", "")
    password = env.get("AIOSTREAMS_PASSWORD", "")
    addon_password = env.get("AIOSTREAMS_ADDON_PASSWORD", "")

    if not base_url or not password or (not create and not uuid):
        print("⏭️  [AIOStreams] Skipping push: Missing AIOSTREAMS_URL, AIOSTREAMS_PASSWORD, or AIOSTREAMS_UUID in .env")
        return

    raw_cfg = normalize_aiostreams_config(load_injected_json("aiostreams", env))
    if addon_password:
        raw_cfg["addonPassword"] = addon_password

    if create:
        url = f"{base_url}/api/v1/user"
        print(f"🚀 [AIOStreams] Creating new user config on {base_url}...")
        status, resp = http_json("POST", url, body={"config": raw_cfg, "password": password})
        if status not in (200, 201):
            raise RuntimeError(f"[AIOStreams] Create failed (HTTP {status}): {json.dumps(resp)}")
        data = resp.get("data", {})
        new_uuid = data.get("uuid")
        enc_pass = data.get("encryptedPassword")
        manifest_url = f"{base_url}/stremio/{new_uuid}/{enc_pass}/manifest.json"
        print(f"✅ [AIOStreams] Created! New UUID: {new_uuid}")
        print(f"🔗 [AIOStreams] Install URL: {manifest_url}")
    else:
        url = f"{base_url}/api/v1/user"
        token = base64.b64encode(f"{uuid}:{password}".encode("utf-8")).decode("ascii")
        print(f"⬆️  [AIOStreams] Pushing in-place update for UUID {uuid} to {base_url}...")
        status, resp = http_json(
            "PUT",
            url,
            headers={"Authorization": f"Basic {token}"},
            body={"config": raw_cfg},
        )
        if status != 200:
            raise RuntimeError(f"[AIOStreams] Push failed (HTTP {status}): {json.dumps(resp)}")
        print(f"✅ [AIOStreams] Successfully updated UUID {uuid} in-place (Install URL unchanged)")


def push_aiometadata(env: Dict[str, str], create: bool = False) -> None:
    cfg_file = CONFIGS_DIR / "aiometadata.json"
    if not cfg_file.exists():
        print(f"⏭️  [AIOMetadata] Skipping push: {cfg_file.relative_to(ROOT_DIR)} does not exist")
        return

    base_url = env.get("AIOMETADATA_URL", "").rstrip("/")
    uuid = env.get("AIOMETADATA_UUID", "")
    password = env.get("AIOMETADATA_PASSWORD", "")
    addon_password = env.get("AIOMETADATA_ADDON_PASSWORD", "")

    if not base_url or not password or (not create and not uuid):
        print("⏭️  [AIOMetadata] Skipping push: Missing AIOMETADATA_URL, AIOMETADATA_PASSWORD, or AIOMETADATA_UUID in .env")
        return

    raw_cfg = normalize_aiometadata_config(load_injected_json("aiometadata", env))
    body: Dict[str, Any] = {
        "config": raw_cfg,
        "password": password,
        "force": True,
    }
    if addon_password:
        body["addonPassword"] = addon_password

    if create:
        url = f"{base_url}/api/config/save"
        print(f"🚀 [AIOMetadata] Creating new user config on {base_url}...")
        status, resp = http_json("POST", url, body=body)
        if status not in (200, 201):
            raise RuntimeError(f"[AIOMetadata] Create failed (HTTP {status}): {json.dumps(resp)}")
        new_uuid = resp.get("userUUID")
        install_url = resp.get("installUrl") or f"{base_url}/stremio/{new_uuid}/manifest.json"
        print(f"✅ [AIOMetadata] Created! New UUID: {new_uuid}")
        print(f"🔗 [AIOMetadata] Install URL: {install_url}")
    else:
        url = f"{base_url}/api/config/update/{uuid}"
        print(f"⬆️  [AIOMetadata] Pushing in-place update for UUID {uuid} to {base_url}...")
        status, resp = http_json("PUT", url, body=body)
        if status != 200:
            raise RuntimeError(f"[AIOMetadata] Push failed (HTTP {status}): {json.dumps(resp)}")
        print(f"✅ [AIOMetadata] Successfully updated UUID {uuid} in-place (Install URL unchanged)")


def push_aiomanager(env: Dict[str, str]) -> None:
    cfg_file = CONFIGS_DIR / "aiomanager.json"
    if not cfg_file.exists():
        print(f"⏭️  [AIOManager] Skipping push: {cfg_file.relative_to(ROOT_DIR)} does not exist")
        return

    base_url = env.get("AIOMANAGER_URL", "").rstrip("/")
    uuid = env.get("AIOMANAGER_UUID", "")
    password = env.get("AIOMANAGER_SYNC_PASSWORD", "")
    if not (base_url and uuid and password):
        print("⏭️  [AIOManager] Skipping push: Missing AIOMANAGER_URL, AIOMANAGER_UUID, or AIOMANAGER_SYNC_PASSWORD in .env")
        return

    injected_state = load_injected_json("aiomanager", env)
    envelope = aiomanager_encrypt_envelope(injected_state, password)
    derived_token = derive_aiomanager_sync_token(password)

    url = f"{base_url}/api/sync/{uuid}"
    print(f"⬆️  [AIOManager] Encrypting & pushing sync state for UUID {uuid} to {base_url}...")
    status, resp = http_json("POST", url, headers={"x-sync-password": derived_token}, body=envelope)
    if status not in (200, 201):
        raise RuntimeError(f"[AIOManager] Push failed (HTTP {status}): {json.dumps(resp)}")
    print(f"✅ [AIOManager] Successfully pushed encrypted sync state for UUID {uuid}")


# ==============================================================================
# SANITIZE LOCAL FILES (For manually exported UI files placed in configs/)
# ==============================================================================


def sanitize_local_files(targets: List[str], env: Dict[str, str]) -> None:
    normalizers = {
        "aiostreams": normalize_aiostreams_config,
        "aiometadata": normalize_aiometadata_config,
        "aiomanager": normalize_aiomanager_config,
    }
    found_any = False
    for service in targets:
        path = CONFIGS_DIR / f"{service}.json"
        if not path.exists():
            continue
        found_any = True
        data = json.loads(path.read_text(encoding="utf-8"))
        if service == "aiomanager" and data.get("isEncrypted") and data.get("data"):
            pw = env.get("AIOMANAGER_SYNC_PASSWORD", "")
            if pw:
                data = aiomanager_decrypt_envelope(data, pw)
        clean_cfg = normalizers[service](data)
        save_clean_json(service, clean_cfg, env)
        print(f"🧹 [{service}] Auto-harvested secrets to .env & scrubbed {path.relative_to(ROOT_DIR)}")

    if not found_any:
        print("ℹ️  No config files found in configs/ to sanitize.")


def main() -> int:
    parser = argparse.ArgumentParser(description="AIO Streaming Setup GitOps CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pull_p = subparsers.add_parser("pull", help="Pull live configs from servers into configs/")
    pull_p.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=("all", *SERVICES),
        help="Which service to pull (default: all)",
    )

    push_p = subparsers.add_parser("push", help="Inject secrets & push local configs/ to servers")
    push_p.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=("all", *SERVICES),
        help="Which service to push (default: all)",
    )
    push_p.add_argument(
        "--create",
        action="store_true",
        help="Create a brand-new UUID on the target server (use when migrating to self-hosted)",
    )

    san_p = subparsers.add_parser(
        "sanitize",
        help="Unwrap and scrub secrets from manually exported JSON files in configs/",
    )
    san_p.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=("all", *SERVICES),
        help="Which service file to sanitize (default: all)",
    )

    args = parser.parse_args()
    env = load_env(ENV_FILE)
    targets = list(SERVICES) if args.target == "all" else [args.target]

    try:
        if args.command == "pull":
            for t in targets:
                if t == "aiostreams":
                    pull_aiostreams(env)
                elif t == "aiometadata":
                    pull_aiometadata(env)
                elif t == "aiomanager":
                    pull_aiomanager(env)
        elif args.command == "push":
            for t in targets:
                if t == "aiostreams":
                    push_aiostreams(env, create=args.create)
                elif t == "aiometadata":
                    push_aiometadata(env, create=args.create)
                elif t == "aiomanager":
                    push_aiomanager(env)
        elif args.command == "sanitize":
            sanitize_local_files(targets, env)
    except Exception as exc:
        print(f"❌ Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
