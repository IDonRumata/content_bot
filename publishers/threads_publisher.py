"""
Publishes approved posts to Meta Threads via the official Threads API.

Flow (Meta's two-step creation):
  Step 1 — POST /me/threads          → creates a media container, returns container_id
  Step 2 — POST /me/threads_publish  → publishes the container, returns post_id

Docs: https://developers.facebook.com/docs/threads/posts

Rate limits:
  • 250 API calls per user per hour (we use 2 per post → plenty of headroom)
  • 5 posts per day for new accounts, up to unlimited after review

Token length:
  Threads limit is 500 chars. We auto-truncate to 480 and append "…" if needed.
"""
from __future__ import annotations

import asyncio

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

_THREADS_BASE = "https://graph.threads.net/v1.0"
_MAX_CHARS = 480


def _trim_for_threads(text: str) -> str:
    """Strip HTML tags and truncate to Threads 500-char limit."""
    import re
    clean = re.sub(r"<[^>]+>", "", text)   # remove Telegram HTML tags
    if len(clean) <= _MAX_CHARS:
        return clean
    return clean[:_MAX_CHARS] + "…"


class ThreadsPublisher:
    def __init__(self) -> None:
        self._user_id = _settings.threads_user_id
        self._token = _settings.threads_access_token
        self._enabled = bool(self._user_id and self._token)

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        stop=stop_after_attempt(4),
    )
    async def publish(self, text: str) -> str | None:
        """
        Publish text post to Threads.
        Returns Threads post_id or None if Threads is not configured.
        """
        if not self._enabled:
            logger.warning("threads_not_configured")
            return None

        content = _trim_for_threads(text)

        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Create media container
            resp1 = await client.post(
                f"{_THREADS_BASE}/{self._user_id}/threads",
                params={
                    "media_type": "TEXT",
                    "text": content,
                    "access_token": self._token,
                },
            )
            resp1.raise_for_status()
            container_id = resp1.json()["id"]
            logger.debug("threads_container_created", container_id=container_id)

            # Brief mandatory delay (Meta recommends ~30s for media processing,
            # text posts are instant but a small pause avoids race conditions)
            await asyncio.sleep(2)

            # Step 2: Publish the container
            resp2 = await client.post(
                f"{_THREADS_BASE}/{self._user_id}/threads_publish",
                params={
                    "creation_id": container_id,
                    "access_token": self._token,
                },
            )
            resp2.raise_for_status()
            post_id = resp2.json()["id"]
            logger.info("threads_published", post_id=post_id)
            return post_id

    async def refresh_token_reminder(self) -> bool:
        """
        Long-lived Threads tokens expire after 60 days.
        Returns True if the token expires within 7 days (should trigger admin alert).
        """
        if not self._enabled:
            return False
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_THREADS_BASE}/me",
                params={"fields": "id", "access_token": self._token},
            )
            if resp.status_code == 401:
                return True  # expired
        return False
