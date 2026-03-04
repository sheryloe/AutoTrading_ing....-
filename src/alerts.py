from __future__ import annotations

import time
from typing import Any

import requests


class AlertManager:
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: int = 8) -> None:
        self.bot_token = str(bot_token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_telegram(self, text: str) -> tuple[bool, str]:
        if not self.enabled:
            return False, "telegram_disabled"
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": str(text or "")[:3900],
            "disable_web_page_preview": True,
        }
        try:
            res = requests.post(url, json=payload, timeout=self.timeout_seconds)
            res.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, ""

    def make_alert_row(self, level: str, title: str, text: str) -> dict[str, Any]:
        return {
            "ts": int(time.time()),
            "level": str(level or "info"),
            "title": str(title or ""),
            "text": str(text or ""),
        }

