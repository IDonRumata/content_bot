"""
Telegram Application factory.

Supports two modes (set BOT_MODE in .env):
  polling    — long-polling, for local dev / VPS without domain
  production — webhook over HTTPS (requires public URL + SSL certificate)

Error handling:
  All unhandled exceptions are routed through handle_uncaught_exception,
  which alerts admin and optionally sends to Sentry.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from bot.handlers import register_handlers
from config import get_settings
from monitoring.health_monitor import handle_uncaught_exception
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()


async def _error_handler(update: object, context) -> None:
    """Global PTB error handler — routes all exceptions to health monitor."""
    err = context.error
    ctx_str = str(update) if update else ""
    await handle_uncaught_exception(err, ctx_str)


def build_application() -> Application:
    """Build and configure the Telegram Application."""
    app = (
        ApplicationBuilder()
        .token(_settings.telegram_bot_token)
        .build()
    )
    register_handlers(app)
    app.add_error_handler(_error_handler)
    return app


async def start_polling(app: Application) -> None:
    """Start the bot in long-polling mode (blocks until shutdown)."""
    logger.info("bot_starting_polling")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # ignore messages sent while bot was offline
    )
    logger.info("bot_polling_active")


async def stop_polling(app: Application) -> None:
    logger.info("bot_stopping")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


async def start_webhook(app: Application) -> None:
    """Start the bot in webhook mode."""
    logger.info("bot_starting_webhook", url=_settings.webhook_url)
    await app.initialize()
    await app.start()
    await app.updater.start_webhook(
        listen="0.0.0.0",
        port=8443,
        url_path=f"/webhook/{_settings.telegram_bot_token}",
        webhook_url=f"{_settings.webhook_url}/webhook/{_settings.telegram_bot_token}",
        secret_token=_settings.telegram_webhook_secret or None,
        drop_pending_updates=True,
    )
    logger.info("bot_webhook_active")
