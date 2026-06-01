# 🔐 Amnezia VPN — Subscription Management System

Полноценная система продажи подписок на Amnezia VPN с Telegram-ботом и веб-панелью администратора.

## Архитектура

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Telegram Bot   │     │  Web Admin Panel │     │    YooKassa     │
│   (aiogram 3.x)  │     │   (FastAPI)      │     │   (Webhook)     │
└────────┬─────────┘     └────────┬─────────┘     └────────┬────────┘
         │                        │                         │
         └────────────┬───────────┘─────────────────────────┘
                      │
              ┌───────┴────────┐
              │ Services Layer │
              │ • YooKassa     │
              │ • Subscriptions│
              │ • Server Mgr   │
              │ • Promo Codes  │
              │ • Statistics   │
              └───────┬────────┘
                      │
              ┌───────┴────────┐
              │   Database     │
              │ SQLite / PgSQL │
              └───────┬────────┘
                      │
         ┌────────────┼────────────┐
         │            │            │
    ┌────┴────┐  ┌────┴────┐  ┌───┴─────┐
    │🇩🇪 Germany│  │🇳🇱 Nether.│  │🇱🇹 Lithuan│
    │ AWG SSH  │  │ AWG SSH  │  │ AWG SSH │
    └──────────┘  └──────────┘  └─────────┘
```

## 🚀 Быстрый старт

### 1. Установка зависимостей

```bash
cd amnezia_web_panel
pip install -r requirements.txt
```

### 2. Конфигурация

```bash
cp .env.example .env
```

Отредактируйте `.env`:

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота (от @BotFather) |
| `ADMIN_IDS` | Telegram ID админов: `[123456789, 987654321]` |
| `YOOKASSA_SHOP_ID` | ID магазина ЮKassa |
| `YOOKASSA_SECRET_KEY` | Секретный ключ ЮKassa |
| `YOOKASSA_RETURN_URL` | URL возврата после оплаты (ссылка на бота) |
| `DATABASE_URL` | URL базы данных |
| `ADMIN_USERNAME` | Логин веб-панели |
| `ADMIN_PASSWORD` | Пароль веб-панели |
| `WEBHOOK_BASE_URL` | Публичный URL для вебхуков ЮKassa |

### 3. Инициализация серверов

Отредактируйте `seed_servers.py`, указав реальные данные SSH-подключения, затем:

```bash
python seed_servers.py
```

### 4. Запуск

```bash
python main.py
```

Система запустит параллельно:
- 🤖 **Telegram Bot** (polling mode)
- 🌐 **Web Admin Panel** (`http://0.0.0.0:8000`)
- ⏰ **Scheduler** (деактивация подписок, сбор статистики — каждые 5 мин)

## 📁 Структура проекта

```
amnezia_web_panel/
├── main.py                         # Точка входа (бот + веб + scheduler)
├── config.py                       # Конфигурация (Pydantic Settings)
├── seed_servers.py                  # Скрипт инициализации серверов
├── alembic.ini                      # Конфигурация миграций
├── Dockerfile                       # Docker-контейнер
├── docker-compose.yml               # Docker Compose (manager + Redis)
│
├── database/
│   ├── models.py                   # 7 моделей SQLAlchemy 2.0
│   ├── session.py                  # AsyncEngine + session factory
│   ├── crud.py                     # CRUD операции
│   └── migrations/                 # Alembic миграции
│       ├── env.py
│       ├── script.py.mako
│       └── versions/
│
├── services/
│   ├── yookassa_service.py         # Платежи ЮKassa
│   ├── subscription_service.py     # Подписки + provisioning
│   ├── server_manager.py           # SSH + Docker AWG
│   ├── promo_service.py            # Промокоды
│   └── stats_service.py            # Метрики
│
├── bot/
│   ├── handlers/
│   │   ├── start.py                # /start, меню, помощь
│   │   ├── buy_subscription.py     # FSM покупки
│   │   ├── my_subscriptions.py     # Просмотр/скачивание конфигов
│   │   └── admin.py                # /gift, /stats, /create_promo, /broadcast
│   ├── keyboards/inline.py         # Inline-клавиатуры
│   ├── states/subscription_states.py
│   └── middlewares/db_middleware.py
│
└── web/
    ├── main.py                     # FastAPI app + webhook endpoint
    ├── auth.py                     # Сессионная аутентификация
    ├── routers/                    # Роутеры: dashboard, users, subs, servers...
    ├── templates/                  # Jinja2 шаблоны (Tailwind CSS dark)
    └── static/
```

## 🤖 Telegram Bot — команды

### Пользователи
| Команда / Кнопка | Действие |
|---|---|
| `/start` | Главное меню |
| 🛒 Купить VPN | Выбор тарифа → промокод → оплата |
| 📋 Мои подписки | Статус подписки + скачивание конфигов |
| ℹ️ Помощь | Инструкция |

### Администраторы
| Команда | Описание |
|---|---|
| `/stats` | Быстрая статистика |
| `/gift <tg_id> <plan>` | Подарить подписку |
| `/create_promo <code> <discount%> [max_uses]` | Создать промокод |
| `/broadcast <текст>` | Рассылка всем пользователям |

## 🌐 Web Admin Panel

- **Dashboard** — карточки метрик + дневная статистика
- **Пользователи** — таблица зарегистрированных пользователей
- **Подписки** — все подписки с фильтрацией по статусу
- **Серверы** — статус VPN-серверов (online/offline, кол-во клиентов)
- **Промокоды** — создание и управление скидочными кодами
- **Статистика** — детальная аналитика + история платежей

## 💳 Процесс покупки

```
1. Пользователь нажимает «Купить VPN»
2. Выбирает тариф (1/3/12 мес)
3. Вводит промокод (опционально)
4. Перенаправляется на ЮKassa для оплаты
5. После оплаты нажимает «Я оплатил» (или ждёт webhook)
6. Бот создаёт клиентов на ВСЕХ серверах (DE, NL, LT)
7. Бот отправляет .conf файлы для каждого сервера
8. Пользователь импортирует конфиги в Amnezia VPN
```

## 🐳 Docker

```bash
# Сборка и запуск
docker-compose up -d

# Логи
docker-compose logs -f amnezia-manager
```

## 🔧 Миграции (Alembic)

```bash
# Создать миграцию из моделей
alembic revision --autogenerate -m "add_new_field"

# Применить
alembic upgrade head

# Откатить
alembic downgrade -1
```

## 📊 Тарифы

| План | Цена | Дней | Экономия |
|---|---|---|---|
| 1 месяц | 290₽ | 30 | — |
| 3 месяца | 690₽ | 90 | 21% |
| 12 месяцев | 2490₽ | 365 | 28% |

## ⚙️ Требования

- Python 3.12+
- VPN-серверы с Docker + AmneziaWG
- SSH-доступ к VPN-серверам
- ЮKassa API (test или production)
- Публичный URL для webhook (ngrok для разработки)

## 📄 Лицензия

MIT
