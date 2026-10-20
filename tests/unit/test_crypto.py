"""Unit and Hypothesis property-based tests for `stream_weaver.core.crypto`."""

import base64
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from stream_weaver.core.crypto import (
    decrypt_envelope,
    decrypt_field,
    derive_sync_token,
    encrypt_envelope,
    encrypt_field,
)
from stream_weaver.core.result import Err, Ok


def test_derive_sync_token_deterministic() -> None:
    tok_no_salt = derive_sync_token("hunter2")
    tok_with_salt = derive_sync_token("hunter2", "abc123salt")
    assert len(tok_no_salt) == 64
    assert len(tok_with_salt) == 64
    assert tok_no_salt != tok_with_salt
    assert derive_sync_token("hunter2") == tok_no_salt


def test_encrypt_decrypt_envelope_roundtrip_small_and_compressed() -> None:
    salt_b64 = base64.b64encode(b"0123456789abcdef").decode("ascii")
    state: dict[str, Any] = {
        "version": "1.0.0",
        "accounts": [{"id": "acc-1", "apiKey": "key-123"}],
        "_syncSalt": salt_b64,
    }
    enc_res = encrypt_envelope(
        state,
        "my-secret-pw",
        iv=b"123456789012",
        synced_at="2026-10-17T00:00:00Z",
    )
    assert isinstance(enc_res, Ok)
    envelope = enc_res.value
    assert envelope["isEncrypted"] is True
    assert envelope["compressed"] is False
    assert envelope["apiKeys"] == {"acc-1": "key-123"}

    dec_res = decrypt_envelope(envelope, "my-secret-pw")
    assert isinstance(dec_res, Ok)
    decrypted = dec_res.value
    assert decrypted["version"] == "1.0.0"
    assert decrypted["accounts"] == [{"id": "acc-1", "apiKey": "key-123"}]
    assert decrypted["_syncSalt"] == salt_b64

    # Wrong password must fail authentication
    bad_res = decrypt_envelope(envelope, "wrong-password")
    assert isinstance(bad_res, Err)


def test_encrypt_decrypt_compressed_large_envelope() -> None:
    large_state: dict[str, Any] = {
        "version": "2.0.0",
        "padding": "x" * 60_000,
    }
    enc_res = encrypt_envelope(large_state, "pw-large")
    assert isinstance(enc_res, Ok)
    assert enc_res.value["compressed"] is True

    dec_res = decrypt_envelope(enc_res.value, "pw-large")
    assert isinstance(dec_res, Ok)
    assert dec_res.value["padding"] == "x" * 60_000


@settings(max_examples=8, deadline=None)
@given(
    plaintext=st.text(min_size=1, max_size=200),
    password=st.text(min_size=4, max_size=32),
    iv=st.binary(min_size=12, max_size=12),
)
def test_property_field_encryption_roundtrip(
    plaintext: str,
    password: str,
    iv: bytes,
) -> None:
    if plaintext.startswith("eyJ"):
        return
    salt_b64 = base64.b64encode(b"fixed-16-byte-sl").decode("ascii")
    enc_res = encrypt_field(plaintext, password, salt_b64, iv=iv)
    assert isinstance(enc_res, Ok)
    decrypted = decrypt_field(enc_res.value, password, salt_b64)
    assert decrypted == plaintext
