"""ntfy and Telegram senders. URLs contain secrets: never log them."""

import json
import urllib.parse

from .httppost import post


class Ntfy:
    def __init__(self, base_url: str, sleep):
        self.base = base_url.rstrip("/")
        self.sleep = sleep

    def send(self, topic: str, text: str) -> None:
        url = f"{self.base}/{urllib.parse.quote(topic, safe='')}"
        post(url, text.encode("utf-8"), {"Title": "Copytrade", "Content-Type": "text/plain; charset=utf-8"},
             sleep=self.sleep)


class Telegram:
    def __init__(self, token: str, api: str, sleep):
        self._base = f"{api.rstrip('/')}/bot{token}"
        self.sleep = sleep

    def __repr__(self) -> str:
        return "Telegram(<token hidden>)"

    def _call(self, method: str, params: dict, timeout: float = 10.0, retries: int = 3):
        _, body = post(f"{self._base}/{method}", json.dumps(params).encode(),
                       {"Content-Type": "application/json"}, timeout=timeout, retries=retries, sleep=self.sleep)
        return body

    def send(self, chat_id, text: str) -> None:
        # No parse_mode: text is plain, so token names can't inject markup.
        self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    def get_updates(self, offset: int | None):
        params = {"timeout": 0, "allowed_updates": ["message", "channel_post"]}
        if offset is not None:
            params["offset"] = offset
        body = self._call("getUpdates", params, retries=0)
        if not isinstance(body, dict) or not isinstance(body.get("result"), list):
            return []
        return body["result"]
