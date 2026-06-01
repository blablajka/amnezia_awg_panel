# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

S-UI is a web panel for managing VPN tunnels built on SagerNet/Sing-Box. It provides multi-protocol support (VLESS, VMess, Trojan, Shadowsocks, Hysteria2, TUIC, etc.), multi-client/inbound configuration, traffic statistics, subscription links, and API access.

Backend: Go 1.25 with Gin framework, GORM/SQLite, and embedded sing-box VPN core.
Frontend: Separate Vue.js project (git submodule at `frontend/`), compiled assets served from `web/html/`.

## Commands

```bash
# Build backend (frontend must be built first into web/html/)
./build.sh
# Or manually:
go build -ldflags "-w -s" -tags "with_quic,with_grpc,with_utls,with_acme,with_gvisor,with_tailscale" -o sui main.go

# Build frontend (submodule), then backend, then run with debug
./runSUI.sh

# Run directly after build (default panel: http://localhost:2095/app/, user: admin/admin)
SUI_DB_FOLDER=db SUI_DEBUG=true ./sui

# CLI management commands
./sui admin -show                     # show admin credentials
./sui admin -username X -password Y   # set admin credentials
./sui admin -reset                    # reset admin to defaults
./sui setting -show                   # show current settings
./sui setting -port 8080 -path /panel/  # change panel port/path
./sui uri                             # show panel URI
./sui migrate                         # migrate from older version

# Linting
go vet ./...

# Docker
docker build -t s-ui .
docker compose up -d
```

### Build tags

Required for full functionality: `with_quic,with_grpc,with_utls,with_acme,with_gvisor,with_tailscale`

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SUI_DEBUG` | `false` | Enable debug mode (verbose logging, GORM debug) |
| `SUI_LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |
| `SUI_DB_FOLDER` | `<binary-dir>/db` | SQLite database directory |
| `SUI_BIN_FOLDER` | `bin` | Binaries directory |

## Architecture

```
main.go                    Entry point — runs app or CLI command
app/app.go                 Orchestrator: Init() → Start() → signal loop
    ├── config/            Env-based config (version/name from embedded files)
    ├── database/          GORM + SQLite. Models in database/model/
    ├── service/           Business logic layer (SettingService, ConfigService, etc.)
    ├── core/              Wraps sing-box instance (Box struct) — starts/stops VPN core
    ├── web/               Gin server on :2095 — admin panel SPA + API routes
    ├── sub/               Gin server on :2096 — subscription endpoints (link/json/clash)
    ├── api/               API handlers (v1 cookie-session, v2 token-based)
    ├── cronjob/           Background jobs: stats, traffic depletion, WAL checkpoint
    ├── network/           Auto-HTTPS listener (ALPN-based protocol detection)
    ├── middleware/        Domain validation middleware
    ├── util/              Link/subscription conversion, base64, JSON utilities
    ├── logger/            Logging wrapper around go-logging
    └── cmd/               CLI subcommands: admin, setting, migrate, uri
```

### Application lifecycle (`app/app.go`)

1. `Init()`: parses log level, opens SQLite, seeds default settings, creates sing-box core, cron, web server, sub server, config service
2. `Start()`: starts cron jobs, web server, sub server, then launches sing-box core
3. Signal handling: `SIGHUP` restarts the app, `SIGTERM` stops everything

### API design (`api/`)

Two API versions mounted on the web server under the configurable `webPath`:

- **API v1** (`/app/api/`): Cookie-session auth. All handlers are `POST /:action` or `GET /:action` dispatched via a switch statement in `apiHandler.go`. Actions map to methods on `ApiService` in `apiService.go`.
- **API v2** (`/app/apiv2/`): Token-based auth. REST-style endpoints for inbounds, outbounds, clients, etc.

### Database (`database/`)

SQLite via GORM. Single global `*gorm.DB` accessed via `database.GetDB()`. Models auto-migrated on startup.

Key models: `Setting` (key-value store), `Inbound`, `Outbound`, `Service`, `Endpoint`, `Tls`, `User`, `Client` (VPN users), `Stats` (traffic), `Tokens` (API tokens), `Changes` (audit log).

Default outbound (`direct` tag) and default admin user (`admin`/`admin`) are seeded on first run.

### Settings (`service/setting.go`)

Settings are stored as key-value pairs in the `settings` table. `SettingService` reads/writes typed getters (`getString`, `getInt`, `getBool`) backed by `defaultValueMap` for fallback defaults. Key settings include: panel/sub ports, paths, domains, TLS cert paths, timezone, traffic retention days, subscription encoding options.

### Core integration (`core/`)

Sing-box is embedded as a library, not a subprocess. `core.Core.Start()` unmarshals the JSON config, creates a `Box` instance, and starts it. Runtime services (inbound/outbound/endpoint managers, router) are extracted via `service.FromContext()`. The `ConfigService` in `service/config.go` generates the full sing-box config from database entities and manages hot-reload.

### Services pattern

Each entity type has a service file in `service/` (e.g., `service/inbounds.go`, `service/client.go`). Services typically have:
- `GetAll/GetAllConfig` — read from DB
- `Save(tx, action, data)` — create/edit/delete, with runtime hot-reload if core is running
- A shared `corePtr` variable (set by `ConfigService`) that allows services to call `corePtr.AddInbound()`, `corePtr.RemoveOutbound()`, etc.

### Subscription (`sub/`)

Separate Gin server on port 2096. Provides `/sub/<token>` endpoints returning VPN config links in various formats: plain links, JSON, Clash config. Conversion logic in `util/genLink.go`, `util/linkToJson.go`, `util/outJson.go`.

## Key conventions

- Error handling: use `common.NewError()` / `common.NewErrorf()` from `util/common/` to create structured errors returned to the API
- Services call `database.GetDB()` for reads, but receive `*gorm.DB` transactions (`tx`) for writes from the API layer
- The project has **no tests** — manual verification via build + run is the current practice
- Frontend is a git submodule; pre-built assets in `web/html/` are committed. Only rebuld frontend when changing the UI
- Default panel at `http://localhost:2095/app/`, subscription at `http://localhost:2096/sub/`
