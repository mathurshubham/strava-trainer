import base64
import hashlib
import time

import jwt
import pytest
from cryptography.fernet import Fernet

from app.security.auth import (
    constant_time_eq,
    event_hash,
    is_owner_chat,
    verify_qstash_signature,
    verify_strava_verify_token,
    verify_strava_webhook_identity,
    verify_telegram_secret,
)
from app.security.crypto import TokenCipher
from app.security.redact import redact


def test_constant_time_eq():
    assert constant_time_eq("abc", "abc")
    assert not constant_time_eq("abc", "abd")


def test_telegram_secret_rejects_missing_header():
    assert not verify_telegram_secret(None, "expected")
    assert verify_telegram_secret("expected", "expected")
    assert not verify_telegram_secret("wrong", "expected")


def test_owner_chat_check():
    assert is_owner_chat(123, 123)
    assert not is_owner_chat(456, 123)


def test_strava_verify_token():
    assert verify_strava_verify_token("secret", "secret")
    assert not verify_strava_verify_token(None, "secret")
    assert not verify_strava_verify_token("wrong", "secret")


def test_strava_webhook_identity():
    assert verify_strava_webhook_identity(1, 999, expected_subscription_id=1, expected_athlete_id=999)
    assert not verify_strava_webhook_identity(1, 111, expected_subscription_id=1, expected_athlete_id=999)


def test_event_hash_is_deterministic_and_sensitive_to_inputs():
    h1 = event_hash("strava", "123", "create", 1000)
    h2 = event_hash("strava", "123", "create", 1000)
    h3 = event_hash("strava", "123", "update", 1000)
    assert h1 == h2
    assert h1 != h3


def test_token_cipher_roundtrip():
    cipher = TokenCipher(Fernet.generate_key().decode())
    ciphertext = cipher.encrypt("super-secret-refresh-token")
    assert ciphertext != "super-secret-refresh-token"
    assert cipher.decrypt(ciphertext) == "super-secret-refresh-token"


def test_token_cipher_rejects_wrong_key():
    cipher_a = TokenCipher(Fernet.generate_key().decode())
    cipher_b = TokenCipher(Fernet.generate_key().decode())
    ciphertext = cipher_a.encrypt("secret")
    with pytest.raises(ValueError):
        cipher_b.decrypt(ciphertext)


def test_redact_hides_known_secret_shapes():
    text = "token=123456789:AAabcdefghijklmnopqrstuvwxyz012345 key=AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ01234"
    redacted = redact(text)
    assert "AAabcdefghijklmnopqrstuvwxyz012345" not in redacted
    assert "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ01234" not in redacted
    assert "[REDACTED]" in redacted


def _make_qstash_jwt(key: str, *, iss="Upstash", sub="https://app.example.com/api/worker", body=b"payload", exp_delta=60, nbf_delta=-5):
    now = time.time()
    payload = {
        "iss": iss,
        "sub": sub,
        "exp": now + exp_delta,
        "nbf": now + nbf_delta,
        # QStash sends base64 of the digest, not hex — matches a real captured request.
        "body": base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode(),
    }
    return jwt.encode(payload, key, algorithm="HS256")


def test_qstash_signature_valid_with_current_key():
    current, nxt = "current-signing-key", "next-signing-key"
    body = b'{"activity_id": 123}'
    token = _make_qstash_jwt(current, body=body)
    assert verify_qstash_signature(
        token, body, endpoint_url="https://app.example.com/api/worker",
        current_signing_key=current, next_signing_key=nxt,
    )


def test_qstash_signature_valid_with_next_key_during_rotation():
    current, nxt = "current-signing-key", "next-signing-key"
    body = b"{}"
    token = _make_qstash_jwt(nxt, body=body)
    assert verify_qstash_signature(
        token, body, endpoint_url="https://app.example.com/api/worker",
        current_signing_key=current, next_signing_key=nxt,
    )


def test_qstash_signature_rejects_wrong_body():
    current, nxt = "current-signing-key", "next-signing-key"
    token = _make_qstash_jwt(current, body=b"original")
    assert not verify_qstash_signature(
        token, b"tampered", endpoint_url="https://app.example.com/api/worker",
        current_signing_key=current, next_signing_key=nxt,
    )


def test_qstash_signature_rejects_wrong_endpoint():
    current, nxt = "current-signing-key", "next-signing-key"
    body = b"{}"
    token = _make_qstash_jwt(current, body=body, sub="https://evil.example.com/api/worker")
    assert not verify_qstash_signature(
        token, body, endpoint_url="https://app.example.com/api/worker",
        current_signing_key=current, next_signing_key=nxt,
    )


def test_qstash_signature_rejects_expired_token():
    current, nxt = "current-signing-key", "next-signing-key"
    body = b"{}"
    token = _make_qstash_jwt(current, body=body, exp_delta=-10)
    assert not verify_qstash_signature(
        token, body, endpoint_url="https://app.example.com/api/worker",
        current_signing_key=current, next_signing_key=nxt,
    )


def _qstash_jwt_with_body_claim(key: str, body_claim: str) -> str:
    now = time.time()
    return jwt.encode(
        {
            "iss": "Upstash",
            "sub": "https://app.example.com/api/worker",
            "exp": now + 60,
            "nbf": now - 5,
            "body": body_claim,
        },
        key,
        algorithm="HS256",
    )


@pytest.mark.parametrize(
    "encode",
    [
        # Every base64 spelling QStash or its SDKs might emit for the digest.
        lambda d: base64.urlsafe_b64encode(d).decode(),              # url-safe, padded (observed live)
        lambda d: base64.urlsafe_b64encode(d).decode().rstrip("="),  # url-safe, unpadded
        lambda d: base64.b64encode(d).decode(),                      # standard alphabet, padded
        lambda d: base64.b64encode(d).decode().rstrip("="),          # standard alphabet, unpadded
    ],
)
def test_qstash_signature_accepts_every_base64_spelling(encode):
    current, nxt = "current-signing-key", "next-signing-key"
    # Digest chosen so it contains a byte pair encoding to '+'/'/' (i.e. '-'/'_'),
    # which is the only way the two alphabets actually differ.
    body = b'{"kind":"telegram_update","update_id":626015512}'
    token = _qstash_jwt_with_body_claim(current, encode(hashlib.sha256(body).digest()))
    assert verify_qstash_signature(
        token, body, endpoint_url="https://app.example.com/api/worker",
        current_signing_key=current, next_signing_key=nxt,
    )


def test_qstash_signature_rejects_hex_body_claim():
    """Regression: we originally compared the `body` claim to a hex digest, which
    silently 401'd every real QStash delivery. Hex must not be accepted."""
    current, nxt = "current-signing-key", "next-signing-key"
    body = b'{"kind":"strava_activity"}'
    token = _qstash_jwt_with_body_claim(current, hashlib.sha256(body).hexdigest())
    assert not verify_qstash_signature(
        token, body, endpoint_url="https://app.example.com/api/worker",
        current_signing_key=current, next_signing_key=nxt,
    )
