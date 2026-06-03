# AmneziaWG Smart VPN Panel

VPN management panel with Telegram bot, subscription payments, and Prometheus monitoring. Uses **AmneziaWG 2.0** kernel module for DPI-resistant traffic obfuscation.

## Quick Start

```bash
wget -qO install.sh https://raw.githubusercontent.com/blablajka/amnezia_awg_panel/master/stable_core/install.sh && bash install.sh
```

Installs everything on a clean Debian 12 / Ubuntu 22.04+ VPS. Takes ~5 minutes.

After install, open the URL displayed in terminal. Login with `admin` / `vpn2026secure`.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Russian VPS                         │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Clients  │  │ Telegram │  │  Prometheus       │  │
│  │ (AWG)    │  │ Bot      │  │  + Grafana        │  │
│  └────┬─────┘  └────┬─────┘  └────────┬─────────┘  │
│       │             │                 │             │
│  ┌────▼─────────────▼─────────────────▼─────────┐  │
│  │              Web Panel :8000                  │  │
│  │    FastAPI + Jinja2 + Tailwind CSS            │  │
│  │    /admin/dashboard  /admin/metrics           │  │
│  └────────────────────┬─────────────────────────┘  │
│                       │ HTTP :7777                  │
│  ┌────────────────────▼─────────────────────────┐  │
│  │           awg-server (Go)                     │  │
│  │    REST API — client CRUD, traffic stats      │  │
│  │    Multi-interface pool (awg0, awg1, ...)     │  │
│  └────────────────────┬─────────────────────────┘  │
│                       │ awg CLI                     │
│  ┌────────────────────▼─────────────────────────┐  │
│  │    AmneziaWG 2.0 Kernel Module                │  │
│  │    Obfuscation: Jc, Jmin, Jmax, S1-S4,        │  │
│  │    H1-H4, I1 (CPS tags)                       │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Zapret   │  │ AS List  │  │  node_exporter   │  │
│  │DPI bypass│  │Scnr block│  │  system metrics  │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
└──────────────────────┬──────────────────────────────┘
                       │ awg1 bridge
                       ▼
┌─────────────────────────────────────────────────────┐
│                Foreign VPS                           │
│  ┌──────────────────────────────────────────────┐  │
│  │    AmneziaWG 2.0 — tunnel endpoint            │  │
│  │    Split routing: .ru → direct, rest → tunnel │  │
│  └──────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Features

### VPN Management
- **AmneziaWG 2.0** — kernel-level obfuscation bypasses DPI blocking
- **Per-client configs** — automatic `.conf` generation with QR codes
- **Client expiry** — temporary access: 1h, 12h, 1d, 7d, 30d
- **PSK support** — PresharedKey for Shadowrocket/iOS clients
- **IPv6 dual-stack** — ULA addresses in tunnel mode
- **Hot reload** — `awg syncconf` without service restart

### Web Panel (`/admin`)
- **Dashboard** — live metrics, server status cards, revenue chart
- **User management** — Telegram users, subscriptions, payments
- **Server management** — add/remove VPS, deploy AWG, health checks
- **Bridge management** — cascade tunnels (Russia → Foreign)
- **Protocol configuration** — AWG parameters per server
- **Promo codes** — percent/fixed discounts, usage limits
- **Statistics** — daily aggregation, revenue tracking

### Monitoring
- **`/admin/metrics`** — Prometheus endpoint (users, subscriptions, servers, revenue)
- **node_exporter :9100** — CPU, RAM, disk, network (system metrics)
- **Grafana-ready** — `prometheus.yml` template included

### Security
- **AS Network List** — ipset-based blocking of scanner/hosting AS ranges
- **Zapret** — DPI bypass (nfqws packet modification)
- **Network hardening** — BBR congestion control, syncookies, rp_filter
- **UFW + Fail2Ban** — SSH rate limiting, firewall rules
- **Session auth** — httponly cookies, 7-day expiry

### Telegram Bot
- `/start` — user registration with referral tracking
- `/buy` — subscription purchase flow
- `/status` — current subscription and config download
- Admin notifications for new payments

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Web Panel | FastAPI + Jinja2 + Tailwind CSS |
| Bot | Aiogram 3.x |
| API Server | awg-server (Go, static binary) |
| Database | SQLAlchemy 2.0 + SQLite / PostgreSQL |
| VPN | AmneziaWG 2.0 kernel module |
| DPI Bypass | Zapret (nfqws, tpws) |
| Monitoring | Prometheus + node_exporter |

## Configuration

All settings via environment variables in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | dummy | Telegram bot token |
| `ADMIN_IDS` | `[]` | Admin Telegram IDs (JSON array) |
| `DATABASE_URL` | sqlite:///./vpn_system.db | Database connection |
| `ADMIN_USERNAME` | admin | Web panel login |
| `ADMIN_PASSWORD` | vpn2026secure | Web panel password |
| `ADMIN_PATH` | auto-generated | Admin panel URL path |
| `WEB_PORT` | 8000 | Panel listen port |
| `PRICE_1_MONTH` | 290 | 1-month subscription (RUB) |
| `PRICE_3_MONTHS` | 690 | 3-month subscription (RUB) |
| `PRICE_12_MONTHS` | 2490 | 12-month subscription (RUB) |
| `REFERRAL_BONUS_DAYS` | 3 | Bonus days for referrals |

## Installer Environment Variables

Override at install time:

```bash
SKIP_ZAPRET=1 SKIP_AS_LIST=1 AWG_PRESET=mobile bash install.sh
```

| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_AWG_INSTALLER` | 0 | Skip AWG kernel module install |
| `SKIP_ZAPRET` | 0 | Skip Zapret DPI bypass |
| `SKIP_AS_LIST` | 0 | Skip AS Network List |
| `AWG_PRESET` | default | AWG 2.0 preset (default/mobile) |
| `AWG_LISTEN_PORT` | 39743 | AWG UDP listen port |
| `ADMIN_USERNAME` | admin | Panel admin username |
| `ADMIN_PASSWORD` | vpn2026secure | Panel admin password |

## Service Management

```bash
# All services
systemctl status smart-vpn       # Web panel
systemctl status awg-server      # AWG REST API
systemctl status node_exporter   # System metrics

# VPN status
awg show                          # Active interfaces and peers

# View logs
journalctl -u smart-vpn -f       # Panel logs
journalctl -u awg-server -f      # API logs
```

## Development

```bash
git clone https://github.com/blablajka/amnezia_awg_panel.git
cd amnezia_awg_panel/stable_core
pip install -r requirements.txt
uvicorn web.main:create_web_app --factory --reload
```

Requires Python 3.11+. Panel runs on `http://localhost:8000/admin/dashboard`.

## AWG 2.0 Parameters

| Param | Range | Description |
|-------|-------|-------------|
| Jc | 1-128 | Junk packet count |
| Jmin | 0-1280 | Min junk size (bytes) |
| Jmax | 0-1280 | Max junk size (≥ Jmin) |
| S1 | 15-150 | Init message padding |
| S2 | 15-150 | Response padding (S1+56 ≠ S2) |
| S3 | 8-55 | Cookie padding |
| S4 | 4-27 | Data padding |
| H1-H4 | uint32 | Message type ranges (no overlap) |
| I1 | CPS tag | Optional concealment packet |

**Presets:** `default` (home/static networks), `mobile` (Tele2, Yota, Megafon).

## Credits

Built on:
- [amneziawg-installer](https://github.com/bivlked/amneziawg-installer) — AWG 2.0 kernel module deployment
- [awg-server](https://github.com/StealthSurf-VPN/awg-server) — REST API for client management
- [Zapret](https://github.com/bol-van/zapret) — DPI bypass toolkit
- [AS Network List](https://github.com/blablajka/AS_Network_List_for-debian) — Scanner/hosting blocking

## License

MIT
