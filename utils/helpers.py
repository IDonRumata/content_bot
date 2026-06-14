"""Utility helpers shared across modules."""
from __future__ import annotations

import hashlib
import html
import re
import secrets
import string


def esc(text: object) -> str:
    """
    Escape arbitrary text for safe interpolation into Telegram HTML messages.

    Telegram parses a small HTML subset; any stray ``<``/``>``/``&`` in dynamic
    content (e.g. an exception repr like ``<Future at 0x...>``) makes
    ``send_message(parse_mode="HTML")`` fail with "unsupported start tag".
    Always wrap dynamic values with this before putting them in an alert.
    """
    return html.escape(str(text), quote=False)


def generate_token(length: int = 32) -> str:
    """Cryptographically secure random token."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_content(text: str) -> str:
    """SHA-256 hash of content for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def truncate(text: str, max_len: int = 4096) -> str:
    """Truncate text to Telegram message limit."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def sanitize_html(text: str) -> str:
    """Remove dangerous HTML tags, keep safe Telegram HTML subset."""
    allowed = re.compile(r"<(?!/?(b|i|u|s|code|pre|a)(\s[^>]*)?>)[^>]+>", re.IGNORECASE)
    return allowed.sub("", text)


def count_tokens_approx(text: str) -> int:
    """Rough token count: ~4 chars per token for Latin, ~2 for Cyrillic."""
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    latin = len(text) - cyrillic
    return latin // 4 + cyrillic // 2


def format_number(n: int) -> str:
    """Human-readable number: 1_250_000 → '1.25M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)
