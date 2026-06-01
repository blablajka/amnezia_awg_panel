# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Amnezia VPN Subscription Management System — a Telegram bot + web admin panel for selling VPN subscriptions. Users buy plans via the bot, pay through YooKassa, and receive AmneziaWG `.conf` files provisioned across multiple servers (DE, NL, LT) via SSH.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (bot + web + scheduler in one process)
python main.py

# Seed server entries into the DB (edit the script first with real SSH credentials)
python seed_servers.py

# Alembic migrations
alembic revision --autogenerate -m "description"   # create migration from models
alembic upgrade head                                # apply
alembic downgrade -1                                # rollback

# Docker
docker-compose up -d           # start (manager + Redis)
docker-compose logs -f amnezia-manager
```

## Architecture

**`main.py`** launches three coroutines in one asyncio event loop:
1. Telegram Bot (aiogram 3.x, polling mode, `MemoryStorage` for FSM)
2. Web Admin Panel (FastAPI + uvicorn programmatic server)
3. Background scheduler (every 5 min: expire subs, collect daily stats)

### Layers

```
bot/handlers/   ────┐
web/routers/    ────┤  consumes services + crud
web/main.py     ────┘  (also hosts the YooKassa webhook endpoint)
    │
services/            Business logic: YooKassaService, SubscriptionService,
    │                ServerManager, PromoService, StatsService
    │
database/crud.py     Data access (all functions take AsyncSession)
database/models.py   7 SQLAlchemy 2.0 models (Mapped[] style)
database/session.py  AsyncEngine + session factory, auto-create tables on startup
```

### Database (SQLAlchemy 2.0 async, SQLite by default)

7 models with relationships:
- **User** → telegram_id, username, full_name, is_admin
- **Subscription** → plan, status (active/expired/cancelled), starts_at, expires_at. FK to User.
- **Server** → host, SSH credentials, docker_container name, country_code (DE/NL/LT)
- **UserServer** → join table: User + Server + Subscription, stores the client's `.conf` as `config_data`
- **Payment** → yookassa_payment_id, amount, status. FK to User, optional FK to PromoCode and Subscription.
- **PromoCode** → discount_percent or discount_amount, max_uses counter, valid_until
- **DailyStat** → aggregated per-day metrics (revenue, new users, active subs)

`session.py` auto-creates all tables via `Base.metadata.create_all` on startup. SQLite is used by default; swap DATABASE_URL to `postgresql+asyncpg://...` for production. SQLite skips connection pooling.

### Bot middleware

`bot/middlewares/db_middleware.py` — a single aiogram middleware that opens an `async_session_factory()` session, puts it in `data["session"]`, and commits/rolls-back automatically. Every handler receives `session: AsyncSession` as a parameter without a Depends-style annotation — it's injected via middleware data.

### Bot handlers (aiogram)

- `start.py` — `/start`: register/get-or-create user, show welcome menu with inline keyboard. Includes trial activation (7 days), platform-specific setup instructions (Windows/Linux/Android/iOS).
- `buy_subscription.py` — FSM flow: select plan → enter promo (optional) → YooKassa payment link → confirm payment → provision all servers, send `.conf` files
- `my_subscriptions.py` — show active sub details (days left, traffic used, config data), download config files
- `admin.py` — `/stats`, `/gift <tg_id> <plan>`, `/create_promo <code> <discount%> [max_uses]`, `/broadcast <text>`

FSM states are defined in `bot/states/subscription_states.py`: `BuySubscription` with `select_plan`, `enter_promo`, `confirm_payment`.

### Web admin panel (FastAPI)

Mounted at `http://0.0.0.0:8000`. Session-based auth (in-memory dict, no Redis needed for dev). Routes under `/web/routers/`: dashboard, users, subscriptions, servers, promo_codes, stats. All Jinja2 templates use Tailwind CSS (dark theme). The web app also exposes `POST /api/yookassa/webhook` for YooKassa payment notifications — on `PAYMENT_SUCCEEDED` it activates the subscription, provisions all servers, and sends `.conf` files to the user via Telegram.

### Server management

`services/server_manager.py` uses paramiko for SSH. Operations:
- `create_client` — generates WireGuard keypair on the server, allocates next IP from `10.8.1.0/24`, adds peer via `awg set wg0`, returns full `.conf` including AmneziaWG obfuscation params (Jc, Jmin, Jmax, S1, S2, H1-H4)
- `remove_client` — removes peer by public key
- `get_server_status` — returns online/offline and peer count
- `get_client_traffic` — parses `awg show wg0 dump` for rx/tx bytes by IP

All SSH operations run via `loop.run_in_executor(None, ...)` to avoid blocking the asyncio event loop.

### Subscription lifecycle

1. `activate_subscription()` — if user has active sub, extends it; otherwise creates new one
2. `provision_all_servers()` — loops over all active Server rows, calls `ServerManager.create_client()` for each, saves `UserServer` with config_data
3. `deactivate_expired()` — called by the 5-minute scheduler, sets expired subs to `expired` status, also sets related `UserServer.is_active = False`

### YooKassa integration

`services/yookassa_service.py` uses the `yookassa` SDK. Payment flow: `create_payment()` → user redirected to confirmation_url → after payment, YooKassa posts to `/api/yookassa/webhook` → `parse_notification()` → `process_succeeded()` returns metadata → controller activates subscription and provisions servers. IP verification is implemented but commented out (for dev with ngrok).

## Configuration

All config in `.env` (Pydantic `BaseSettings`). Key variables: `BOT_TOKEN`, `ADMIN_IDS`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `DATABASE_URL`, `ADMIN_USERNAME`/`ADMIN_PASSWORD`, `WEBHOOK_BASE_URL`. Prices defined as `PRICE_1_MONTH`, `PRICE_3_MONTHS`, `PRICE_12_MONTHS` in config (a `7_days` trial plan also exists in plan maps but isn't a paid tier). `plan_days`, `plan_names`, and `prices` dicts are available on the `settings` singleton.
