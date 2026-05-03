import time
from typing import Dict

DEFAULT_COOLDOWN_SECONDS = 60


class CooldownTracker:
    """
    Tracks provider failures and puts them in cooldown.
    A provider in cooldown is skipped until the cooldown expires.
    """

    def __init__(self, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        self.cooldown_seconds = cooldown_seconds
        self._cooldowns: Dict[str, float] = {}  # provider -> expires_at

    def mark_failed(self, provider: str) -> None:
        self._cooldowns[provider] = time.monotonic() + self.cooldown_seconds

    def is_available(self, provider: str) -> bool:
        expires_at = self._cooldowns.get(provider)
        if expires_at is None:
            return True
        if time.monotonic() >= expires_at:
            del self._cooldowns[provider]  # cooldown expired
            return True
        return False

    def cooldown_remaining(self, provider: str) -> float:
        expires_at = self._cooldowns.get(provider)
        if expires_at is None:
            return 0.0
        remaining = expires_at - time.monotonic()
        return max(0.0, remaining)
