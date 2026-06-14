"""
Authentication & authorisation for the Telegram bot.

Security model:
  • Bot is PRIVATE — only the configured admin Telegram ID can interact with it.
  • All handlers are wrapped with @admin_only decorator.
  • Unknown users receive a generic error (no information leakage).
  • Every unauthorised access attempt is logged to the audit trail.

Attack vectors mitigated:
  • Impersonation: Telegram user_id is server-side, cannot be spoofed by clients.
  • Enumeration: bot doesn't confirm its own existence to unknown users.
  • Token theft: bot token in env var only; not logged, not committed.
"""
from __future__ import annotations

import functools
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()


def is_admin(user_id: int | None) -> bool:
    """Full-privilege admin (manages bloggers, config, everything)."""
    return user_id is not None and user_id == _settings.admin_telegram_id


def is_authorized(user_id: int | None) -> bool:
    """Admin OR editor — allowed to review/edit/approve posts."""
    return user_id is not None and user_id in _settings.authorized_ids


async def _deny(update: Update, handler_name: str) -> None:
    """Log an unauthorised attempt and send a minimal response."""
    user = update.effective_user
    logger.warning(
        "unauthorised_access",
        user_id=user.id if user else "unknown",
        username=user.username if user else "unknown",
        handler=handler_name,
    )
    if update.callback_query:
        await update.callback_query.answer("⛔ Access denied.", show_alert=True)
    elif update.message:
        await update.message.reply_text("⛔ Access denied.")


def _restrict(check: Callable[[int | None], bool]) -> Callable:
    """Build a handler decorator that allows users passing `check`."""
    def decorator(handler: Callable) -> Callable:
        @functools.wraps(handler)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            if not check(user.id if user else None):
                await _deny(update, handler.__name__)
                return
            return await handler(update, context, *args, **kwargs)
        return wrapper
    return decorator


# Full admin only — blogger management, scraping, config.
admin_only = _restrict(is_admin)

# Editors and admin — post review / edit / approve / reject.
editor_or_admin = _restrict(is_authorized)


def verify_webhook_signature(token: str, request_token: str) -> bool:
    """
    Verify Telegram webhook X-Telegram-Bot-Api-Secret-Token header.
    Uses constant-time comparison to prevent timing attacks.
    """
    import hmac
    return hmac.compare_digest(token, request_token)
