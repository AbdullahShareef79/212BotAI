"""Telegram notification helper."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_API = "https://api.telegram.org"


class TelegramNotifier:
    """Send messages to a Telegram chat via a bot token."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._url = f"{_API}/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._client = httpx.Client(timeout=15)

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send *text* to the configured Telegram chat. Returns True on success."""
        if not self._chat_id:
            return False
        try:
            resp = self._client.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": text[:4096],
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code == 200:
                log.debug("Telegram message sent (%d chars)", len(text))
                return True
            log.warning("Telegram API %d: %s", resp.status_code, resp.text[:200])
            return False
        except Exception as exc:
            log.error("Telegram send error: %s", exc)
            return False

    def close(self) -> None:
        self._client.close()
