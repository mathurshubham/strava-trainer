"""Telegram Bot API client. Webhook mode only (PRD §5.3 — polling needs an
always-on process, which contradicts the serverless architecture).

Never sends raw markdown or unsanitized text — callers pass already-rendered,
whitelisted HTML from app/render/telegram.py. The one piece of defense this
client owns itself is the documented 400 fallback (strip tags, retry once).
"""
from __future__ import annotations

from io import BytesIO

import httpx

from app.render.telegram import CAPTION_LIMIT, chunk_caption, render_message, strip_all_tags


class TelegramClient:
    def __init__(self, bot_token: str, http_client: httpx.Client | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._http = http_client or httpx.Client(timeout=15.0)

    def _post(self, method: str, **kwargs) -> dict:
        response = self._http.post(f"{self._base}/{method}", **kwargs)
        return response.json()

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self._post("sendChatAction", json={"chat_id": chat_id, "action": action})

    def send_message(self, chat_id: int, html_text: str, reply_markup: dict | None = None) -> dict:
        payload: dict = {"chat_id": chat_id, "text": html_text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup

        data = self._post("sendMessage", json=payload)
        if not data.get("ok") and data.get("error_code") == 400:
            fallback_payload: dict = {"chat_id": chat_id, "text": strip_all_tags(html_text)}
            if reply_markup:
                fallback_payload["reply_markup"] = reply_markup
            data = self._post("sendMessage", json=fallback_payload)
        return data

    def send_rendered(self, chat_id: int, chunks: list[str], reply_markup: dict | None = None) -> list[dict]:
        """Send a pre-chunked HTML message as N Telegram messages. Only the
        final chunk gets the reply_markup (inline keyboard)."""
        results = []
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            results.append(self.send_message(chat_id, chunk, reply_markup=reply_markup if is_last else None))
        return results

    def send_markdown(self, chat_id: int, markdown: str, reply_markup: dict | None = None) -> list[dict]:
        return self.send_rendered(chat_id, render_message(markdown), reply_markup=reply_markup)

    def send_photo(self, chat_id: int, photo: BytesIO, caption_html: str | None = None) -> dict:
        data: dict = {"chat_id": str(chat_id)}
        if caption_html:
            capped = chunk_caption(caption_html, limit=CAPTION_LIMIT)[0] if len(caption_html) > CAPTION_LIMIT else caption_html
            data["caption"] = capped
            data["parse_mode"] = "HTML"
        photo.seek(0)
        files = {"photo": ("chart.png", photo, "image/png")}
        return self._post("sendPhoto", data=data, files=files)

    def set_webhook(self, url: str, secret_token: str, allowed_updates: list[str], drop_pending_updates: bool = True) -> dict:
        return self._post(
            "setWebhook",
            json={
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": allowed_updates,
                "drop_pending_updates": drop_pending_updates,
            },
        )

    def delete_webhook(self) -> dict:
        return self._post("deleteWebhook", json={})

    def get_webhook_info(self) -> dict:
        return self._post("getWebhookInfo", json={})

    def get_me(self) -> dict:
        """Read-only bot identity check — confirms the token is valid without
        sending anything to any chat. Useful for smoke-testing credentials."""
        return self._post("getMe", json={})
