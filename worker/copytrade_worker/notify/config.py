import os
from dataclasses import dataclass

DAY_MS = 86_400_000


@dataclass(frozen=True)
class Config:
    ntfy_url: str = "https://ntfy.sh"
    telegram_token: str | None = None
    telegram_api: str = "https://api.telegram.org"
    host_ntfy_topic: str | None = None
    host_telegram_chat_id: str | None = None
    weekly_ms: int = 7 * DAY_MS
    give_up_ms: int = DAY_MS          # stop retrying an undeliverable row after this age
    interval_s: float = 5.0
    batch: int = 100

    def __repr__(self) -> str:  # never print secrets
        return (f"Config(ntfy_url={self.ntfy_url!r}, telegram={'set' if self.telegram_token else 'unset'}, "
                f"host_ntfy={'set' if self.host_ntfy_topic else 'unset'}, "
                f"host_telegram={'set' if self.host_telegram_chat_id else 'unset'})")

    __str__ = __repr__

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(
            ntfy_url=(env.get("NTFY_URL") or "https://ntfy.sh").rstrip("/"),
            telegram_token=env.get("TELEGRAM_BOT_TOKEN") or None,
            host_ntfy_topic=env.get("HOST_NTFY_TOPIC") or None,
            host_telegram_chat_id=env.get("HOST_TELEGRAM_CHAT_ID") or None,
        )
