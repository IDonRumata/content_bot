"""
In-memory rate limiter for Telegram bot handlers.

Why in-memory (not Redis):
  Single-process bot doesn't need distributed rate limiting.
  Memory is wiped on restart — acceptable for abuse prevention.

Strategy:
  Token bucket per user_id, refilled every minute.
  Default: 20 requests/minute (config: RATE_LIMIT_PER_MINUTE).

Attack vectors mitigated:
  • Flood attacks from compromised/forwarded bot links.
  • Automated spam if bot token is leaked (buys time to rotate).
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Token-bucket rate limiter keyed by Telegram user_id."""

    def __init__(self, max_per_minute: int | None = None) -> None:
        self._max = max_per_minute or _settings.rate_limit_per_minute
        self._buckets: dict[int, _Bucket] = defaultdict(
            lambda: _Bucket(tokens=float(self._max))
        )

    def is_allowed(self, user_id: int) -> bool:
        bucket = self._buckets[user_id]
        now = time.monotonic()
        elapsed = now - bucket.last_refill

        # Refill tokens proportionally
        refill = elapsed * (self._max / 60.0)
        bucket.tokens = min(float(self._max), bucket.tokens + refill)
        bucket.last_refill = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False


# Module-level singleton
_limiter = RateLimiter()


def rate_limited(handler: Callable) -> Callable:
    """
    Decorator: rejects requests exceeding the configured rate limit.
    Works in conjunction with @admin_only (order: admin_only first).
    """
    import functools

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user and not _limiter.is_allowed(user.id):
            logger.warning("rate_limit_exceeded", user_id=user.id)
            if update.message:
                await update.message.reply_text("⏳ Слишком много запросов. Подожди немного.")
            return
        return await handler(update, context, *args, **kwargs)
    return wrapper
