#!/bin/bash
set -e

# ═══════════════════════════════════════════════════════════════════════
# Smart VPN Panel — Universal One-Command Installer for Debian 12/Ubuntu
# ═══════════════════════════════════════════════════════════════════════
#
# wget -qO install.sh https://raw.githubusercontent.com/blablajka/amnezia_awg_panel/master/stable_core/install.sh && bash install.sh
#
# Sets up: AWG 2.0 kernel module, awg-server API, web panel,
#          AS Network List (scanner blocking), Zapret (DPI bypass),
#          Prometheus node_exporter, dual-interface routing.

export DEBIAN_FRONTEND=noninteractive

# ── Configuration (override via env vars) ───────────────────────────

REPO_URL="${REPO_URL:-https://github.com/blablajka/amnezia_awg_panel.git}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-vpn2026secure}"
WEB_PORT="${WEB_PORT:-8000}"
AWG_LISTEN_PORT="${AWG_LISTEN_PORT:-39743}"
AWG_PRESET="${AWG_PRESET:-default}"
SKIP_AWG_INSTALLER="${SKIP_AWG_INSTALLER:-0}"
SKIP_ZAPRET="${SKIP_ZAPRET:-0}"
SKIP_AS_LIST="${SKIP_AS_LIST:-0}"

INSTALL_DIR="/opt/smart-vpn"
AWG_SERVER_DIR="/opt/smart-vpn/awg-server"

echo "================================================="
echo " Smart VPN Panel — Universal Installer v2.0"
echo "================================================="
echo " Target:  $(lsb_release -ds 2>/dev/null || cat /etc/os-release | grep PRETTY | cut -d= -f2)"
echo " Kernel:  $(uname -r)"
echo " Arch:    $(uname -m)"
echo "================================================="

# ── Step 1: System Dependencies ─────────────────────────────────────

echo ""
echo "=> [1/9] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    git python3 python3-venv python3-pip nginx curl wget jq \
    uuid-runtime ipset iptables iproute2 qrencode \
    build-essential dkms linux-headers-$(uname -r) \
    gawk perl ufw fail2ban \
    software-properties-common gpg

echo "   System packages: OK"

# ── Step 2: AmneziaWG 2.0 Kernel Module ─────────────────────────────

if [ "$SKIP_AWG_INSTALLER" != "1" ]; then
    echo ""
    echo "=> [2/9] Installing AmneziaWG 2.0 (kernel module + tools)..."

    if ! lsmod | grep -q amneziawg; then
        # Add Amnezia PPA
        OS_CODENAME=$(lsb_release -cs 2>/dev/null || echo "bookworm")
        # Map Debian codenames to Ubuntu for PPA
        case "$OS_CODENAME" in
            bookworm) PPA_CODENAME="focal" ;;
            trixie)   PPA_CODENAME="noble" ;;
            *)        PPA_CODENAME="$OS_CODENAME" ;;
        esac

        if ! grep -q amnezia /etc/apt/sources.list.d/*.list 2>/dev/null; then
            curl -fsSL https://raw.githubusercontent.com/amnezia-vpn/amnezia.org/master/apt/amnezia.gpg | gpg --dearmor -o /usr/share/keyrings/amnezia.gpg
            echo "deb [signed-by=/usr/share/keyrings/amnezia.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu $PPA_CODENAME main" > /etc/apt/sources.list.d/amnezia.list
            apt-get update -qq
        fi

        apt-get install -y -qq amneziawg-dkms amneziawg-tools 2>/dev/null || {
            # Fallback: use bivlked installer
            echo "   PPA install failed, using bivlked installer..."
            cd /tmp
            curl -fsSL https://raw.githubusercontent.com/bivlked/amneziawg-installer/v5.15.2/install_amneziawg.sh -o install_awg.sh
            bash install_awg.sh --preset="$AWG_PRESET" --port="$AWG_LISTEN_PORT" --yes --route-amnezia --no-tweaks
        }

        # Load module
        modprobe amneziawg 2>/dev/null || true
        echo "amneziawg" > /etc/modules-load.d/amneziawg.conf

        # Verify
        if lsmod | grep -q amneziawg; then
            echo "   AWG kernel module: OK ($(awg --version 2>/dev/null || echo 'installed'))"
        else
            echo "   WARNING: AWG kernel module not loaded. Check dkms status."
        fi
    else
        echo "   AWG kernel module already loaded. Skip."
    fi
else
    echo ""
    echo "=> [2/9] AWG installer: SKIPPED (SKIP_AWG_INSTALLER=1)"
fi

# ── Step 3: IP Forwarding + Sysctl ──────────────────────────────────

echo ""
echo "=> [3/9] Configuring network hardening..."

sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv6.conf.all.forwarding=1 2>/dev/null || true

cat > /etc/sysctl.d/99-vpn.conf << 'SYSCTL'
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.ipv4.tcp_syncookies=1
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.default.rp_filter=1
net.ipv4.conf.all.accept_redirects=0
net.ipv6.conf.all.accept_redirects=0
net.netfilter.nf_conntrack_max=65536
SYSCTL

sysctl -p /etc/sysctl.d/99-vpn.conf
echo "   Network hardening: OK"

# ── Step 4: Clone Repository + Web Panel ────────────────────────────

echo ""
echo "=> [4/9] Installing Web Panel..."

rm -rf "$INSTALL_DIR"
git clone -q "$REPO_URL" "$INSTALL_DIR"
cd "$INSTALL_DIR/stable_core"

# Generate secure credentials
ADMIN_PATH="/$(cat /proc/sys/kernel/random/uuid | tr -d '-')"
SECRET_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=' | head -c 43)

# Use BOT_TOKEN from env, existing .env, or dummy
if [ -z "$BOT_TOKEN" ] && [ -f .env ]; then
    BOT_TOKEN=$(grep -oP 'BOT_TOKEN=\K.+' .env 2>/dev/null | head -1)
fi
BOT_TOKEN="${BOT_TOKEN:-dummy_token_to_allow_startup}"

# Create .env preserving existing if present
if [ ! -f .env ] || grep -q 'BOT_TOKEN=dummy' .env 2>/dev/null; then
    cat > .env << ENVEOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=[]
DATABASE_URL=sqlite+aiosqlite:///./vpn_system.db
WEB_HOST=0.0.0.0
WEB_PORT=$WEB_PORT
SECRET_KEY=$SECRET_KEY
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_PASSWORD=$ADMIN_PASSWORD
ADMIN_PATH=$ADMIN_PATH
REFERRAL_BONUS_DAYS=3
SUPPORT_USERNAME=@admin
PRICE_1_MONTH=290
PRICE_3_MONTHS=690
PRICE_12_MONTHS=2490
ENVEOF
    echo "   .env created"
else
    echo "   .env already exists, preserving settings"
fi

# Python venv
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
echo "   Python dependencies: OK"

# ── Step 5: awg-server (Go binary from GitHub releases) ─────────────

echo ""
echo "=> [5/9] Installing awg-server API..."

mkdir -p "$AWG_SERVER_DIR"
ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
AWG_SERVER_URL="https://github.com/stealthsurf-vpn/awg-server/releases/latest/download/awg-server-linux-${ARCH}"

curl -fsSL "$AWG_SERVER_URL" -o /usr/local/bin/awg-server 2>/dev/null || {
    echo "   WARNING: Could not download awg-server binary from releases."
    echo "   Building from source (requires Go 1.24+)..."
    if ! command -v go >/dev/null 2>&1; then
        curl -fsSL https://go.dev/dl/go1.24.4.linux-${ARCH/x86_64/amd64}.tar.gz -o /tmp/go.tar.gz
        tar -C /usr/local -xzf /tmp/go.tar.gz
        export PATH="/usr/local/go/bin:$PATH"
    fi
    cd "$AWG_SERVER_DIR"
    git clone -q https://github.com/stealthsurf-vpn/awg-server.git .
    go build -o /usr/local/bin/awg-server .
}

chmod +x /usr/local/bin/awg-server
echo "   awg-server binary: $(file /usr/local/bin/awg-server | cut -d: -f2)"

# Generate API token
AWG_API_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d '\n+/=' | head -c 32)

# Create data directory
mkdir -p /data
chmod 700 /data

# Systemd unit for awg-server
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
cat > /etc/systemd/system/awg-server.service << UNITEOF
[Unit]
Description=AmneziaWG REST API Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/awg-server
Restart=always
RestartSec=5
Environment=AWG_API_TOKEN=$AWG_API_TOKEN
Environment=AWG_ADDRESS=10.0.0.1/24
Environment=AWG_ENDPOINT=$SERVER_IP
Environment=AWG_LISTEN_PORT=$AWG_LISTEN_PORT
Environment=AWG_HTTP_PORT=7777
Environment=AWG_DATA_DIR=/data
Environment=AWG_DNS=1.1.1.1
Environment=AWG_MTU=1420

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl enable --now awg-server 2>/dev/null || {
    echo "   WARNING: awg-server service failed to start (no AWG kernel module?)"
}
echo "   awg-server: installed"

# ── Step 6: AS Network List (scanner blocking) ──────────────────────

if [ "$SKIP_AS_LIST" != "1" ]; then
    echo ""
    echo "=> [6/9] Installing AS Network List (block scanners)..."

    wget -qO- https://raw.githubusercontent.com/blablajka/AS_Network_List_for-debian/main/install.sh 2>/dev/null | bash || {
        echo "   WARNING: AS Network List install failed (non-critical)"
    }
    echo "   AS Network List: OK"
else
    echo ""
    echo "=> [6/9] AS Network List: SKIPPED (SKIP_AS_LIST=1)"
fi

# ── Step 7: Zapret (DPI bypass) ─────────────────────────────────────

if [ "$SKIP_ZAPRET" != "1" ]; then
    echo ""
    echo "=> [7/9] Installing Zapret (DPI bypass)..."

    if [ ! -d /opt/zapret ]; then
        cd /tmp
        git clone -q --depth=1 https://github.com/bol-van/zapret.git 2>/dev/null || {
            echo "   WARNING: Could not clone zapret repo"
        }
        if [ -d zapret ]; then
            cd zapret
            echo 5 | ./install_easy.sh 2>/dev/null || {
                echo "   WARNING: Zapret install_easy.sh failed"
            }
            cd /tmp && rm -rf zapret
        fi
    fi
    echo "   Zapret: $([ -d /opt/zapret ] && echo 'OK' || echo 'SKIPPED')"
else
    echo ""
    echo "=> [7/9] Zapret: SKIPPED (SKIP_ZAPRET=1)"
fi

# ── Step 8: Prometheus node_exporter ─────────────────────────────────

echo ""
echo "=> [8/9] Installing Prometheus node_exporter..."

if ! command -v node_exporter >/dev/null 2>&1; then
    NODE_VER="1.8.2"
    NODE_ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
    cd /tmp
    curl -fsSL "https://github.com/prometheus/node_exporter/releases/download/v${NODE_VER}/node_exporter-${NODE_VER}.linux-${NODE_ARCH}.tar.gz" -o node_exporter.tgz
    tar xzf node_exporter.tgz
    mv node_exporter-*.linux-*/node_exporter /usr/local/bin/
    rm -rf node_exporter*

    cat > /etc/systemd/system/node_exporter.service << 'NODEEOF'
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/node_exporter --collector.systemd --collector.processes
Restart=always

[Install]
WantedBy=multi-user.target
NODEEOF

    systemctl daemon-reload
    systemctl enable --now node_exporter 2>/dev/null || true
fi
echo "   node_exporter: $(node_exporter --version 2>&1 | head -1)"

# ── Step 9: Web Panel systemd + Nginx ───────────────────────────────

echo ""
echo "=> [9/9] Starting Web Panel..."

# Systemd service for web panel (port 8000, internal)
cat > /etc/systemd/system/smart-vpn.service << SVCEEOF
[Unit]
Description=Smart VPN Panel
After=network.target awg-server.service
Wants=awg-server.service

[Service]
WorkingDirectory=$INSTALL_DIR/stable_core
Environment="PATH=$INSTALL_DIR/stable_core/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$INSTALL_DIR/stable_core/venv/bin/uvicorn web.main:create_web_app --factory --host 127.0.0.1 --port $WEB_PORT
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
SVCEEOF

systemctl daemon-reload
systemctl enable --now smart-vpn 2>/dev/null || {
    echo "   WARNING: Panel systemd failed, starting directly..."
    source "$INSTALL_DIR/stable_core/venv/bin/activate"
    nohup uvicorn web.main:create_web_app --factory --host 127.0.0.1 --port "$WEB_PORT" > /var/log/smart-vpn.log 2>&1 &
}

# Nginx reverse proxy (port 80 → panel, /metrics → Prometheus)
cat > /etc/nginx/sites-available/smart-vpn << 'NGINXEOF'
server {
    listen 80;
    server_name _;
    client_max_body_size 100M;

    # Prometheus metrics (no auth for scraping)
    location /metrics {
        proxy_pass http://127.0.0.1:8000/metrics;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Web panel
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/smart-vpn /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

# ── Create prometheus.yml template ───────────────────────────────────

cat > /opt/smart-vpn/prometheus.yml << PROMEOF
# Prometheus scrape config — install via:
#   docker run -d --name prometheus -p 9090:9090 -v /opt/smart-vpn/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus

global:
  scrape_interval: 30s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']

  - job_name: 'vpn_panel'
    metrics_path: '/metrics'
    static_configs:
      - targets: ['localhost:80']
PROMEOF

# ── Done ────────────────────────────────────────────────────────────

IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
ADMIN_URL="http://${IP}${ADMIN_PATH:-/admin}/dashboard"
METRICS_URL="http://${IP}/metrics"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Smart VPN Panel — Install Complete!              ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                        ║"
echo "║  Panel:    $ADMIN_URL"
echo "║  Login:    $ADMIN_USERNAME / $ADMIN_PASSWORD"
echo "║                                                        ║"
echo "║  Metrics:  $METRICS_URL"
echo "║  Node Exp: http://${IP}:9100/metrics"
echo "║  AWG API:  http://127.0.0.1:7777 (local only)"
echo "║                                                        ║"
echo "║  API Token: $AWG_API_TOKEN"
echo "║                                                        ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Services:                                              ║"
echo "║    systemctl status smart-vpn     (web panel)           ║"
echo "║    systemctl status awg-server    (AWG API)             ║"
echo "║    systemctl status node_exporter (metrics)             ║"
echo "║    awg show                        (VPN status)         ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Next Steps:                                            ║"
echo "║  1. Login → Servers → Add foreign VPS                   ║"
echo "║  2. Bridges → Create Cascade (Russia → Foreign)         ║"
echo "║  3. Set BOT_TOKEN in .env for Telegram bot              ║"
echo "║  4. Add Prometheus in Grafana for monitoring            ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  SAVE THE API TOKEN — needed for panel-to-server auth."
echo ""

# Save credentials to persistent file
cat > /opt/smart-vpn/CREDENTIALS.txt << CREDEOF
Smart VPN Panel Credentials
============================
URL:      $ADMIN_URL
Login:    $ADMIN_USERNAME
Password: $ADMIN_PASSWORD
API Token: $AWG_API_TOKEN
Metrics:  $METRICS_URL
CREDEOF
chmod 600 /opt/smart-vpn/CREDENTIALS.txt
echo "  Credentials saved: /opt/smart-vpn/CREDENTIALS.txt"

exit 0
