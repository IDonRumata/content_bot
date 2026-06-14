"""
APScheduler-based task scheduler.

Jobs:
  scrape_job    — runs every N hours, scrapes YouTube, rewrites content,
                  sends new posts to admin for review
  publish_job   — runs every 30 min, publishes APPROVED posts that are due
  health_job    — runs every 10 min, checks system health & alerts on issues
  token_check   — runs daily, logs token consumption summary
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import get_settings
from database import db_manager
from database.models import Post, PostStatus
from processors.content_processor import ContentProcessor
from publishers.telegram_publisher import TelegramPublisher
from publishers.threads_publisher import ThreadsPublisher
from scrapers.youtube_scraper import YouTubeScraper
from utils.helpers import esc, format_number
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# Singletons reused across scheduler calls (avoids re-auth on every tick)
_scraper: YouTubeScraper | None = None
_processor: ContentProcessor | None = None
_tg_publisher: TelegramPublisher | None = None
_th_publisher: ThreadsPublisher | None = None


def _get_scraper() -> YouTubeScraper:
    global _scraper
    if _scraper is None:
        _scraper = YouTubeScraper()
    return _scraper


def _get_processor() -> ContentProcessor:
    global _processor
    if _processor is None:
        _processor = ContentProcessor()
    return _processor


def _get_tg_publisher() -> TelegramPublisher:
    global _tg_publisher
    if _tg_publisher is None:
        _tg_publisher = TelegramPublisher()
    return _tg_publisher


def _get_th_publisher() -> ThreadsPublisher:
    global _th_publisher
    if _th_publisher is None:
        _th_publisher = ThreadsPublisher()
    return _th_publisher


# ── Scrape job ─────────────────────────────────────────────────────────────────

async def run_scrape_job(category: str | None = None) -> int:
    """
    Scrape active bloggers, rewrite content, save as PENDING_REVIEW,
    notify reviewers about new posts.

    `category` — if given ("finance" / "vibecoding"), only scrape bloggers of
    that domain; None = all. Returns the count of new posts created.
    """
    logger.info("scrape_job_start", category=category or "all")
    scraper = _get_scraper()
    processor = _get_processor()
    tg = _get_tg_publisher()

    bloggers = await db_manager.get_active_bloggers(category=category)
    if not bloggers:
        logger.warning("no_active_bloggers", category=category or "all")
        return 0

    new_posts: list[Post] = []
    loop = asyncio.get_event_loop()

    for blogger in bloggers:
        b_category = getattr(blogger, "category", "finance") or "finance"
        try:
            videos = await scraper.scrape_blogger(
                blogger.channel_id,
                max_results=_settings.max_posts_per_run,
            )
            for video in videos:
                # Topic-level de-dup: skip themes already covered recently,
                # across ALL bloggers, before spending rewrite tokens.
                topic = await loop.run_in_executor(
                    None, processor.classify_topic,
                    video.title, video.description, b_category,
                )
                if topic and await db_manager.topic_exists_recent(
                    topic, _settings.topic_dedup_days
                ):
                    logger.info(
                        "topic_duplicate_skipped",
                        topic=topic, blogger=blogger.name, source_id=video.video_id,
                    )
                    continue

                # Skip "empty" videos (ads / shorts with no transcript & thin
                # description) — rewriting them produces posts "from thin air".
                transcript = await loop.run_in_executor(
                    None, scraper.get_transcript, video.video_id
                )
                source_chars = len(transcript) + len(video.description or "")
                if source_chars < _settings.min_source_chars:
                    logger.info(
                        "thin_content_skipped",
                        blogger=blogger.name, source_id=video.video_id,
                        source_chars=source_chars,
                    )
                    continue

                post = await scraper.video_to_post(
                    video, blogger.id, b_category, transcript=transcript
                )
                post.topic_signature = topic or None

                # Rewrite in a thread pool to avoid blocking the event loop
                post = await loop.run_in_executor(
                    None, lambda p=post: processor.process_post(p, blogger.name)
                )

                saved = await db_manager.save_post(post)
                new_posts.append(saved)
                logger.info(
                    "post_saved",
                    post_id=saved.id, source_id=saved.source_id,
                    category=b_category, topic=topic,
                )

        except Exception as e:
            logger.error("scrape_blogger_failed", blogger=blogger.name, error=str(e))
            await db_manager.log_event(
                "scrape_error",
                f"Ошибка парсинга {blogger.name}: {e}",
                severity="error",
            )
            await tg.notify_admin(
                f"⚠️ <b>Ошибка парсинга</b>\n"
                f"Блоггер: {esc(blogger.name)}\n"
                f"Ошибка: {esc(e)}"
            )

    if new_posts:
        label = {"finance": "💸 Финансы", "vibecoding": "🧑‍💻 AI/Кодинг"}.get(
            category, "Все темы"
        )
        await tg.notify_reviewers(
            f"📬 <b>Новые посты на проверке: {len(new_posts)}</b>\n"
            f"Тема: {label}\n"
            f"Используй /queue для просмотра."
        )

    await db_manager.log_event(
        "scrape_completed",
        f"Парсинг завершён. Новых постов: {len(new_posts)}",
        details={"new_posts": len(new_posts), "bloggers": len(bloggers)},
    )
    logger.info("scrape_job_done", new_posts=len(new_posts))
    return len(new_posts)


# ── Publish job ────────────────────────────────────────────────────────────────

async def run_publish_job() -> None:
    """
    Publish all APPROVED posts that are scheduled for now or earlier.
    Publishes to Telegram channel and Threads simultaneously.
    """
    posts = await db_manager.get_approved_posts()
    if not posts:
        return

    tg = _get_tg_publisher()
    th = _get_th_publisher()

    for post in posts:
        try:
            # Publish to Telegram channel
            tg_msg_id = await tg.publish(post.rewritten_text or "")

            # Publish to Threads (parallel, non-blocking on failure)
            threads_post_id = None
            try:
                threads_post_id = await th.publish(post.rewritten_text or "")
            except Exception as te:
                logger.warning("threads_publish_failed", post_id=post.id, error=str(te))

            await db_manager.update_post_status(
                post.id,
                PostStatus.PUBLISHED,
                telegram_message_id=tg_msg_id,
                threads_post_id=threads_post_id,
                published_at=datetime.now(timezone.utc),
            )
            logger.info("post_published", post_id=post.id)

            # Rate-limit: wait between posts to avoid Telegram flood limits
            await asyncio.sleep(3)

        except Exception as e:
            logger.error("publish_failed", post_id=post.id, error=str(e))
            await db_manager.update_post_status(
                post.id,
                PostStatus.FAILED,
                publish_errors={"error": str(e), "timestamp": datetime.utcnow().isoformat()},
            )
            await tg.notify_admin(
                f"❌ <b>Ошибка публикации</b>\n"
                f"Пост ID: {post.id}\n"
                f"Ошибка: {esc(e)}"
            )


# ── Token report job ───────────────────────────────────────────────────────────

async def run_token_report_job() -> None:
    """Send daily token usage summary to admin."""
    from sqlalchemy import func, select
    from database.db_manager import AsyncSessionLocal
    from database.models import Post as PostModel

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select as sa_select
        row = (await session.execute(
            sa_select(
                func.count(PostModel.id).label("total"),
                func.sum(PostModel.tokens_used).label("tokens"),
            ).where(PostModel.status == PostStatus.PUBLISHED)
        )).one()

    total = row.total or 0
    tokens = row.tokens or 0
    cost = tokens / 1_000_000 * 0.50  # conservative estimate

    tg = _get_tg_publisher()
    await tg.notify_admin(
        f"📊 <b>Ежедневный отчёт</b>\n\n"
        f"Опубликовано всего: {total} постов\n"
        f"Токенов использовано: {tokens:,}\n"
        f"Примерная стоимость: ~${cost:.4f}"
    )


# ── Scheduler setup ────────────────────────────────────────────────────────────

def create_scheduler() -> AsyncIOScheduler:
    """Build and return the configured scheduler (not yet started)."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Scrape & rewrite job
    scheduler.add_job(
        run_scrape_job,
        trigger=IntervalTrigger(hours=_settings.scrape_interval_hours),
        id="scrape_job",
        name="YouTube scrape + AI rewrite",
        max_instances=1,           # never run overlapping scrapes
        misfire_grace_time=300,    # if missed, retry within 5 min
        replace_existing=True,
    )

    # Publish queue job
    scheduler.add_job(
        run_publish_job,
        trigger=IntervalTrigger(minutes=30),
        id="publish_job",
        name="Publish approved posts",
        max_instances=1,
        misfire_grace_time=120,
        replace_existing=True,
    )

    # Daily token report
    scheduler.add_job(
        run_token_report_job,
        trigger=IntervalTrigger(hours=24),
        id="token_report",
        name="Daily token report",
        replace_existing=True,
    )

    return scheduler
