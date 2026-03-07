from __future__ import annotations

from typing import Any

import requests


class TelegramBotClient:
    def __init__(self, bot_token: str, timeout_seconds: int = 8) -> None:
        self.bot_token = str(bot_token or "").strip()
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token)

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    def get_updates(self, offset: int = 0, timeout: int = 0) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        params = {"offset": int(offset), "timeout": int(timeout)}
        req_timeout = max(float(self.timeout_seconds), float(int(timeout) + 8))
        res = self.session.get(f"{self.base_url}/getUpdates", params=params, timeout=req_timeout)
        res.raise_for_status()
        body = res.json()
        if not body.get("ok"):
            raise RuntimeError(str(body))
        data = body.get("result")
        return data if isinstance(data, list) else []

    def send_message(self, chat_id: str, text: str) -> tuple[bool, str]:
        if not self.enabled or not chat_id:
            return False, "telegram_disabled"
        msg = str(text or "")
        payload = {
            "chat_id": str(chat_id),
            "text": msg[:3900],
            "disable_web_page_preview": True,
        }
        try:
            res = self.session.post(
                f"{self.base_url}/sendMessage",
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=self.timeout_seconds,
            )
            res.raise_for_status()
            body = res.json()
            if not body.get("ok"):
                return False, str(body)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, ""

    def delete_webhook(self, drop_pending_updates: bool = False) -> tuple[bool, str]:
        if not self.enabled:
            return False, "telegram_disabled"
        try:
            res = self.session.post(
                f"{self.base_url}/deleteWebhook",
                json={"drop_pending_updates": bool(drop_pending_updates)},
                timeout=self.timeout_seconds,
            )
            res.raise_for_status()
            body = res.json()
            if not body.get("ok"):
                return False, str(body)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, ""
