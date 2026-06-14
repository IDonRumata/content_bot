"""
Health monitor — runs every 10 minutes and checks:

  1. Database connectivity
  2. Posts stuck in FAILED state
  3. Threads token expiry (warns 7 days before)
  4. Sentry integration (optional — catches uncaught exceptions globally)

On any critical issue → sends alert to admin via Telegram.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import get_settings
from database import db_manager
from database.models import PostStatus
from utils.helpers import esc
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()


class HealthMonitor:
    # ── Main check ─────────────────────────────────────────────────────────────

    async def run_checks(self) -> None:
        issues: list[str] = []

        issues += await self._check_database()
        issues += await self._check_failed_posts()
        issues += await self._check_threads_token()

        if issues:
            from publishers.telegram_publisher import TelegramPublisher
            tg = TelegramPublisher()
            msg = "🚨 <b>Health Monitor Alert</b>\n\n" + "\n".join(f"• {i}" for i in issues)
            await tg.notify_admin(msg)
            await db_manager.log_event(
                "health_alert",
                f"{len(issues)} issue(s) detected",
                severity="error",
                details={"issues": issues},
            )
        else:
            logger.debug("health_check_ok")

    # ── Individual checks ──────────────────────────────────────────────────────

    async def _check_database(self) -> list[str]:
        try:
            await db_manager.get_active_bloggers()
            return []
        except Exception as e:
            logger.error("db_health_check_failed", error=str(e))
            return [f"❌ База данных недоступна: {esc(e)}"]

    async def _check_failed_posts(self) -> list[str]:
        from sqlalchemy import select, func
        from database.db_manager import AsyncSessionLocal
        from database.models import Post

        async with AsyncSessionLocal() as session:
            count = await session.scalar(
                select(func.count(Post.id)).where(Post.status == PostStatus.FAILED)
            )

        if count and count > 0:
            return [f"⚠️ {count} постов в статусе FAILED — проверь /stats"]
        return []

    async def _check_threads_token(self) -> list[str]:
        from publishers.threads_publisher import ThreadsPublisher
        try:
            th = ThreadsPublisher()
            if await th.refresh_token_reminder():
                return ["⚠️ Threads access token истёк или истекает — обнови токен"]
        except Exception:
            pass
        return []


# ── Global exception handler ───────────────────────────────────────────────────

async def handle_uncaught_exception(exc: Exception, context_str: str = "") -> None:
    """
    Called from the Application error handler and any top-level try/except.
    Logs to Sentry (if configured) and notifies admin.
    """
    logger.error("uncaught_exception", error=str(exc), context=context_str)
    tb = traceback.format_exc()

    if _settings.sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass

    try:
        from publishers.telegram_publisher import TelegramPublisher
        tg = TelegramPublisher()
        await tg.notify_admin(
            f"🔥 <b>Необработанная ошибка</b>\n\n"
            f"<code>{esc(str(exc)[:300])}</code>\n\n"
            f"Контекст: {esc(context_str[:200]) if context_str else '—'}"
        )
    except Exception:
        pass  # if notification also fails, we've already logged it


def setup_sentry() -> None:
    """Initialise Sentry SDK if DSN is configured."""
    if not _settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=_settings.sentry_dsn,
            traces_sample_rate=0.1,
        )
        logger.info("sentry_initialized")
    except ImportError:
        logger.warning("sentry_sdk_not_installed")


def attach_health_monitor(scheduler: AsyncIOScheduler) -> HealthMonitor:
    """Register health check job with the scheduler and return the monitor."""
    monitor = HealthMonitor()
    scheduler.add_job(
        monitor.run_checks,
        trigger=IntervalTrigger(minutes=10),
        id="health_check",
        name="Health monitor",
        max_instances=1,
        replace_existing=True,
    )
    return monitor
