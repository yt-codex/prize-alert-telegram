"""Telegram notification helpers."""

from __future__ import annotations

import requests


TELEGRAM_API_BASE = "https://api.telegram.org"


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    """Send a plain text Telegram message via Bot API."""
    response = requests.post(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    response.raise_for_status()
