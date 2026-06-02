# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Smart VPN Panel — web-based VPN management with Telegram bot + subscription payments.

**Stack:**
- **Web**: FastAPI + Jinja2 + Tailwind CSS (admin panel)
- **Bot**: Aiogram 3.x Telegram bot
- **DB**: SQLAlchemy 2.0 (async) + SQLite (dev) / PostgreSQL (prod)
- **VPN Protocols**: AmneziaWG 2.0 (primary), Hysteria2, GOST
- **Payments**: YooKassa (webhook-based)
- **Target OS**: Debian 12 (primary), Ubuntu 22.04/24.04

**Current focus: AmneziaWG 2.0 migration** — AWG 2.0 parameters (dynamic H1-H4 ranges, S3-S4 padding, CPS I1 tags), presets (default/mobile), client expiry, IPv6 dual-stack, PSK support.

## Project Structure

```
stable_core/
├── .env                          # Environment config (BOT_TOKEN, DATABASE_URL, prices)
├── config.py                     # Pydantic Settings class (all config loaded from .env)
├── requirements.txt              # Python dependencies
├── install.sh                    # Production install (git clone + venv + systemd + nginx)
├── database/
│   ├── __init__.py
│   ├── session.py                # SQLAlchemy async engine + session factory (aiosqlite)
│   ├── models.py                 # SQLAlchemy 2.0 models (User, Server, Subscription, etc.)
│   ├── crud.py                   # CRUD helper functions
│   └── migrations/               # Alembic (env.py, versions/)
├── services/
│   ├── __init__.py
│   ├── promo_service.py          # Promo code validation + discount calculation
│   ├── stats_service.py          # Dashboard metrics + daily stats (pure SQL aggregation)
│   ├── server_manager.py         # SSH-based server provisioning, health checks, monitoring
│   ├── server_hardening.py       # UFW, Fail2Ban, sysctl hardening
│   ├── subscription_service.py   # Subscription lifecycle (activate, expire, renew)
│   └── protocols/
│       ├── __init__.py
│       ├── base.py               # BaseProtocolHandler ABC
│       ├── awg.py                # AmneziaWG via HTTP API (awg-server port 7777)
│       ├── hysteria2.py          # Hysteria2 handler
│       └── gost.py               # GOST handler
└── web/
    ├── main.py                   # FastAPI app factory (create_web_app)
    ├── auth.py                   # Session auth (cookies, in-memory dict)
    ├── templates/                # Jinja2 + Tailwind CSS (10 pages)
    │   ├── base.html, login.html, dashboard.html, users.html
    │   ├── subscriptions.html, servers.html, protocols.html
    │   ├── bridges.html, promo_codes.html, stats.html
    ├── routers/                  # Route handlers for each page
    │   ├── dashboard.py, users.py, subscriptions.py, servers.py
    │   ├── protocols.py, bridges.py, promo_codes.py, stats.py
    └── static/css/custom.css
```

## Commands

```bash
# Install dependencies
cd stable_core && pip install -r requirements.txt

# Run dev server
cd stable_core && uvicorn web.main:create_web_app --factory --host 0.0.0.0 --port 8000 --reload

# DB migrations
cd stable_core && alembic upgrade head

# Production install (as root on clean Debian 12)
bash stable_core/install.sh
```

## Database Models

| Model | Table | Key Fields |
|-------|-------|------------|
| User | users | telegram_id, username, is_admin, referrer_id |
| Server | servers | name, host, port, ssh_user, protocol, country_code |
| Subscription | subscriptions | user_id, plan, status, starts_at, expires_at |
| UserServer | user_servers | user_id, server_id, subscription_id, client_name, config_data (AWG .conf) |
| Payment | payments | user_id, subscription_id, yookassa_payment_id, amount, status |
| PromoCode | promo_codes | code, discount_percent, discount_amount, max_uses, valid_until |
| Bridge | bridges | server_from_id, server_to_id, protocol, config_data |
| DailyStat | daily_stats | date, new_users, new_subscriptions, revenue, active_subscriptions |

### Key Relationships
- User → Subscription (1:many, delete-orphan)
- User → UserServer (1:many) — access to specific servers
- Subscription → UserServer (1:many) — configs tied to subscription period
- Server → UserServer (1:many)
- Server → Bridge (as server_from / server_to)

## Admin Panel Routes

All routes under `settings.ADMIN_PATH` (default: `/admin`):

| Route | Page |
|-------|------|
| `/login` | Login form |
| `/dashboard` | Dashboard with metrics |
| `/users` | User management |
| `/subscriptions` | Subscription management |
| `/servers` | Server management |
| `/protocols` | Protocol configuration |
| `/bridges` | Bridge/tunnel management |
| `/promo_codes` | Promo code management |
| `/stats` | Statistics |

## Code Ownership (What Works)

✅ **Core (user-maintained, don't touch without asking):**
- `database/` — DB architecture + CRUD (aiosqlite, async, thread-safe)
- `web/routers/` + `web/main.py` — route handling, session auth, HTML/JSON responses
- `install.sh` — Debian deployment (venv, systemd, nginx)
- `services/stats_service.py` — statistics aggregation (pure SQL)

## Known Bugs (Need Fixing)

### 1. main.py logger conflict (line 14-36)
```python
from loguru import logger  # line 14 — loguru imported
# ...
logger = logging.getLogger(__name__)  # line 36 — immediately overwritten by stdlib logging
```
Remove loguru import (line 14-21), keep only `logging.getLogger(__name__)`.

### 2. Server model missing AWG 2.0 params
`database/models.py` Server model has no fields for AWG 2.0 obfuscation:
- Missing: `jc`, `jmin`, `jmax`, `s1`, `s2`, `s3`, `s4`, `h1`, `h2`, `h3`, `h4`, `i1`, `preset`
- These need to be stored per-server so configs can be generated correctly.
- Use a JSON column `awg_params` to store them flexibly, OR add individual columns.
- **Recommendation**: Add `awg_params` JSON column + `awg_preset` varchar column.

### 3. server_manager.py deploys AWG 1.x
`_deploy_awg_server_sync()` uses only `AWG_JC`, `AWG_JMIN`, `AWG_JMAX` env vars.
Needs full AWG 2.0 deployment with:
- All 11 AWG 2.0 parameters from Server model
- `awg syncconf` instead of systemctl restart (hot-reload)
- Kernel module from bivlked/amneziawg-installer (supports AWG 2.0)

### 4. awg.py protocol handler uses old API assumptions
- Port 7777 — verify for AWG 2.0
- `/api/clients` endpoints — may need update
- No AWG 2.0 params in config generation

## AWG 2.0 Reference (from bivlked/amneziawg-installer ADVANCED.md)

### Obfuscation Parameters (11 mandatory + 1 optional)

| Param | Range | Description |
|-------|-------|-------------|
| `Jc` | 1-128 (2.0: 0-10) | Junk packet count |
| `Jmin` | 0-1280 | Min junk size (bytes) |
| `Jmax` | 0-1280 (≥ Jmin) | Max junk size (bytes) |
| `S1` | 15-150 | Init message padding |
| `S2` | 15-150 | Response padding — **S1+56 ≠ S2** mandatory |
| `S3` | 8-55 | Cookie padding (AWG 2.0 only) |
| `S4` | 4-27 (max 32) | Data padding (AWG 2.0 only) |
| `H1`-`H4` | uint32 ranges | **Must not overlap**. Safe ≤ 2147483647 for Windows client |
| `I1` | CPS tag string | Optional. Format: `<r N>`, `<b hex>`, `<t>`. Omit = AWG 1.0 fallback |

### Presets

| Preset | Jc | Jmin | Jmax | Use case |
|--------|-----|------|------|----------|
| `default` | 3-6 random | 40-89 | Jmin+50..250 | Home/static networks |
| `mobile` | 3 fixed | 30-50 | Jmin+20..80 | Mobile operators (Tele2, Yota, Megafon) |

### CPS Tag Language
```
<b hex>    — static bytes, e.g. <b 0xc30000000108>
<r N>      — N random bytes
<rd N>     — N random digits
<rc N>     — N random ASCII chars
<t>        — Unix timestamp (4 bytes)
```

### Per-Carrier Configs (community-tested, May 2026)

| Carrier | Working Config |
|---------|---------------|
| Tele2 (Krasnoyarsk) | preset=mobile, I1=`<r 48>` |
| MTS (Primorye) | Jc=3, I1=`<r 48>` |
| Tele2/Megafon (Kemerovo) | QUIC mimicry I1=`<b 0xc30000000108><r 8><b 0x08><r 8><b 0x0045dc><t><r 16>` |
| Megafon (regions) | No I1 (remove line = AWG 1.0 fallback) |
| Yota, Tattelecom | `--preset=mobile` |
| Beeline | `--preset=default` |

### Sample awg0.conf (AWG 2.0 server)

```ini
[Interface]
PrivateKey = [KEY]
Address = 10.9.9.1/24
ListenPort = 39743
Jc = 6
Jmin = 55
Jmax = 205
S1 = 72
S2 = 56
S3 = 32
S4 = 16
H1 = 234567-345678
H2 = 3456789-4567890
H3 = 56789012-67890123
H4 = 456789012-567890123
I1 = <r 128>

[Peer]
#_Name = client_name
PublicKey = [CLIENT_PUBLIC_KEY]
AllowedIPs = 10.9.9.2/32
```

### Features to Implement

1. **Client expiry**: Formats 1h/12h/1d/7d/30d/4w, cron every 5min, timestamp in `/root/awg/expiry/<name>`
2. **IPv6 dual-stack**: ULA `fddd:2c4:2c4:2c4::/64`, server=::1, clients=::2,::3... Full-tunnel mode when active
3. **syncconf**: `awg syncconf awg0` for hot-reload (no service restart)
4. **PSK**: `awg genpsk` — PresharedKey per client for Shadowrocket/iOS
5. **vpn:// URI**: zlib-compressed Base64 for Amnezia VPN app import (Perl `Compress::Zlib`)
6. **QR codes**: `<name>.png` (conf QR) + `<name>.vpnuri.png` (vpn:// URI QR) via `qrencode`
