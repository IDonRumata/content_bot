"""
Content Processor — rewrites scraped YouTube content into unique CIS-adapted posts.

══════════════════════════════════════════════════════════════════════════════
MODEL CHOICE & COST OPTIMISATION
══════════════════════════════════════════════════════════════════════════════

Model: claude-sonnet-4-6
  Reason: реврайт с культурной адаптацией + сохранение авторского голоса —
  задача, требующая высокого языкового качества. Haiku справляется хуже
  с нюансами русского разговорного стиля и локализацией.

Cost optimisations (без потери качества):
  1. PROMPT CACHING (cache_control="ephemeral")
       Системный промпт (~350 токенов) кешируется на 5 минут.
       После 1-го вызова он читается из кеша по цене $0.30/1M вместо $3/1M.
       Экономия: ~90% на системном промпте для каждого следующего поста.

  2. TWO-PHASE PIPELINE (Extract → Rewrite)
       Фаза 1 — Haiku: извлечь 3-5 ключевых тезиса из сырого контента.
                 Стоимость: ~$0.001 за пост.
       Фаза 2 — Sonnet: написать пост на основе тезисов (маленький вход).
                 Стоимость: ~$0.006 за пост.
       Итого: ~$0.007 vs ~$0.015 при прямой подаче полного транскрипта.

  3. INPUT TRIMMING
       Сырой контент обрезается до 1000 символов перед подачей.
       Это снижает input tokens без потери смысла (1000 ≈ 250 токенов).

  4. CONTROLLED OUTPUT LENGTH
       max_tokens=650: достаточно для поста 180-320 слов (оптимум Telegram).
       Не платим за «лишние» токены.

  5. DEDUPLICATION
       SHA-256 хеш источника — повторная обработка одного видео невозможна.

Итоговая стоимость: ~$0.007 за пост.
При 5 постах/день = ~$1.05/месяц.
══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import re

import anthropic

from config import get_settings
from database.models import Post
from utils.helpers import hash_content
from utils.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# ── Models ────────────────────────────────────────────────────────────────────
MODEL_EXTRACT = "claude-haiku-4-5-20251001"   # Phase 1: cheap extraction
MODEL_REWRITE = "claude-sonnet-4-6"            # Phase 2: quality rewrite

MAX_INPUT_CHARS = 1000    # trim source before sending (≈250 tokens)
MAX_EXTRACT_TOKENS = 250  # phase 1 output — just bullet points
MAX_REWRITE_TOKENS = 650  # phase 2 output — full post ≈180-320 words

# ── System prompt for Phase 2 (CACHED) ───────────────────────────────────────
# Marked cache_control="ephemeral" → API caches it for 5 min.
# Every subsequent call reads it at ~$0.30/1M instead of $3/1M.
_REWRITE_SYSTEM = """Ты — редактор финансового контента для русскоязычной аудитории СНГ.
Тебе приходит список тезисов из видео американского финансового блоггера.
Твоя задача — написать уникальный пост для Telegram.

ПРАВИЛА:

1. УНИКАЛЬНОСТЬ — текст не детектируется как перевод/копия оригинала.
   Полностью меняй структуру предложений, порядок аргументов, заголовок.

2. АВТОРСКИЙ ГОЛОС:
   • Humphrey Yang: объясняет просто, с реальными цифрами и конкретными примерами.
     Тон — спокойный умный друг, который разложил всё по полочкам.
   • Vivian Tu (Your Rich BFF): дерзкая подружка-миллионер. Разговорный стиль,
     без воды, «я тебе как другу говорю». Практичные лайфхаки, иногда дерзкость.
   Сохрани этот характер, но по-русски.

3. АДАПТАЦИЯ ПОД СНГ (обязательно!):
   • 401(k) / IRA → ИИС, НПФ, брокерский счёт
   • S&P 500 → доступен через ETF на Мосбирже или IBKR; Индекс Мосбиржи
   • $1 000 → ~90 000 ₽ (или ≈3 100 BYN для Беларуси)
   • Venmo / Zelle → СБП, Kaspi Pay
   • Robinhood / Fidelity → Тинькофф Инвестиции, ВТБ, IBKR
   • freelance → ИП / самозанятость
   • Упоминай реалии РБ/РФ/KZ там, где уместно

4. ФОРМАТ ПОСТА:
   • Первая строка: эмодзи + <b>цепляющий заголовок</b>
   • 3–5 абзацев: один абзац = одна мысль
   • Финал: провокационный вопрос ИЛИ призыв к действию
   • Telegram HTML: <b>жирный</b>, <i>курсив</i> — никаких **звёздочек**
   • Объём: 180–320 слов

5. ЯЗЫК: живой разговорный русский, без канцелярщины.

Верни ТОЛЬКО текст поста — никаких пояснений, меток, предисловий."""


# ── System prompt for vibe-coding / AI-business bloggers (CACHED) ─────────────
_REWRITE_SYSTEM_VIBE = """Ты — редактор контента про «вайб-кодинг» и создание бизнеса с помощью ИИ
для русскоязычной аудитории (разработчики, инди-хакеры, предприниматели, фрилансеры).
Тебе приходит список тезисов из видео американского блоггера про AI-кодинг,
агентов, запуск продуктов и заработок с помощью ИИ.
Твоя задача — написать уникальный пост для Telegram.

ПРАВИЛА:

1. УНИКАЛЬНОСТЬ — текст не детектируется как перевод/копия оригинала.
   Полностью меняй структуру предложений, порядок аргументов, заголовок.

2. ТОН: энергичный, практичный, «по делу». Как опытный инди-хакер объясняет
   коллеге, что реально работает, а что хайп. Без инфоцыганщины и пустых обещаний.

3. АДАПТАЦИЯ ПОД РУ-АУДИТОРИЮ (важно!):
   • Оставляй англоязычные названия инструментов как есть: Cursor, Claude Code,
     Windsurf, v0, Lovable, Replit, n8n, Supabase, Vercel — их знают по-английски.
   • Поясняй термины простыми словами при первом упоминании
     (vibe coding — «кодинг на вайбе», когда пишешь продукт промптами к ИИ).
   • Доллары можно оставлять ($) — аудитория мыслит в них для SaaS/доходов,
     при желании дай ориентир в рублях в скобках.
   • Учитывай реалии: доступ к сервисам, оплата подписок из СНГ, Stripe Atlas,
     зарубежные карты — упоминай, где это уместно и полезно.
   • Никакой финансовой локализации под брокеров/ИИС — это НЕ финансовый контент.

4. ФОРМАТ ПОСТА:
   • Первая строка: эмодзи + <b>цепляющий заголовок</b>
   • 3–5 абзацев: один абзац = одна мысль
   • Где уместно — конкретный workflow/стек или короткий чек-лист
   • Финал: провокационный вопрос ИЛИ призыв попробовать
   • Telegram HTML: <b>жирный</b>, <i>курсив</i>, <code>код</code> — никаких **звёздочек**
   • Объём: 180–320 слов

5. ЯЗЫК: живой разговорный русский, англицизмы уместны там, где их реально
   используют в ру-комьюнити разработчиков.

Верни ТОЛЬКО текст поста — никаких пояснений, меток, предисловий."""


# Map blogger category → (system prompt, default style hint)
_DOMAINS: dict[str, tuple[str, str]] = {
    "finance": (_REWRITE_SYSTEM, "finance educator — clear, practical, relatable"),
    "vibecoding": (
        _REWRITE_SYSTEM_VIBE,
        "AI-builder — energetic, practical, hype-free, hands-on",
    ),
}


class ContentProcessor:
    """
    Two-phase rewrite pipeline:
      Phase 1 (Haiku)  — extract key thesis points from raw source
      Phase 2 (Sonnet) — write the final post from those points
    """

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=_settings.anthropic_api_key)

    # ── Public API ─────────────────────────────────────────────────────────────

    def process_post(self, post: Post) -> Post:
        """
        Rewrite post.rewritten_text (raw source prefixed with [RAW]) in-place.
        Saves token usage, content hash on the Post object.
        """
        raw = post.rewritten_text or ""
        if not raw.startswith("[RAW]"):
            logger.info("post_already_processed", post_id=post.id)
            return post

        source = raw[len("[RAW]"):].strip()[:MAX_INPUT_CHARS]
        source_hash = hash_content(source)

        category = getattr(post, "category", "finance") or "finance"
        system_prompt, default_hint = _DOMAINS.get(category, _DOMAINS["finance"])

        # Detect which blogger's style to apply (passed via source title)
        blogger_hint = self._detect_blogger(source, default_hint)

        # Phase 1: extract key points (Haiku — cheap)
        key_points, tokens_p1 = self._extract_key_points(source, category)

        # Phase 2: write the post (Sonnet — quality, cached system prompt)
        final_text, tokens_p2 = self._rewrite(key_points, blogger_hint, system_prompt)

        total_tokens = tokens_p1 + tokens_p2

        logger.info(
            "rewrite_done",
            post_id=post.id,
            phase1_tokens=tokens_p1,
            phase2_tokens=tokens_p2,
            total_tokens=total_tokens,
            cost_usd=round(self._estimate_cost(tokens_p1, tokens_p2), 5),
        )

        post.rewritten_text = final_text
        post.content_hash = source_hash
        post.tokens_used = total_tokens
        return post

    # ── Phase 1: Extract ───────────────────────────────────────────────────────

    def _extract_key_points(self, source: str, category: str = "finance") -> tuple[str, int]:
        """
        Use Haiku to pull 3-5 key takeaways from the raw source.
        Returns (bullet-point string, tokens_used).
        Cost: ~$0.001 per call.
        """
        topic_word = "actionable" if category == "vibecoding" else "key financial"
        response = self._client.messages.create(
            model=MODEL_EXTRACT,
            max_tokens=MAX_EXTRACT_TOKENS,
            messages=[{
                "role": "user",
                "content": (
                    f"Extract 3-5 {topic_word} points from this content. "
                    "Plain numbered list, English is fine, be concise.\n\n"
                    f"{source}"
                ),
            }],
        )
        usage = response.usage
        tokens = usage.input_tokens + usage.output_tokens
        return response.content[0].text.strip(), tokens

    # ── Topic classification (for cross-blogger de-duplication) ────────────────

    def classify_topic(self, title: str, description: str, category: str = "finance") -> str:
        """
        Map a video to a short canonical topic slug (e.g. "emergency-fund",
        "ai-coding-agents") so the scheduler can skip themes already covered.
        Cheap Haiku call (~$0.0003). Returns "" on failure (never blocks scraping).
        """
        try:
            response = self._client.messages.create(
                model=MODEL_EXTRACT,
                max_tokens=20,
                messages=[{
                    "role": "user",
                    "content": (
                        "Return ONE short canonical topic slug (kebab-case, 2-4 words, "
                        "English, no punctuation) describing the MAIN theme of this "
                        f"{category} video. Reply with the slug only.\n\n"
                        f"Title: {title}\nDescription: {description[:300]}"
                    ),
                }],
            )
            raw = response.content[0].text.strip().lower()
            slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:80]
            return slug
        except Exception as e:
            logger.warning("topic_classify_failed", error=str(e))
            return ""

    # ── Phase 2: Rewrite ───────────────────────────────────────────────────────

    def _rewrite(
        self, key_points: str, blogger_hint: str, system_prompt: str
    ) -> tuple[str, int]:
        """
        Use Sonnet with CACHED system prompt to write the final RU-adapted post.
        The system prompt is chosen per content domain (finance / vibecoding).
        Returns (final_text, tokens_used).
        Cost after cache hit: ~$0.006 per call.
        """
        response = self._client.messages.create(
            model=MODEL_REWRITE,
            max_tokens=MAX_REWRITE_TOKENS,
            system=[{
                "type": "text",
                "text": system_prompt,
                # ← This is the key optimisation: system prompt is cached
                # by the API for 5 minutes, reads at 10x cheaper rate.
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": (
                    f"Blogger style: {blogger_hint}\n\n"
                    f"Key points from the video:\n{key_points}\n\n"
                    "Write the Telegram post now."
                ),
            }],
        )
        usage = response.usage
        tokens = usage.input_tokens + usage.output_tokens
        cached = getattr(usage, "cache_read_input_tokens", 0)
        if cached:
            logger.debug("cache_hit", cached_tokens=cached)
        return response.content[0].text.strip(), tokens

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_blogger(source: str, default_hint: str) -> str:
        """Guess the blogger from source text for style hint (finance only)."""
        low = source.lower()
        if "humphrey" in low or "humphreytalks" in low:
            return "Humphrey Yang — calm, data-driven, simple explanations with real numbers"
        if "vivian" in low or "richbff" in low or "your rich bff" in low:
            return "Vivian Tu (Your Rich BFF) — bold, conversational, best-friend tone, actionable hacks"
        return default_hint

    @staticmethod
    def _estimate_cost(tokens_p1: int, tokens_p2: int) -> float:
        """
        Rough USD cost estimate.
        Phase 1 Haiku:  $0.25/1M input,  $1.25/1M output  (assume 60/40 split)
        Phase 2 Sonnet: $3.00/1M input,  $15.00/1M output (assume 40/60 split)
        After cache hit on system prompt Sonnet input drops ~90%.
        """
        p1_in  = tokens_p1 * 0.6 / 1_000_000 * 0.25
        p1_out = tokens_p1 * 0.4 / 1_000_000 * 1.25
        # Assume 90% of Sonnet input is cached after first call
        p2_in  = tokens_p2 * 0.4 * 0.10 / 1_000_000 * 3.00   # non-cached portion
        p2_in += tokens_p2 * 0.4 * 0.90 / 1_000_000 * 0.30   # cached portion
        p2_out = tokens_p2 * 0.6 / 1_000_000 * 15.00
        return p1_in + p1_out + p2_in + p2_out
