"""
Telegram bot command and callback handlers.

Approval workflow:
  1. Scheduler scrapes YouTube → rewrites post → saves as PENDING_REVIEW
  2. Bot sends card to admin with [Approve] / [Reject] / [Edit] buttons
  3. Admin taps Approve → status = APPROVED, post enters publish queue
  4. Admin taps Reject  → status = REJECTED
  5. Admin taps Edit    → bot asks for new text, saves it, re-shows card

Commands:
  /start          — welcome
  /queue          — list pending review posts
  /stats          — token/cost/publish stats
  /bloggers       — show active bloggers
  /add_blogger    — add new YouTube channel
  /remove_blogger — deactivate a blogger
  /scrape_now     — trigger manual scrape
  /help           — command list
"""
from __future__ import annotations

from datetime import datetime, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import db_manager
from database.models import Post, PostStatus
from security.auth import admin_only, editor_or_admin
from utils.helpers import esc, format_number, truncate
from utils.logger import get_logger

logger = get_logger(__name__)

# Conversation states
WAITING_EDIT_TEXT = 1
WAITING_NEW_BLOGGER_ID = 2
WAITING_NEW_BLOGGER_NAME = 3


# ── Keyboard builders ─────────────────────────────────────────────────────────

def _review_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{post_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{post_id}"),
        ],
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit:{post_id}"),
        ],
    ])


def _post_card(post: Post) -> str:
    """Format a review card for admin."""
    views = format_number(post.source_views)
    likes = format_number(post.source_likes)
    src_date = post.source_published_at.strftime("%d.%m.%Y") if post.source_published_at else "—"
    return (
        f"📋 <b>Пост на проверку</b> [ID: {post.id}]\n\n"
        f"🎬 <b>Источник:</b> <a href='{esc(post.source_url)}'>{esc(post.source_title[:60])}</a>\n"
        f"👁 {views} просмотров   ❤️ {likes} лайков   📅 {src_date}\n\n"
        f"──────────────────────\n\n"
        f"{truncate(post.rewritten_text or '', 3200)}\n\n"
        f"──────────────────────\n"
        f"🪙 Токенов использовано: {post.tokens_used}"
    )


# ── /start ────────────────────────────────────────────────────────────────────

@editor_or_admin
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>Content Bot активен</b>\n\n"
        "Я автоматически собираю топ-посты финансовых блоггеров,\n"
        "переписываю их для СНГ-аудитории и жду твоего одобрения.\n\n"
        "Используй /help для списка команд.",
        parse_mode="HTML",
    )


# ── /help ─────────────────────────────────────────────────────────────────────

@editor_or_admin
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>Команды:</b>\n\n"
        "/queue — посты, ожидающие проверки\n"
        "/stats — статистика токенов и публикаций\n"
        "/bloggers — список активных блоггеров\n"
        "/add_blogger — добавить блоггера\n"
        "/remove_blogger — отключить блоггера\n"
        "/scrape_now — запустить парсинг прямо сейчас\n"
        "/help — эта подсказка",
        parse_mode="HTML",
    )


# ── /queue ────────────────────────────────────────────────────────────────────

@editor_or_admin
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    posts = await db_manager.get_posts_for_review()
    if not posts:
        await update.message.reply_text("📭 Очередь на проверку пуста.")
        return

    await update.message.reply_text(f"📬 Постов на проверке: <b>{len(posts)}</b>", parse_mode="HTML")

    for post in posts[:5]:  # show max 5 at once to avoid flood
        msg = await update.message.reply_text(
            _post_card(post),
            parse_mode="HTML",
            reply_markup=_review_keyboard(post.id),
            disable_web_page_preview=True,
        )
        # Save message ID so callback can find this post
        await db_manager.update_post_status(
            post.id, PostStatus.PENDING_REVIEW, review_message_id=msg.message_id
        )


# ── /stats ────────────────────────────────────────────────────────────────────

@editor_or_admin
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from sqlalchemy import func, select
    from database.db_manager import AsyncSessionLocal
    from database.models import Post as PostModel, PostStatus

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select as sa_select
        rows = (await session.execute(
            sa_select(
                PostModel.status,
                func.count(PostModel.id).label("cnt"),
                func.sum(PostModel.tokens_used).label("tokens"),
            ).group_by(PostModel.status)
        )).all()

    lines = ["<b>📊 Статистика</b>\n"]
    total_tokens = 0
    for row in rows:
        status_label = {
            "pending_review": "⏳ На проверке",
            "approved": "✅ Одобрено",
            "rejected": "❌ Отклонено",
            "published": "📤 Опубликовано",
            "failed": "💥 Ошибка",
        }.get(row.status, row.status)
        tokens = row.tokens or 0
        total_tokens += tokens
        lines.append(f"{status_label}: {row.cnt} постов, {tokens:,} токенов")

    # Rough cost estimate (Haiku pricing)
    cost = total_tokens / 1_000_000 * 0.50
    lines.append(f"\n💰 Примерные затраты: ~${cost:.4f}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /bloggers ─────────────────────────────────────────────────────────────────

@editor_or_admin
async def cmd_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bloggers = await db_manager.get_active_bloggers()
    if not bloggers:
        await update.message.reply_text("Нет активных блоггеров.")
        return
    lines = ["<b>🎬 Активные блоггеры:</b>\n"]
    for b in bloggers:
        cat = getattr(b, "category", "finance") or "finance"
        tag = "💸" if cat == "finance" else "🧑‍💻"
        lines.append(f"{tag} <b>{b.name}</b>\n  ID канала: <code>{b.channel_id}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /add_blogger — ConversationHandler ───────────────────────────────────────

@admin_only
async def cmd_add_blogger_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Введи YouTube <b>Channel ID</b> блоггера.\n\n"
        "Найти можно на странице канала → О канале → Поделиться → скопировать ID.\n"
        "Пример: <code>UCF5TJYJHoEL9LVGSHiDDBlg</code>\n\n"
        "/cancel — отмена",
        parse_mode="HTML",
    )
    return WAITING_NEW_BLOGGER_ID


async def _recv_blogger_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel_id = update.message.text.strip()
    if not channel_id or len(channel_id) < 10:
        await update.message.reply_text("Некорректный Channel ID. Попробуй снова или /cancel.")
        return WAITING_NEW_BLOGGER_ID
    context.user_data["new_channel_id"] = channel_id
    await update.message.reply_text("Теперь введи <b>имя блоггера</b> (для отображения):", parse_mode="HTML")
    return WAITING_NEW_BLOGGER_NAME


async def _recv_blogger_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    channel_id = context.user_data.get("new_channel_id", "")
    if not name:
        await update.message.reply_text("Имя не может быть пустым. Попробуй снова или /cancel.")
        return WAITING_NEW_BLOGGER_NAME
    blogger = await db_manager.add_blogger(channel_id, name)
    await update.message.reply_text(
        f"✅ Блоггер <b>{blogger.name}</b> добавлен!\n"
        f"Следующий парсинг захватит его канал.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# ── /remove_blogger ───────────────────────────────────────────────────────────

@admin_only
async def cmd_remove_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Использование: /remove_blogger <channel_id>\n"
            "Channel ID можно посмотреть в /bloggers"
        )
        return
    channel_id = args[0].strip()
    ok = await db_manager.deactivate_blogger(channel_id)
    if ok:
        await update.message.reply_text(f"🔕 Блоггер <code>{channel_id}</code> отключён.", parse_mode="HTML")
    else:
        await update.message.reply_text("Блоггер не найден.")


# ── /scrape_now ───────────────────────────────────────────────────────────────

@admin_only
async def cmd_scrape_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⚙️ Запускаю парсинг вручную…")
    # Import here to avoid circular imports
    from scheduler.task_scheduler import run_scrape_job
    try:
        count = await run_scrape_job()
        await update.message.reply_text(
            f"✅ Парсинг завершён. Новых постов на проверке: <b>{count}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("manual_scrape_failed", error=str(e))
        await update.message.reply_text(f"❌ Ошибка парсинга: {e}")


# ── Callback query handlers (Approve / Reject / Edit) ────────────────────────

@editor_or_admin
async def callback_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    post_id = int(query.data.split(":")[1])

    from datetime import timedelta
    from config import get_settings
    settings = get_settings()

    # Schedule posting at next available slot
    scheduled_at = datetime.now(timezone.utc)

    await db_manager.update_post_status(
        post_id,
        PostStatus.APPROVED,
        scheduled_at=scheduled_at,
    )
    await query.edit_message_reply_markup(reply_markup=None)
    await query.edit_message_text(
        query.message.text + f"\n\n✅ <b>Одобрено</b> — в очереди на публикацию",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    logger.info("post_approved", post_id=post_id)


@editor_or_admin
async def callback_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    post_id = int(query.data.split(":")[1])
    await db_manager.update_post_status(post_id, PostStatus.REJECTED)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.edit_message_text(
        query.message.text + "\n\n❌ <b>Отклонено</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    logger.info("post_rejected", post_id=post_id)


@editor_or_admin
async def callback_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask user to send new text for the post."""
    query = update.callback_query
    await query.answer()
    post_id = int(query.data.split(":")[1])
    context.user_data["editing_post_id"] = post_id
    await query.message.reply_text(
        f"✏️ Отправь новый текст для поста ID <b>{post_id}</b>.\n"
        "Он заменит сгенерированный вариант.\n/cancel — отмена",
        parse_mode="HTML",
    )


@editor_or_admin
async def recv_edited_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post_id = context.user_data.get("editing_post_id")
    if not post_id:
        return
    new_text = update.message.text.strip()
    if not new_text:
        return
    await db_manager.update_post_status(
        post_id, PostStatus.PENDING_REVIEW, rewritten_text=new_text
    )
    context.user_data.pop("editing_post_id", None)

    post = await db_manager.get_post_by_id(post_id)
    msg = await update.message.reply_text(
        _post_card(post),
        parse_mode="HTML",
        reply_markup=_review_keyboard(post_id),
        disable_web_page_preview=True,
    )
    await db_manager.update_post_status(
        post_id, PostStatus.PENDING_REVIEW, review_message_id=msg.message_id
    )


# ── Handler registration helper ───────────────────────────────────────────────

def register_handlers(app) -> None:
    """Register all handlers with the Application instance."""
    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("bloggers", cmd_bloggers))
    app.add_handler(CommandHandler("remove_blogger", cmd_remove_blogger))
    app.add_handler(CommandHandler("scrape_now", cmd_scrape_now))

    # Add blogger conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_blogger", cmd_add_blogger_start)],
        states={
            WAITING_NEW_BLOGGER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, _recv_blogger_id)],
            WAITING_NEW_BLOGGER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, _recv_blogger_name)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(callback_approve, pattern=r"^approve:\d+$"))
    app.add_handler(CallbackQueryHandler(callback_reject, pattern=r"^reject:\d+$"))
    app.add_handler(CallbackQueryHandler(callback_edit, pattern=r"^edit:\d+$"))

    # Free-text handler for editing posts (must be last)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        recv_edited_text,
    ))
