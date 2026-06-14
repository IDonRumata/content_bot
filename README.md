# Content Bot 📱

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20API-0088cc?logo=telegram)](https://core.telegram.org/bots/api)

Полностью автоматизированная система для поиска, переписывания и публикации контента из YouTube в Telegram. Бот находит свежие видео топовых блогеров, адаптирует их под русскоязычную аудиторию с помощью Claude AI и публикует в канал после модерации редактором.

**Два контент-домена:** 💸 Финансы | 🧑‍💻 AI & Вайб-кодинг

---

## ✨ Ключевые возможности

| Функция | Описание |
|---------|---------|
| 🔍 **Поиск контента** | Автоматический скан YouTube-плейлистов топовых блогеров, фильтр по свежести и популярности (просмотры, лайки, комментарии) |
| 🤖 **Рерайт через Claude** | Двухфазный пайплайн (Haiku → Sonnet) с кэшированием системного промпта для эффективности |
| 🎯 **Domain-aware рерайт** | Разные стили и форматы для финансового контента и AI/кодинга |
| 🔄 **Умная дедупликация** | По видео и по теме — сюжеты не повторяются между блогерами |
| ✏️ **Модерация в Telegram** | Редактор видит карточку поста с кнопками: одобрить / отклонить / отредактировать |
| 👥 **Ролевая модель** | Admin (полный доступ) и Editor (ревью, правка, публикация) |
| 📅 **Планировщик** | Парсинг по расписанию, автопубликация, статистика по токенам |
| 📊 **Наблюдаемость** | Структурированные логи, health-monitor, алерты в Telegram |

---

## 🏗️ Архитектура

```
┌─────────────────────┐
│ YouTube Channels    │  (uploads playlist, ranked by engagement)
│ (официальный API)   │
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────────────────────────┐
│ YouTubeScraper                               │
│ • Fetch videos by date & popularity         │
│ • Extract transcript (residential proxy)     │
│ • Skip thin content (ads/shorts)            │
└──────────┬──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────┐
│ Deduplication (video + topic)                │
│ • Skip already-seen videos                   │
│ • Skip recent topic duplicates               │
└──────────┬──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────┐
│ ContentProcessor (Claude)                    │
│ • Phase 1: Haiku (extract thesis points)     │
│ • Phase 2: Sonnet (write final post)         │
│ • Domain-specific prompts (finance / AI)     │
│ • Unique headlines, author voice             │
└──────────┬──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────┐
│ Review Queue (SQLite)                        │
│ • Post preview for admin/editor              │
│ • Approve / Reject / Edit buttons            │
└──────────┬──────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────┐
│ Publishers                                   │
│ • Telegram channel (HTML formatted)          │
│ • Meta Threads (optional)                    │
└──────────────────────────────────────────────┘
```

---

## 🛠️ Стек технологий

**Core:**
- Python 3.12+ с type hints
- `python-telegram-bot` (polling / webhook)
- Anthropic Claude (Haiku + Sonnet с кэшированием)

**APIs & Data:**
- YouTube Data API v3 (официальный)
- SQLAlchemy (async ORM) + aiosqlite
- Pydantic (валидация конфиг)

**Reliability:**
- APScheduler (периодические задачи)
- Tenacity (ретраи с экспоненциальной задержкой)
- Structlog (структурированное логирование)

**Deployment:**
- Systemd service
- Environment-based configuration
- SQLite (встроенная БД)

---

## 🚀 Быстрый старт

### 1️⃣ Установка

```bash
# Клонируй репозиторий
git clone https://github.com/IDonRumata/content_bot.git
cd content_bot

# Создай виртуальное окружение
python -m venv venv
source venv/bin/activate  # или venv\Scripts\activate на Windows

# Установи зависимости
pip install -r requirements.txt
```

### 2️⃣ Конфигурация

```bash
# Скопируй шаблон
cp .env.example .env

# Заполни .env (см. таблицу выше)
# Нужны: Telegram токен, Claude ключ, YouTube ключ, ID канала
nano .env
```

### 3️⃣ Запуск

```bash
# Локальная разработка (polling mode)
python main.py

# Просмотри логи в реальном времени (в другом терминале)
tail -f logs/*.log
```

---

## ⚙️ Конфигурация

Все настройки — через переменные окружения в `.env` файле:

### Обязательные переменные

| Переменная | Пример | Где получить |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `123456:ABCdef...` | [@BotFather](https://t.me/botfather) → Create new bot |
| `ADMIN_TELEGRAM_ID` | `987654321` | [@userinfobot](https://t.me/userinfobot) |
| `TELEGRAM_CHANNEL_ID` | `-1001234567890` | Право-клик на канал → Copy channel ID |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | [console.anthropic.com](https://console.anthropic.com) → API keys |
| `YOUTUBE_API_KEY` | `AIzaSy...` | [Google Cloud Console](https://console.cloud.google.com) → YouTube Data API v3 |

### Для модерации

| Переменная | Пример |
|---|---|
| `EDITOR_TELEGRAM_IDS` | `123456789,987654321` (через запятую) |

### Для транскриптов (обязательно для VPS)

YouTube блокирует запросы субтитров с дата-центровых IP. Нужен резидентный прокси:

| Переменная | Пример | Источник |
|---|---|---|
| `WEBSHARE_PROXY_USERNAME` | `ws123456` | [webshare.io](https://www.webshare.io) → Residential proxy → Credentials |
| `WEBSHARE_PROXY_PASSWORD` | `pass123` | (НИКОГДА не коммитай в код!) |

### Планировщик

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `SCRAPE_INTERVAL_HOURS` | 12 | Как часто искать новый контент |
| `MAX_POSTS_PER_RUN` | 5 | Максимум видео за один скан |
| `MIN_VIEWS_THRESHOLD` | 30000 | Минимум просмотров для кандидата |
| `SCRAPE_RECENT_DAYS` | 30 | Только видео за последние N дней |
| `TOPIC_DEDUP_DAYS` | 45 | Не повторять темы за последние N дней |
| `MIN_SOURCE_CHARS` | 220 | Минимум контента для рерайта (skip ads/shorts) |

---

## 🤖 Команды бота

Отправь эти команды боту в Telegram:

| Команда | Доступ | Действие |
|---|---|---|
| `/queue` | Admin, Editor | Посты на проверке (по 8 штук) |
| `/queue finance` | Admin, Editor | Только финансовые посты |
| `/queue ai` | Admin, Editor | Только AI/кодинг посты |
| `/stats` | Admin, Editor | Статистика: токены, посты, затраты |
| `/bloggers` | Admin, Editor | Список отслеживаемых блогеров |
| `/add_blogger` | Admin | Добавить новый YouTube-канал |
| `/remove_blogger` | Admin | Удалить блогера |
| `/scrape_now` | Admin | Запустить парсинг вручную (выбор темы) |
| `/help` | All | Справка по командам |

### Кнопки на карточке поста

Когда пришла карточка на проверку:

- ✅ **Одобрить** — пост идёт в очередь публикации
- ❌ **Отклонить** — пост удаляется (не будет больше показываться)
- ✏️ **Редактировать** — отправь новый текст, карточка заново на проверку

---

## 📖 Примеры использования

### Добавить нового блогера

```
/add_blogger
```

Бот запросит URL канала YouTube, например:
```
https://www.youtube.com/c/HumphreyYang
```

### Запустить парсинг конкретной темы

```
/scrape_now
```

Появятся кнопки:
- 💸 **Финансы**
- 🧑‍💻 **AI / Кодинг**
- 🌐 **Все темы**

Выбери — бот запустится в фоне на 10–15 минут.

### Просмотреть статистику

```
/stats
```

Ответ:
```
📊 Статистика

На проверке: 42 поста, 53,200 токенов
✅ Одобрено: 18 постов, 33,953 токенов
❌ Отклонено: 62 поста, 106,364 токенов

💰 Примерные затраты: ~$0.18
```

---

## 🔒 Безопасность

- ✅ **Секреты в `.env`** — все ключи через переменные окружения, не в коде
- ✅ **`.env.example`** в репо — только плейсхолдеры, реальные значения ты добавляешь сам
- ✅ **HTML-экранирование** — динамический текст в сообщениях защищён от инъекций
- ✅ **Чистые логи** — токены и ключи не попадают в журнал
- ✅ **Ролевая модель** — только Admin может менять конфиг, Editor только ревьюирует
- ✅ **Telegram ID авторизация** — только известные пользователи взаимодействуют с ботом

---

## 📦 Структура проекта

```
content_bot/
├── main.py                      # Точка входа
├── config.py                    # Pydantic Settings (все конфиги из .env)
│
├── bot/
│   ├── handlers.py              # Command handlers (/queue, /stats, etc.)
│   ├── telegram_bot.py          # Application factory & polling/webhook
│   └── __init__.py
│
├── scrapers/
│   ├── youtube_scraper.py       # YouTube API, ранжирование, транскрипты
│   └── __init__.py
│
├── processors/
│   ├── content_processor.py     # Claude Haiku → Sonnet рерайт
│   └── __init__.py
│
├── publishers/
│   ├── telegram_publisher.py    # Отправка в Telegram канал
│   ├── threads_publisher.py     # Опционально: Meta Threads
│   └── __init__.py
│
├── scheduler/
│   ├── task_scheduler.py        # APScheduler: скан + рерайт + публикация
│   └── __init__.py
│
├── database/
│   ├── db_manager.py            # Async SQLAlchemy queries
│   ├── models.py                # ORM: Post, Blogger, SystemEvent
│   └── __init__.py
│
├── monitoring/
│   ├── health_monitor.py        # Проверка здоровья, алерты
│   └── __init__.py
│
├── security/
│   ├── auth.py                  # @admin_only & @editor_or_admin decorators
│   └── __init__.py
│
├── utils/
│   ├── logger.py                # Structlog конфигурация
│   ├── helpers.py               # HTML-экранирование, хелперы
│   └── __init__.py
│
├── .env.example                 # Шаблон переменных окружения
├── .gitignore                   # .env, *.db, venv, etc.
├── requirements.txt             # pip dependencies
└── README.md                    # Этот файл
```

---

## 🚢 Деплой на VPS

### Требования

- Ubuntu 22.04+ (или другой Linux)
- Python 3.12+
- Systemd

### Установка на сервер

```bash
# Подключись по SSH
ssh user@your-vps-ip

# Клонируй репозиторий
git clone https://github.com/IDonRumata/content_bot.git /opt/content_bot
cd /opt/content_bot

# Создай виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Установи зависимости
pip install -r requirements.txt

# Создай .env с реальными значениями
cp .env.example .env
nano .env  # Заполни все переменные!

# Тестовый запуск
python main.py  # Должен стартовать без ошибок, Ctrl+C чтобы выйти
```

### Systemd сервис

Создай файл `/etc/systemd/system/content_bot.service`:

```ini
[Unit]
Description=Content Bot — Telegram Content Automation
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/opt/content_bot
Environment="PATH=/opt/content_bot/venv/bin"
ExecStart=/opt/content_bot/venv/bin/python main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=content_bot

[Install]
WantedBy=multi-user.target
```

Стартуй и включи в автозагрузку:

```bash
sudo systemctl daemon-reload
sudo systemctl enable content_bot
sudo systemctl start content_bot

# Проверь статус
sudo systemctl status content_bot

# Смотри логи
sudo journalctl -u content_bot -f
```

---

## 🔧 Troubleshooting

### Ошибка: `429 Too Many Requests` при парсинге транскриптов

**Причина:** YouTube блокирует запросы с дата-центровых IP.

**Решение:** Добавь резидентный прокси в `.env`:
```
WEBSHARE_PROXY_USERNAME=your_webshare_username
WEBSHARE_PROXY_PASSWORD=your_webshare_password
```

Затем перезапусти бота:
```bash
sudo systemctl restart content_bot
```

### Бот не отвечает на команды

**Проверить:**
1. Правильный ли `TELEGRAM_BOT_TOKEN` в `.env`?
2. Правильный ли `ADMIN_TELEGRAM_ID`?
3. Логирует ли бот? `sudo journalctl -u content_bot`

### Посты не публикуются

**Проверить:**
1. Правильный ли `TELEGRAM_CHANNEL_ID`? (должен быть отрицательный: `-100...`)
2. Бот добавлен в канал как администратор?
3. В `/queue` видны посты на проверке? Одобри их кнопкой ✅

---

## 📊 Контент-домены

### 💸 Финансы

Блогеры (примеры):
- Humphrey Yang — аналитический стиль, реальные цифры
- Vivian Tu — дерзкий тон, практичные советы
- Graham Stephan — детальный анализ инвестиций
- Andrei Jikh — визуальный подход, пассивный доход

**Рерайт:** адаптация под реалии СНГ (ИИС, Мосбиржа, Тинькофф), локализация валют, сохранение авторского голоса.

### 🧑‍💻 AI & Вайб-кодинг

Блогеры (примеры):
- Fireship — краткий, технический
- Cole Medin — практичные инструменты
- Theo Browne — реакции на новое в frontend/AI

**Рерайт:** сохранение англоязычных названий инструментов (Claude, Cursor, n8n), пояснение терминов, практичный тон без инфоцыганщины.

---

## 📝 Разработка

### Добавить нового блогера в список по умолчанию

Отредактируй `config.py`:

```python
default_bloggers: str = (
    "UCF5TJYJHoEL9LVGSHiDDBlg:Humphrey Yang,"
    "UCJEnQMBLz3EJGl9XRxiCPrw:Vivian Tu (Your Rich BFF),"
    "новый_channel_id:Имя Блогера"  # ← добавь строку
)
```

### Структура промпта рерайта

Рерайт вдохновляется стилем конкретного блогера и следует строгим правилам:

✅ Уникальные заголовки (разные для каждого поста)
✅ Факты только из транскрипта (no hallucinations)
✅ Авторский голос (спокойный vs дерзкий vs энергичный)
✅ Адаптация под аудиторию (СНГ-реалии для финансов)

❌ Шаблонные конструкции ("Когда…, то…")
❌ Выдуманные цифры и кейсы
❌ Обещания гарантированной доходности

---

## 📄 Лицензия

MIT © 2025 IDonRumata

---

## 💬 Поддержка

Если есть вопросы или нашёл баг — открой [Issue](https://github.com/IDonRumata/content_bot/issues) или напиши в Telegram.

---

**Built with ❤️ using Claude AI, YouTube Data API & Telegram Bot API**
