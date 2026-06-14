# Content Bot

Телеграм-бот, который находит свежие видео топовых YouTube-блогеров, переписывает
их в уникальные посты для русскоязычной аудитории с помощью Claude и публикует в
Telegram-канал после модерации.

Два контент-домена в одном канале: **финансы** и **вайб-кодинг / бизнес на AI** —
у каждого блогера своя категория и свой стиль рерайта.

---

## Возможности

- **Поиск свежего контента** — обход uploads-плейлистов каналов, фильтр по дате и
  вовлечённости (просмотры, лайки, комментарии).
- **Рерайт через Claude** — двухфазный пайплайн (Haiku извлекает тезисы → Sonnet
  пишет пост), кэширование системного промпта для экономии токенов.
- **Domain-aware адаптация** — отдельные промпты под финансы и под вайб-кодинг.
- **Дедупликация** — по видео и по теме (`topic_signature`), чтобы сюжеты не
  повторялись между блогерами.
- **Модерация в Telegram** — карточки постов с кнопками «Одобрить / Отклонить /
  Редактировать».
- **Ролевая модель** — администратор (полный доступ) и редакторы (ревью, правка,
  одобрение публикаций).
- **Планировщик** — парсинг, публикация по расписанию, ежедневный отчёт по токенам.
- **Наблюдаемость** — структурированные логи, health-monitor, алерты в Telegram.

## Архитектура

```
YouTube (uploads playlist)
      │  scrapers/youtube_scraper.py
      ▼
  ранжирование + дедуп (видео/тема)
      │  scheduler/task_scheduler.py
      ▼
  рерайт (Claude)            processors/content_processor.py
      │  Haiku → Sonnet
      ▼
  очередь модерации (SQLite) database/  ·  bot/handlers.py
      │  одобрение
      ▼
  публикация                 publishers/telegram_publisher.py
```

## Стек

- Python 3.12+ · `python-telegram-bot` (polling/webhook)
- Anthropic Claude (Haiku + Sonnet) · `google-api-python-client` (YouTube Data API v3)
- SQLAlchemy (async) + SQLite · APScheduler · Pydantic Settings
- `tenacity` (retry) · `structlog` (логи)

## Структура проекта

```
content_bot/
├── main.py                 # точка входа: init → scheduler → bot
├── config.py               # конфигурация (Pydantic Settings, .env)
├── bot/                    # Telegram: handlers, application factory
├── scrapers/               # YouTube-скрапер
├── processors/             # рерайт контента (Claude)
├── publishers/             # публикация в Telegram / Threads
├── scheduler/              # APScheduler-задачи
├── database/               # модели и async-доступ к БД
├── monitoring/             # health-monitor, обработка ошибок
├── security/               # авторизация (роли admin/editor)
└── utils/                  # логирование, хелперы
```

## Быстрый старт

```bash
# 1. Зависимости (рекомендуется uv)
uv venv && uv pip install -r requirements.txt

# 2. Конфигурация
cp .env.example .env        # заполнить токены и ключи

# 3. Запуск
python main.py
```

## Конфигурация

Все параметры — через переменные окружения (см. `.env.example`). Ключевые:

| Переменная | Назначение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `ADMIN_TELEGRAM_ID` | ID администратора (полный доступ) |
| `EDITOR_TELEGRAM_IDS` | ID редакторов через запятую (ревью/правка/аппрув) |
| `TELEGRAM_CHANNEL_ID` | Канал для публикации |
| `ANTHROPIC_API_KEY` | Ключ Claude |
| `YOUTUBE_API_KEY` | Ключ YouTube Data API v3 |
| `MIN_VIEWS_THRESHOLD` | Минимум просмотров для кандидата |
| `SCRAPE_RECENT_DAYS` | Окно свежести видео (дни) |
| `TOPIC_DEDUP_DAYS` | Антиповтор тем (дни) |
| `SCRAPE_INTERVAL_HOURS` | Период парсинга |

## Команды бота

| Команда | Доступ | Действие |
|---|---|---|
| `/queue` | admin/editor | Посты на проверке |
| `/stats` | admin/editor | Статистика токенов и публикаций |
| `/bloggers` | admin/editor | Список активных блогеров |
| `/add_blogger` | admin | Добавить YouTube-канал |
| `/remove_blogger` | admin | Отключить блогера |
| `/scrape_now` | admin | Запустить парсинг вручную |

## Деплой

Запускается как systemd-сервис в режиме polling. Пример unit-файла:

```ini
[Unit]
Description=Content Bot
After=network-online.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/opt/content_bot
ExecStart=/opt/content_bot/venv/bin/python main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl restart content_bot
journalctl -u content_bot -f
```

## Безопасность

- Секреты только в `.env` (в репозитории — `.env.example` с плейсхолдерами).
- Динамический текст в HTML-сообщениях экранируется (`utils.helpers.esc`).
- Логи внешних клиентов приглушены, чтобы токены не попадали в журнал.
- Доступ к боту — только по Telegram ID (роли admin / editor).
