"""Telegram webhook handler logic. PRD §8.2:

- Wrong/missing secret token -> 200 + silent drop. NEVER 401 — that confirms
  the endpoint exists to whoever's probing it.
- Non-owner chat_id -> one polite refusal, then ignored thereafter (the
  refusal itself is sent by the worker, not here — this just tags the event).
"""
from __future__ import annotations

import json
import logging

from app.security.auth import is_owner_chat, verify_telegram_secret
from app.storage.queue import QStashQueue
from app.web.types import WebResponse

logger = logging.getLogger(__name__)


def _extract_chat_id(update: dict) -> int | None:
    message = update.get("message") or update.get("edited_message")
    if message:
        return message.get("chat", {}).get("id")
    callback = update.get("callback_query")
    if callback:
        return callback.get("message", {}).get("chat", {}).get("id")
    return None


def handle_post(
    headers: dict,
    body: bytes,
    *,
    webhook_secret: str,
    owner_chat_id: int,
    queue: QStashQueue,
    worker_url: str,
) -> WebResponse:
    secret_header = headers.get("X-Telegram-Bot-Api-Secret-Token") or headers.get("x-telegram-bot-api-secret-token")
    if not verify_telegram_secret(secret_header, webhook_secret):
        return WebResponse(status=200, body=b"")  # never 401 — don't confirm the endpoint exists

    try:
        update = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return WebResponse(status=200, body=b"")

    chat_id = _extract_chat_id(update)
    if chat_id is None:
        return WebResponse(status=200, body=b"")

    if not is_owner_chat(chat_id, owner_chat_id):
        queue.publish(worker_url, {"kind": "telegram_non_owner", "chat_id": chat_id})
        return WebResponse(status=200, body=b"")

    try:
        queue.publish(worker_url, {"kind": "telegram_update", "update": update})
    except Exception:
        logger.exception("Failed to enqueue Telegram update")
    return WebResponse(status=200, body=b"")
