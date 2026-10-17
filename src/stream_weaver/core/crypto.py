"""Pure cryptographic primitives for AIOManager Cloud Sync (PBKDF2-HMAC-SHA256 + AES-256-GCM + deflate)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from stream_weaver.core.errors import CryptoError
from stream_weaver.core.result import Err, Ok, Result
from stream_weaver.core.types import JsonDict

DEFAULT_SYNC_SALT: bytes = b"aiomanager-sync-key-v2"
PBKDF2_ITERATIONS: int = 600_000
AES_KEY_BYTES: int = 32
GCM_IV_BYTES: int = 12
GCM_TAG_BYTES: int = 16
COMPRESSION_THRESHOLD_BYTES: int = 51_200


def derive_sync_token(password: str, salt: str | None = None) -> str:
    """
    Pure function matching AIOManager's client-side `deriveSyncToken(password, salt)`:
      SHA-256(password + (salt ? `:sync-auth-token:${salt}` : ':sync-auth-token'))
    """
    pepper = f":sync-auth-token:{salt}" if salt else ":sync-auth-token"
    payload = f"{password}{pepper}".encode()
    return hashlib.sha256(payload).hexdigest()


def derive_aes_key(password: str, salt_b64: str | None = None) -> Result[bytes, CryptoError]:
    """Derives a 256-bit AES key using PBKDF2-HMAC-SHA256 (600,000 iterations)."""
    try:
        salt = base64.b64decode(salt_b64) if salt_b64 else DEFAULT_SYNC_SALT
    except Exception as exc:
        return Err(CryptoError(f"Invalid base64 sync salt: {exc}"))

    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=AES_KEY_BYTES,
    )
    return Ok(key)


def _deflate_raw(data: bytes) -> bytes:
    compressor = zlib.compressobj(level=zlib.Z_DEFAULT_COMPRESSION, wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def _inflate_auto(data: bytes) -> bytes:
    try:
        return zlib.decompress(data, wbits=-zlib.MAX_WBITS)
    except zlib.error:
        return zlib.decompress(data)


def decrypt_envelope(
    envelope: Mapping[str, Any],
    password: str,
) -> Result[JsonDict, CryptoError]:
    """
    Purely decrypts an AIOManager Cloud Sync envelope (`isEncrypted: true`) into a JSON dictionary.
    If the envelope is unencrypted, returns a shallow copy of the envelope.
    """
    if not (envelope.get("isEncrypted") and envelope.get("data")):
        return Ok(dict(envelope))

    data_b64 = envelope.get("data")
    if not isinstance(data_b64, str):
        return Err(CryptoError("Encrypted AIOManager envelope is missing string 'data' field."))

    salt_b64 = envelope.get("syncSalt") if isinstance(envelope.get("syncSalt"), str) else None
    compressed = bool(envelope.get("compressed"))

    match derive_aes_key(password, salt_b64):
        case Err() as err:
            return err
        case Ok(key):
            pass

    try:
        combined = base64.b64decode(data_b64)
    except Exception as exc:
        return Err(CryptoError(f"Invalid base64 ciphertext in envelope: {exc}"))

    if len(combined) < GCM_IV_BYTES + GCM_TAG_BYTES:
        return Err(CryptoError("Encrypted payload is shorter than minimum AES-GCM frame size."))

    iv = combined[:GCM_IV_BYTES]
    ciphertext_and_tag = combined[GCM_IV_BYTES:]

    try:
        decrypted_bytes = AESGCM(key).decrypt(iv, ciphertext_and_tag, None)
        decrypted_str = decrypted_bytes.decode("utf-8")
        if compressed:
            compressed_buf = base64.b64decode(decrypted_str)
            json_str = _inflate_auto(compressed_buf).decode("utf-8")
        else:
            json_str = decrypted_str
        parsed = json.loads(json_str)
    except InvalidTag:
        return Err(CryptoError("AES-GCM authentication failed (incorrect password or corrupted data)."))
    except Exception as exc:
        return Err(CryptoError(f"Failed to decode decrypted AIOManager payload: {exc}"))

    if not isinstance(parsed, dict):
        return Err(CryptoError("Decrypted AIOManager payload is not a JSON object."))

    result: JsonDict = dict(parsed)
    if salt_b64:
        result["_syncSalt"] = salt_b64
    return Ok(result)


def encrypt_envelope(
    state_obj: Mapping[str, Any],
    password: str,
    *,
    iv: bytes | None = None,
    synced_at: str | None = None,
) -> Result[JsonDict, CryptoError]:
    """
    Purely encrypts an AIOManager state dictionary into the AES-256-GCM + PBKDF2 envelope
    expected by `POST /api/sync/:uuid`.

    Passing explicit `iv` (12 bytes) and `synced_at` makes this function 100% deterministic
    for property-based testing.
    """
    clean_state: JsonDict = dict(state_obj)
    sync_salt_raw = clean_state.pop("_syncSalt", None)
    sync_salt_b64 = sync_salt_raw if isinstance(sync_salt_raw, str) else None
    clean_state.pop("force", None)
    clean_state.pop("contentHint", None)

    timestamp = synced_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    stamped_obj: JsonDict = {**clean_state, "syncedAt": timestamp}
    stamped_str = json.dumps(stamped_obj, separators=(",", ":"), ensure_ascii=False)
    stamped_bytes = stamped_str.encode("utf-8")

    if len(stamped_bytes) > COMPRESSION_THRESHOLD_BYTES:
        payload_bytes = base64.b64encode(_deflate_raw(stamped_bytes))
        is_compressed = True
    else:
        payload_bytes = stamped_bytes
        is_compressed = False

    match derive_aes_key(password, sync_salt_b64):
        case Err() as err:
            return err
        case Ok(key):
            pass

    nonce = iv if iv is not None else os.urandom(GCM_IV_BYTES)
    if len(nonce) != GCM_IV_BYTES:
        return Err(CryptoError(f"AES-GCM IV must be {GCM_IV_BYTES} bytes, got {len(nonce)}."))

    ciphertext_and_tag = AESGCM(key).encrypt(nonce, payload_bytes, None)
    combined_b64 = base64.b64encode(nonce + ciphertext_and_tag).decode("ascii")

    api_keys: dict[str, str] = {}
    accounts = clean_state.get("accounts")
    if isinstance(accounts, list):
        for acc in accounts:
            if isinstance(acc, dict):
                acc_id = acc.get("id")
                acc_key = acc.get("apiKey")
                if isinstance(acc_id, str) and acc_id and isinstance(acc_key, str) and acc_key:
                    api_keys[acc_id] = acc_key

    envelope: JsonDict = {
        "data": combined_b64,
        "isEncrypted": True,
        "compressed": is_compressed,
        **({"syncSalt": sync_salt_b64} if sync_salt_b64 else {}),
        "syncedAt": timestamp,
        "force": True,
        "apiKeys": api_keys,
    }
    return Ok(envelope)


def decrypt_field(enc_val: str, password: str, salt_b64: str) -> str:
    """
    Decrypts an individual AES-256-GCM encrypted credential field inside AIOManager state.
    If the value is already plaintext (e.g. a JWT starting with `eyJ` or unencrypted token),
    returns it unchanged.
    """
    if not enc_val or enc_val.startswith("eyJ") or not salt_b64:
        return enc_val

    match derive_aes_key(password, salt_b64):
        case Err():
            return enc_val
        case Ok(key):
            pass

    try:
        buf = base64.b64decode(enc_val)
        if len(buf) < GCM_IV_BYTES + GCM_TAG_BYTES:
            return enc_val
        iv = buf[:GCM_IV_BYTES]
        ciphertext_and_tag = buf[GCM_IV_BYTES:]
        plaintext = AESGCM(key).decrypt(iv, ciphertext_and_tag, None)
        return plaintext.decode("utf-8")
    except Exception:
        return enc_val


def encrypt_field(
    plaintext: str,
    password: str,
    salt_b64: str,
    *,
    iv: bytes | None = None,
) -> Result[str, CryptoError]:
    """Encrypts a single credential string using AIOManager's per-field AES-256-GCM format."""
    match derive_aes_key(password, salt_b64):
        case Err() as err:
            return err
        case Ok(key):
            pass

    nonce = iv if iv is not None else os.urandom(GCM_IV_BYTES)
    if len(nonce) != GCM_IV_BYTES:
        return Err(CryptoError(f"AES-GCM IV must be {GCM_IV_BYTES} bytes."))
    ct_and_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return Ok(base64.b64encode(nonce + ct_and_tag).decode("ascii"))
