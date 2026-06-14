"""
YouTube scraper using official YouTube Data API v3.

Why YouTube:
  • Official free API — no scraping bans, no ToS violations
  • Both Humphrey Yang & Vivian Tu have active YouTube channels
  • Rich popularity signals: views, likes, comments
  • Video transcripts available for content rewriting
  • Quota: 10 000 units/day free (1 search = 100 units, 1 video detail = 1 unit)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from config import get_settings
from database import db_manager
from database.models import Post
from utils.helpers import format_number, hash_content
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()


def _is_transient_http_error(exc: BaseException) -> bool:
    """
    Retry only on transient YouTube API errors (timeouts, 5xx, rate-limit 429).
    Permanent 4xx like 403 ``accountDelegationForbidden`` or 404 will never
    succeed on retry — failing fast avoids three pointless 30s back-offs.
    """
    if not isinstance(exc, HttpError):
        return False
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        return True  # unknown — give it a chance
    return status == 429 or status >= 500


def _http_error_summary(exc: HttpError) -> str:
    """
    Short, secret-free description of a YouTube HttpError.
    str(HttpError) embeds the full request URL incl. ?key=<API_KEY>, so it must
    never be logged directly. We surface only status + reason.
    """
    status = getattr(getattr(exc, "resp", None), "status", "?")
    reason = ""
    try:
        for err in (exc.error_details or []):  # type: ignore[attr-defined]
            if err.get("reason"):
                reason = err["reason"]
                break
    except Exception:
        pass
    return f"HTTP {status} {reason}".strip()


@dataclass
class VideoMeta:
    video_id: str
    title: str
    description: str
    views: int
    likes: int
    comments: int
    published_at: datetime
    channel_id: str
    channel_name: str
    url: str
    transcript: str  # plain text, may be empty


class YouTubeScraper:
    """
    Fetch top-performing videos from a YouTube channel and extract
    enough textual content for rewriting.
    """

    # Score = views + likes*10 + comments*5  (tuneable)
    LIKE_WEIGHT = 10
    COMMENT_WEIGHT = 5

    def __init__(self) -> None:
        self._yt = build(
            "youtube",
            "v3",
            developerKey=_settings.youtube_api_key,
            cache_discovery=False,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def scrape_blogger(
        self, channel_id: str, max_results: int = 20
    ) -> list[VideoMeta]:
        """
        Fetch recent videos for a channel, sort by popularity score,
        return top `max_results` that haven't been seen before.
        """
        try:
            raw_videos = self._fetch_channel_videos(channel_id, max_results=50)
            video_ids = [v["id"]["videoId"] for v in raw_videos]
            stats = self._fetch_video_stats(video_ids)
            videos = self._merge_and_rank(raw_videos, stats, channel_id)

            # Filter already-scraped & below threshold
            new_videos: list[VideoMeta] = []
            for v in videos:
                if v.views < _settings.min_views_threshold:
                    continue
                if await db_manager.post_exists(v.video_id):
                    continue
                new_videos.append(v)
                if len(new_videos) >= max_results:
                    break

            logger.info(
                "scrape_done",
                channel_id=channel_id,
                total_fetched=len(videos),
                new=len(new_videos),
            )
            return new_videos

        except HttpError as e:
            # NEVER log str(e): the HttpError repr contains the full request URL
            # including ?key=<YOUTUBE_API_KEY>, which would leak into journald.
            summary = _http_error_summary(e)
            logger.error("youtube_api_error", channel=channel_id, error=summary)
            await db_manager.log_event(
                "scrape_error", f"YouTube API error for {channel_id}: {summary}",
                severity="error", details={"channel_id": channel_id}
            )
            return []

    def get_transcript(self, video_id: str) -> str:
        """
        Fetch video transcript (preferring English, then auto-generated).
        Returns plain text or empty string if unavailable.
        Token-conscious: truncate to 3000 chars to limit downstream costs.
        """
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(
                video_id, languages=["en", "en-US", "en-GB"]
            )
            text = " ".join(t["text"] for t in transcript_list)
            return text[:3000]  # ~750 tokens — enough context, not wasteful
        except (NoTranscriptFound, TranscriptsDisabled):
            return ""
        except Exception as e:
            logger.warning("transcript_fetch_failed", video_id=video_id, error=str(e))
            return ""

    # ── Internal helpers ──────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception(_is_transient_http_error),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        stop=stop_after_attempt(3),
    )
    def _fetch_channel_videos(self, channel_id: str, max_results: int = 50) -> list[dict]:
        """
        Fetch a channel's RECENT uploads via its uploads playlist.

        Why not search.list(channelId=..., order="date")?
          • For some channels YouTube returns 403 ``accountDelegationForbidden``
            on search.list regardless of parameters — it just never works.
          • search.list costs 100 quota units; playlistItems costs 1.
        The uploads playlist id is the channel id with the "UC" prefix swapped
        for "UU" (documented, stable). Items come back newest-first, which is
        exactly the "find fresh content" behaviour we need.
        """
        uploads_playlist = "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id
        response = (
            self._yt.playlistItems()
            .list(
                part="snippet,contentDetails",
                playlistId=uploads_playlist,
                maxResults=min(max_results, 50),
            )
            .execute()
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=_settings.scrape_recent_days)
        out: list[dict] = []
        for item in response.get("items", []):
            cd = item.get("contentDetails", {})
            sn = item.get("snippet", {})
            video_id = cd.get("videoId") or sn.get("resourceId", {}).get("videoId")
            if not video_id:
                continue
            published = cd.get("videoPublishedAt") or sn.get("publishedAt")
            if published:
                try:
                    pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    if pub_dt < cutoff:
                        continue  # too old — keep content fresh
                except ValueError:
                    pass
            # Re-shape to the structure _merge_and_rank expects.
            out.append({
                "id": {"kind": "youtube#video", "videoId": video_id},
                "snippet": {
                    "title": sn.get("title", ""),
                    "description": sn.get("description", ""),
                    "channelTitle": sn.get("channelTitle", ""),
                    "publishedAt": published or "1970-01-01T00:00:00Z",
                },
            })
        return out

    @retry(
        retry=retry_if_exception(_is_transient_http_error),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        stop=stop_after_attempt(3),
    )
    def _fetch_video_stats(self, video_ids: list[str]) -> dict[str, dict]:
        """Batch-fetch statistics (1 unit per 50 videos — very cheap)."""
        if not video_ids:
            return {}
        result: dict[str, dict] = {}
        # API allows max 50 IDs per request
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            response = (
                self._yt.videos()
                .list(part="statistics,contentDetails", id=",".join(chunk))
                .execute()
            )
            for item in response.get("items", []):
                result[item["id"]] = item.get("statistics", {})
        return result

    def _merge_and_rank(
        self,
        raw_videos: list[dict],
        stats: dict[str, dict],
        channel_id: str,
    ) -> list[VideoMeta]:
        videos: list[VideoMeta] = []
        for item in raw_videos:
            vid_id = item["id"]["videoId"]
            snippet = item["snippet"]
            s = stats.get(vid_id, {})

            views = int(s.get("viewCount", 0))
            likes = int(s.get("likeCount", 0))
            comments = int(s.get("commentCount", 0))

            published_at = datetime.fromisoformat(
                snippet["publishedAt"].replace("Z", "+00:00")
            ).replace(tzinfo=timezone.utc)

            videos.append(
                VideoMeta(
                    video_id=vid_id,
                    title=snippet.get("title", ""),
                    description=snippet.get("description", "")[:500],  # limit description
                    views=views,
                    likes=likes,
                    comments=comments,
                    published_at=published_at,
                    channel_id=channel_id,
                    channel_name=snippet.get("channelTitle", ""),
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    transcript="",  # filled lazily when needed
                )
            )

        # Rank by weighted engagement score
        videos.sort(
            key=lambda v: v.views + v.likes * self.LIKE_WEIGHT + v.comments * self.COMMENT_WEIGHT,
            reverse=True,
        )
        return videos

    # ── Factory for Post creation ─────────────────────────────────────────────

    async def video_to_post(
        self, video: VideoMeta, blogger_id: int, category: str = "finance",
        transcript: str | None = None,
    ) -> Post:
        """Build an unsaved Post object from scraped VideoMeta."""
        # Reuse a transcript already fetched by the caller; else fetch now.
        if transcript is None:
            transcript = self.get_transcript(video.video_id)
        return Post(
            blogger_id=blogger_id,
            category=category,
            source_id=video.video_id,
            source_url=video.url,
            source_title=video.title,
            source_views=video.views,
            source_likes=video.likes,
            source_comments=video.comments,
            source_published_at=video.published_at,
            # Store raw transcript as the "seed" — processor will rewrite it
            rewritten_text=f"[RAW]\nTitle: {video.title}\n\nDescription: {video.description}\n\nTranscript: {transcript}",
        )
