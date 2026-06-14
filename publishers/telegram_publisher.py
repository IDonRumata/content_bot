"""
Publishes approved posts to the Telegram channel.
Uses python-telegram-bot's Bot directly (not the Application),
so it can be called from the scheduler context.
"""
from __future__ import annotations

import asyncio

import telegram
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import get_settings
from utils.helpers import truncate
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()


class TelegramPublisher:
    def __init__(self) -> None:
        self._bot = telegram.Bot(token=_settings.telegram_bot_token)

    @retry(
        retry=retry_if_exception_type((telegram.error.TimedOut, telegram.error.NetworkError)),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        stop=stop_after_attempt(4),
    )
    async def publish(self, text: str) -> int:
        """
        Send a message to the configured channel.
        Returns the message_id on success.
        """
        msg = await self._bot.send_message(
            chat_id=_settings.telegram_channel_id,
            text=truncate(text, 4096),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("telegram_published", message_id=msg.message_id)
        return msg.message_id

    async def notify_admin(self, text: str) -> None:
        """Send an HTML notification to the admin user."""
        await self._notify(_settings.admin_telegram_id, text)

    async def notify_reviewers(self, text: str) -> None:
        """Notify everyone who reviews posts (admin + editors)."""
        for uid in _settings.authorized_ids:
            await self._notify(uid, text)

    async def _notify(self, chat_id: int, text: str) -> None:
        """
        Send an HTML message, never raising. Dynamic content must be escaped
        with utils.helpers.esc() by the caller — otherwise stray '<' breaks
        Telegram's HTML parser and the alert is silently lost.
        """
        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=truncate(text, 4096),
                parse_mode="HTML",
            )
        except Exception as e:
            # Notification failure must never crash the main flow.
            # Retry once as plain text so the message still gets through.
            logger.error("admin_notify_failed", chat_id=chat_id, error=str(e))
            try:
                await self._bot.send_message(chat_id=chat_id, text=truncate(text, 4096))
            except Exception:
                pass
