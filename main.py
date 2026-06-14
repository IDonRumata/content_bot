"""
Content Bot — entry point.

Startup sequence:
  1. Load and validate config (.env)
  2. Init Sentry (if configured)
  3. Init database (create tables, seed default bloggers)
  4. Build Telegram Application
  5. Start APScheduler (scrape + publish + health jobs)
  6. Start bot (polling or webhook)
  7. On SIGINT/SIGTERM — graceful shutdown

Usage:
  python main.py
"""
from __future__ import annotations

import asyncio
import signal
import sys

from config import get_settings
from database.db_manager import init_db
from monitoring.health_monitor import attach_health_monitor, setup_sentry
from scheduler.task_scheduler import create_scheduler
from bot.telegram_bot import build_application, start_polling, start_webhook, stop_polling
from utils.logger import get_logger, setup_logging

setup_logging("INFO")
logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()

    # ── 1. Sentry ──────────────────────────────────────────────────────────────
    setup_sentry()

    # ── 2. Database ────────────────────────────────────────────────────────────
    logger.info("db_init_start")
    await init_db()
    logger.info("db_init_done")

    # ── 3. Telegram Application ────────────────────────────────────────────────
    app = build_application()

    # ── 4. Scheduler ──────────────────────────────────────────────────────────
    scheduler = create_scheduler()
    attach_health_monitor(scheduler)
    scheduler.start()
    logger.info("scheduler_started")

    # ── 5. Graceful shutdown handler ───────────────────────────────────────────
    stop_event = asyncio.Event()

    def _signal_handler(*_) -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for all signals
            signal.signal(sig, _signal_handler)

    # ── 6. Start bot ───────────────────────────────────────────────────────────
    if settings.bot_mode == "production":
        if not settings.webhook_url:
            logger.error("webhook_url_not_set")
            sys.exit(1)
        await start_webhook(app)
    else:
        await start_polling(app)

    logger.info("content_bot_running", mode=settings.bot_mode)

    # ── 7. Wait for shutdown ───────────────────────────────────────────────────
    await stop_event.wait()

    logger.info("shutting_down")
    scheduler.shutdown(wait=False)
    if settings.bot_mode != "production":
        await stop_polling(app)
    logger.info("shutdown_complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
