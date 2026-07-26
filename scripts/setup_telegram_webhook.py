#!/usr/bin/env python3
"""Register the Telegram webhook. PRD §5.3.

Usage:
    python scripts/setup_telegram_webhook.py set
    python scripts/setup_telegram_webhook.py info
    python scripts/setup_telegram_webhook.py delete
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.clients.telegram import TelegramClient
from app.config import get_settings


def main() -> None:
    settings = get_settings()
    client = TelegramClient(settings.telegram_bot_token)

    if len(sys.argv) < 2 or sys.argv[1] not in ("set", "info", "delete"):
        print(__doc__)
        raise SystemExit(1)

    command = sys.argv[1]
    if command == "set":
        if not settings.app_url:
            print("APP_URL is not set in .env — set it to your deployed Vercel URL first.")
            raise SystemExit(1)
        result = client.set_webhook(
            url=f"{settings.app_url}/api/webhook/telegram",
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        print(result)
    elif command == "info":
        print(client.get_webhook_info())
    elif command == "delete":
        print(client.delete_webhook())


if __name__ == "__main__":
    main()
