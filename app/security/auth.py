"""Endpoint authentication helpers. PRD §8.2 — constant-time comparisons everywhere.

Every check here returns a plain bool. The caller decides the HTTP response;
this module never raises on a bad credential (a raised exception on auth
failure is how you accidentally leak "this endpoint exists" via a stack trace).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import jwt


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_telegram_secret(header_value: str | None, expected: str) -> bool:
    if not header_value:
        return False
    return constant_time_eq(header_value, expected)


def is_owner_chat(chat_id: int, owner_chat_id: int) -> bool:
    return chat_id == owner_chat_id


def verify_strava_verify_token(token: str | None, expected: str) -> bool:
    if not token:
        return False
    return constant_time_eq(token, expected)


def verify_strava_webhook_identity(
    subscription_id: int,
    owner_id: int,
    expected_subscription_id: int,
    expected_athlete_id: int,
) -> bool:
    return subscription_id == expected_subscription_id and owner_id == expected_athlete_id


def event_hash(provider: str, object_id: str, aspect_type: str, event_time: int) -> str:
    """PRD §3.6: sha256(provider|object_id|aspect_type|event_time) for idempotency."""
    raw = f"{provider}|{object_id}|{aspect_type}|{event_time}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalise_b64(value: str) -> str:
    """Map a base64 digest onto the URL-safe alphabet with padding stripped, so
    standard vs URL-safe and padded vs unpadded forms all compare equal."""
    return value.replace("+", "-").replace("/", "_").rstrip("=")


def verify_qstash_signature(
    signature: str | None,
    body: bytes,
    *,
    endpoint_url: str,
    current_signing_key: str,
    next_signing_key: str,
) -> bool:
    """Verify the `Upstash-Signature` header per Upstash's documented JWT scheme:

    HS256 JWT with claims `iss="Upstash"`, `sub=<endpoint url>`, `exp`, `nbf`,
    and `body=<base64 sha256 digest of the raw request body>`. Try the current
    signing key first, then the next key (covers key-rotation windows).

    The `body` claim is base64, NOT hex — confirmed against a real delivered
    request (see DECISIONS.md). QStash uses the URL-safe alphabet and pads,
    but its own SDKs compare with padding stripped, so we normalise both the
    standard and URL-safe alphabets and drop padding before comparing.
    """
    if not signature:
        return False

    body_digest = hashlib.sha256(body).digest()
    expected_body_hash = _normalise_b64(base64.urlsafe_b64encode(body_digest).decode())

    for key in (current_signing_key, next_signing_key):
        try:
            payload = jwt.decode(
                signature,
                key,
                algorithms=["HS256"],
                audience=None,
                options={"verify_aud": False},
            )
        except jwt.InvalidTokenError:
            continue

        if payload.get("iss") != "Upstash":
            continue
        if payload.get("sub") != endpoint_url:
            continue
        claimed_body_hash = payload.get("body")
        if not isinstance(claimed_body_hash, str):
            continue
        if not constant_time_eq(_normalise_b64(claimed_body_hash), expected_body_hash):
            continue
        exp = payload.get("exp")
        nbf = payload.get("nbf")
        now = time.time()
        if exp is not None and now > exp:
            continue
        if nbf is not None and now < nbf:
            continue
        return True

    return False
