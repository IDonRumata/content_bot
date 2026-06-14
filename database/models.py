"""
SQLAlchemy ORM models.
All tables use async-compatible aiosqlite driver.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PostStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"   # scraped, awaiting admin approval
    APPROVED = "approved"               # admin approved, queued for publish
    REJECTED = "rejected"               # admin rejected
    PUBLISHED = "published"             # sent to all channels
    FAILED = "failed"                   # publish attempt failed


class Blogger(Base):
    """Tracked bloggers (YouTube channels)."""
    __tablename__ = "bloggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="youtube")
    # Content domain — drives which rewrite prompt is used.
    # "finance" (default) or "vibecoding".
    category: Mapped[str] = mapped_column(String(32), default="finance")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    posts: Mapped[list["Post"]] = relationship("Post", back_populates="blogger")

    def __repr__(self) -> str:
        return f"<Blogger {self.name} ({self.channel_id})>"


class Post(Base):
    """A scraped + rewritten post ready for publishing."""
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    blogger_id: Mapped[int] = mapped_column(ForeignKey("bloggers.id"), nullable=False)

    # Source metadata
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    source_title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_views: Mapped[int] = mapped_column(BigInteger, default=0)
    source_likes: Mapped[int] = mapped_column(BigInteger, default=0)
    source_comments: Mapped[int] = mapped_column(BigInteger, default=0)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Content domain inherited from the blogger ("finance" / "vibecoding").
    category: Mapped[str] = mapped_column(String(32), default="finance")

    # Rewritten content
    rewritten_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # dedup
    # Canonical topic slug (e.g. "emergency-fund", "ai-coding-agents") used for
    # cross-blogger topic de-duplication so themes don't repeat.
    topic_signature: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)

    # Workflow
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus), default=PostStatus.PENDING_REVIEW, index=True
    )
    review_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Publish results
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    threads_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publish_errors: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    scraped_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    blogger: Mapped["Blogger"] = relationship("Blogger", back_populates="posts")

    def __repr__(self) -> str:
        return f"<Post {self.source_id} [{self.status}]>"


class SystemEvent(Base):
    """Audit log for all critical system events."""
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info/warn/error/critical
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
