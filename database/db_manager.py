"""
Async database manager — single session factory for the whole app.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import get_settings
from database.models import Base, Blogger, Post, PostStatus, SystemEvent
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables, run lightweight migrations, seed default bloggers."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_schema()
    await _seed_default_bloggers()
    logger.info("database_initialized")


async def _migrate_schema() -> None:
    """
    Additive SQLite migrations. ``create_all`` never ALTERs existing tables,
    so columns added to the models after the DB was first created must be
    applied by hand. Each step is idempotent (checked against PRAGMA).
    """
    async with engine.begin() as conn:
        async def _columns(table: str) -> set[str]:
            rows = await conn.execute(text(f"PRAGMA table_info({table})"))
            return {r[1] for r in rows.fetchall()}

        blogger_cols = await _columns("bloggers")
        if "category" not in blogger_cols:
            await conn.execute(text(
                "ALTER TABLE bloggers ADD COLUMN category VARCHAR(32) DEFAULT 'finance'"
            ))
            logger.info("schema_migrated", change="bloggers.category")

        post_cols = await _columns("posts")
        if "category" not in post_cols:
            await conn.execute(text(
                "ALTER TABLE posts ADD COLUMN category VARCHAR(32) DEFAULT 'finance'"
            ))
            logger.info("schema_migrated", change="posts.category")
        if "topic_signature" not in post_cols:
            await conn.execute(text(
                "ALTER TABLE posts ADD COLUMN topic_signature VARCHAR(80)"
            ))
            logger.info("schema_migrated", change="posts.topic_signature")


async def _seed_default_bloggers() -> None:
    """Insert default bloggers if they don't exist yet."""
    async with AsyncSessionLocal() as session:
        for b in _settings.blogger_list:
            exists = await session.scalar(
                select(Blogger).where(Blogger.channel_id == b["channel_id"])
            )
            if not exists:
                session.add(Blogger(channel_id=b["channel_id"], name=b["name"]))
        await session.commit()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Post helpers ─────────────────────────────────────────────────────────────

async def get_active_bloggers() -> list[Blogger]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Blogger).where(Blogger.active == True))
        return list(result.scalars().all())


async def post_exists(source_id: str) -> bool:
    async with AsyncSessionLocal() as session:
        return bool(
            await session.scalar(select(Post.id).where(Post.source_id == source_id))
        )


async def topic_exists_recent(topic_signature: str, days: int) -> bool:
    """
    True if a post with the same topic was created within the last `days`.
    Used to avoid publishing repeating themes across different bloggers.
    Rejected posts don't count — a theme we declined may resurface later.
    """
    if not topic_signature:
        return False
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        return bool(
            await session.scalar(
                select(Post.id)
                .where(Post.topic_signature == topic_signature)
                .where(Post.scraped_at >= cutoff)
                .where(Post.status != PostStatus.REJECTED)
            )
        )


async def save_post(post: Post) -> Post:
    async with AsyncSessionLocal() as session:
        session.add(post)
        await session.commit()
        await session.refresh(post)
        return post


async def get_posts_for_review() -> list[Post]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Post)
            .where(Post.status == PostStatus.PENDING_REVIEW)
            .order_by(Post.scraped_at)
        )
        return list(result.scalars().all())


async def get_approved_posts() -> list[Post]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Post)
            .where(Post.status == PostStatus.APPROVED)
            .where(Post.scheduled_at <= datetime.utcnow())
            .order_by(Post.scheduled_at)
        )
        return list(result.scalars().all())


async def update_post_status(post_id: int, status: PostStatus, **kwargs) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(status=status, updated_at=datetime.utcnow(), **kwargs)
        )
        await session.commit()


async def get_post_by_id(post_id: int) -> Post | None:
    async with AsyncSessionLocal() as session:
        return await session.get(Post, post_id)


async def get_post_by_review_message(msg_id: int) -> Post | None:
    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(Post).where(Post.review_message_id == msg_id)
        )


# ── Blogger helpers ───────────────────────────────────────────────────────────

async def add_blogger(channel_id: str, name: str, category: str = "finance") -> Blogger:
    async with AsyncSessionLocal() as session:
        blogger = Blogger(channel_id=channel_id, name=name, category=category)
        session.add(blogger)
        await session.commit()
        await session.refresh(blogger)
        return blogger


async def deactivate_blogger(channel_id: str) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Blogger)
            .where(Blogger.channel_id == channel_id)
            .values(active=False)
        )
        await session.commit()
        return result.rowcount > 0


# ── Audit log ─────────────────────────────────────────────────────────────────

async def log_event(event_type: str, message: str, severity: str = "info", details: dict | None = None) -> None:
    async with AsyncSessionLocal() as session:
        session.add(SystemEvent(
            event_type=event_type,
            severity=severity,
            message=message,
            details=details,
        ))
        await session.commit()
