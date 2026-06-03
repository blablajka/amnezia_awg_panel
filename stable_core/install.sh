#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# Smart VPN Panel — One-Command Installer for Debian 12/Ubuntu
# ═══════════════════════════════════════════════════════════════════════
#
# wget -qO install.sh https://raw.githubusercontent.com/blablajka/amnezia_awg_panel/master/stable_core/install.sh && bash install.sh
#
# Flow:
#   1. System deps
#   2. bivlked/amneziawg-installer (AWG 2.0 kernel module + tools + firewall)
#   3. awg-server (REST API on top of AWG 2.0)
#   4. AS Network List (scanner blocking)
#   5. Zapret (DPI bypass)
#   6. node_exporter (Prometheus system metrics)
#   7. Web Panel (FastAPI + Nginx)
#   8. Dual-interface routing placeholder (awg0 client VPN + awg1 bridge)

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# ── Config ──────────────────────────────────────────────────────────

REPO_URL="${REPO_URL:-https://github.com/blablajka/amnezia_awg_panel.git}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-vpn2026secure}"
WEB_PORT="${WEB_PORT:-8000}"
AWG_LISTEN_PORT="${AWG_LISTEN_PORT:-39743}"
AWG_PRESET="${AWG_PRESET:-default}"
AWG_SUBNET="${AWG_SUBNET:-10.9.9.1/24}"
SKIP_AWG_INSTALLER="${SKIP_AWG_INSTALLER:-0}"
SKIP_ZAPRET="${SKIP_ZAPRET:-0}"
SKIP_AS_LIST="${SKIP_AS_LIST:-0}"
SKIP_NODE_EXPORTER="${SKIP_NODE_EXPORTER:-0}"

INSTALL_DIR="/opt/smart-vpn"
AWG_DIR="/root/awg"

# Cache server IP once
SERVER_IP=""
_get_ip() {
    if [ -z "$SERVER_IP" ]; then
        SERVER_IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || \
                    curl -s --max-time 3 ifconfig.co 2>/dev/null || \
                    curl -s --max-time 3 icanhazip.com 2>/dev/null || \
                    hostname -I 2>/dev/null | awk '{print $1}')
    fi
    echo "$SERVER_IP"
}

trap 'echo "ERROR at line $LINENO: $BASH_COMMAND" >&2' ERR

echo "================================================="
echo " Smart VPN Panel — Installer v2.1"
echo "================================================="
echo " Target:  $(lsb_release -ds 2>/dev/null || echo 'Debian/Ubuntu')"
echo " Kernel:  $(uname -r) | Arch: $(uname -m)"
echo "================================================="

# ═══════════════════════════════════════════════════════════════════════
# Step 1: System Dependencies
# ═══════════════════════════════════════════════════════════════════════

echo ""
echo "=> [1/8] System dependencies..."

apt-get update -qq

# Kernel headers: meta-package, fallback to version-specific
apt-get install -y -qq linux-headers-amd64 2>/dev/null || \
    apt-get install -y -qq linux-headers-"$(uname -r)" 2>/dev/null || true

apt-get install -y -qq \
    git python3 python3-venv python3-pip nginx curl wget jq \
    uuid-runtime ipset iptables iproute2 qrencode \
    build-essential dkms gnupg gawk perl

mkdir -p /usr/share/keyrings
echo "   System packages: OK"

# ═══════════════════════════════════════════════════════════════════════
# Step 2: AmneziaWG 2.0 via bivlked installer (PRIMARY)
# ═══════════════════════════════════════════════════════════════════════

if [ "$SKIP_AWG_INSTALLER" != "1" ]; then
    echo ""
    echo "=> [2/8] AmneziaWG 2.0 via bivlked installer..."

    if lsmod | grep -q amneziawg && [ -f "$AWG_DIR/server_private.key" ]; then
        echo "   AWG already installed. Skip."
    else
        AWG_INSTALLER_URL="https://raw.githubusercontent.com/bivlked/amneziawg-installer/v5.15.2/install_amneziawg.sh"

        cd /tmp
        curl -fsSL "$AWG_INSTALLER_URL" -o install_awg.sh
        chmod +x install_awg.sh

        # bivlked installer CLI reference (full compliance):
        #   --yes              Auto-confirm all prompts (reboots, UFW)
        #   --port=N           AWG UDP listen port
        #   --preset=TYPE      default | mobile
        #   --route-amnezia    Amnezia routing mode (RU sites direct, rest VPN)
        #   --no-tweaks        Skip UFW/Fail2Ban/sysctl (we add our own)
        echo "   Running bivlked installer (preset=$AWG_PRESET port=$AWG_LISTEN_PORT)..."
        bash install_awg.sh \
            --yes \
            --port="$AWG_LISTEN_PORT" \
            --preset="$AWG_PRESET" \
            --route-amnezia \
            --no-tweaks || {
                echo "   ERROR: bivlked installer failed. Check /root/awg/install_amneziawg.log"
                exit 1
            }

        rm -f install_awg.sh
    fi

    # Verify AWG is operational
    if lsmod | grep -q amneziawg; then
        echo "   AWG kernel module: OK"
    else
        echo "   WARNING: AWG module not loaded. Try: modprobe amneziawg"
    fi

    # Read bivlked-generated params for awg-server
    AWG_PRIV_KEY=""
    AWG_S1=""; AWG_S2=""; AWG_S3=""; AWG_S4=""
    AWG_H1=""; AWG_H2=""; AWG_H3=""; AWG_H4=""
    AWG_JC=""; AWG_JMIN=""; AWG_JMAX=""
    AWG_I1=""

    if [ -f "$AWG_DIR/awgsetup_cfg.init" ]; then
        # Safe config reader: allowlist only the keys we need
        while IFS='=' read -r key value; do
            case "$key" in
                AWG_S1)  AWG_S1="${value//\"/}" ;;
                AWG_S2)  AWG_S2="${value//\"/}" ;;
                AWG_S3)  AWG_S3="${value//\"/}" ;;
                AWG_S4)  AWG_S4="${value//\"/}" ;;
                AWG_H1)  AWG_H1="${value//\"/}" ;;
                AWG_H2)  AWG_H2="${value//\"/}" ;;
                AWG_H3)  AWG_H3="${value//\"/}" ;;
                AWG_H4)  AWG_H4="${value//\"/}" ;;
                AWG_JC)  AWG_JC="${value//\"/}" ;;
                AWG_JMIN) AWG_JMIN="${value//\"/}" ;;
                AWG_JMAX) AWG_JMAX="${value//\"/}" ;;
                AWG_I1)  AWG_I1="${value//\"/}" ;;
            esac
        done < "$AWG_DIR/awgsetup_cfg.init"
        echo "   AWG params loaded from bivlked config"
    fi

    # Get server private key (bivlked stores it here)
    if [ -f "$AWG_DIR/server_private.key" ]; then
        AWG_PRIV_KEY=$(cat "$AWG_DIR/server_private.key")
    fi
else
    echo ""
    echo "=> [2/8] AWG installer: SKIPPED"
fi

# ═══════════════════════════════════════════════════════════════════════
# Step 3: Network Hardening (our own, since bivlked used --no-tweaks)
# ═══════════════════════════════════════════════════════════════════════

echo ""
echo "=> [3/8] Network hardening..."

sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true
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

sysctl -p /etc/sysctl.d/99-vpn.conf 2>/dev/null || true

# UFW setup (bivlked skipped it with --no-tweaks)
if command -v ufw >/dev/null 2>&1; then
    ufw --force reset 2>/dev/null || true
    ufw default deny incoming
    ufw default allow outgoing
    ufw limit 22/tcp comment 'SSH rate-limit'
    ufw allow "$AWG_LISTEN_PORT"/udp comment 'AWG VPN'
    ufw allow 80/tcp comment 'Web Panel'
    ufw --force enable 2>/dev/null || true
fi

echo "   Hardening: OK"

# ═══════════════════════════════════════════════════════════════════════
# Step 4: awg-server REST API (on top of bivlked's AWG 2.0)
# ═══════════════════════════════════════════════════════════════════════

echo ""
echo "=> [4/8] awg-server REST API..."

ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
AWG_SERVER_BIN="/usr/local/bin/awg-server"
AWG_SERVER_URL="https://github.com/stealthsurf-vpn/awg-server/releases/latest/download/awg-server-linux-${ARCH}"

if [ ! -f "$AWG_SERVER_BIN" ]; then
    curl -fsSL "$AWG_SERVER_URL" -o "$AWG_SERVER_BIN" 2>/dev/null || {
        echo "   Download failed, building from source..."
        if ! command -v go >/dev/null 2>&1; then
            curl -fsSL "https://go.dev/dl/go1.24.4.linux-${ARCH/x86_64/amd64}.tar.gz" -o /tmp/go.tar.gz
            tar -C /usr/local -xzf /tmp/go.tar.gz
            export PATH="/usr/local/go/bin:$PATH"
        fi
        rm -rf /tmp/awg-server-build
        git clone -q https://github.com/stealthsurf-vpn/awg-server.git /tmp/awg-server-build
        cd /tmp/awg-server-build
        go build -o "$AWG_SERVER_BIN" .
        rm -rf /tmp/awg-server-build
    }
    chmod +x "$AWG_SERVER_BIN"
fi
echo "   awg-server binary: $(ls -lh "$AWG_SERVER_BIN" | awk '{print $5}')"

# API token
AWG_API_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d '\n+/=' | head -c 32)

mkdir -p /data
chmod 700 /data

# Build systemd unit — use bivlked params if available, else awg-server autogen
cat > /etc/systemd/system/awg-server.service << UNITEOF
[Unit]
Description=AmneziaWG REST API Server
After=network.target

[Service]
Type=simple
ExecStart=$AWG_SERVER_BIN
Restart=always
RestartSec=5
Environment=AWG_API_TOKEN=$AWG_API_TOKEN
Environment=AWG_ADDRESS=${AWG_SUBNET}
Environment=AWG_ENDPOINT=$(_get_ip)
Environment=AWG_LISTEN_PORT=$AWG_LISTEN_PORT
Environment=AWG_HTTP_PORT=7777
Environment=AWG_DATA_DIR=/data
Environment=AWG_DNS=1.1.1.1
Environment=AWG_MTU=1420
UNITEOF

# Append bivlked params if we have them
if [ -n "${AWG_JC:-}" ];   then echo "Environment=AWG_JC=$AWG_JC"       >> /etc/systemd/system/awg-server.service; fi
if [ -n "${AWG_JMIN:-}" ]; then echo "Environment=AWG_JMIN=$AWG_JMIN"   >> /etc/systemd/system/awg-server.service; fi
if [ -n "${AWG_JMAX:-}" ]; then echo "Environment=AWG_JMAX=$AWG_JMAX"   >> /etc/systemd/system/awg-server.service; fi
if [ -n "${AWG_S3:-}" ];   then echo "Environment=AWG_S3=$AWG_S3"       >> /etc/systemd/system/awg-server.service; fi
if [ -n "${AWG_S4:-}" ];   then echo "Environment=AWG_S4=$AWG_S4"       >> /etc/systemd/system/awg-server.service; fi
if [ -n "${AWG_I1:-}" ];   then echo "Environment=AWG_I1=$AWG_I1"       >> /etc/systemd/system/awg-server.service; fi

cat >> /etc/systemd/system/awg-server.service << 'UNITEOF'

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl enable --now awg-server 2>/dev/null || {
    echo "   WARNING: awg-server failed to start (kernel module loaded?)"
}
echo "   awg-server: installed"

# ═══════════════════════════════════════════════════════════════════════
# Step 5: AS Network List (scanner blocking)
# ═══════════════════════════════════════════════════════════════════════

if [ "$SKIP_AS_LIST" != "1" ]; then
    echo ""
    echo "=> [5/8] AS Network List (scanner blocking)..."
    wget -qO- https://raw.githubusercontent.com/blablajka/AS_Network_List_for-debian/main/install.sh 2>/dev/null | bash || {
        echo "   WARNING: AS list install failed (non-critical)"
    }
    echo "   AS list: OK"
else
    echo ""
    echo "=> [5/8] AS list: SKIPPED"
fi

# ═══════════════════════════════════════════════════════════════════════
# Step 6: Zapret (DPI bypass)
# ═══════════════════════════════════════════════════════════════════════

if [ "$SKIP_ZAPRET" != "1" ]; then
    echo ""
    echo "=> [6/8] Zapret (DPI bypass)..."
    if [ ! -d /opt/zapret ]; then
        git clone -q --depth=1 https://github.com/bol-van/zapret.git /tmp/zapret 2>/dev/null && {
            cd /tmp/zapret
            echo 5 | ./install_easy.sh 2>/dev/null || true
            cd / && rm -rf /tmp/zapret
        } || echo "   WARNING: Zapret clone failed"
    fi
    echo "   Zapret: $([ -d /opt/zapret ] && echo 'OK' || echo 'SKIPPED')"
else
    echo ""
    echo "=> [6/8] Zapret: SKIPPED"
fi

# ═══════════════════════════════════════════════════════════════════════
# Step 7: Prometheus node_exporter
# ═══════════════════════════════════════════════════════════════════════

if [ "$SKIP_NODE_EXPORTER" != "1" ]; then
    echo ""
    echo "=> [7/8] Prometheus node_exporter..."

    if ! command -v node_exporter >/dev/null 2>&1; then
        NODE_VER="1.8.2"
        NODE_ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
        curl -fsSL \
            "https://github.com/prometheus/node_exporter/releases/download/v${NODE_VER}/node_exporter-${NODE_VER}.linux-${NODE_ARCH}.tar.gz" \
            -o /tmp/node_exporter.tgz
        tar xzf /tmp/node_exporter.tgz -C /tmp
        mv /tmp/node_exporter-*.linux-*/node_exporter /usr/local/bin/
        rm -rf /tmp/node_exporter*

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
    echo "   node_exporter: OK"
else
    echo ""
    echo "=> [7/8] node_exporter: SKIPPED"
fi

# ═══════════════════════════════════════════════════════════════════════
# Step 8: Web Panel
# ═══════════════════════════════════════════════════════════════════════

echo ""
echo "=> [8/8] Web Panel..."

rm -rf "$INSTALL_DIR"
git clone -q "$REPO_URL" "$INSTALL_DIR"
cd "$INSTALL_DIR/stable_core"

# Credentials
ADMIN_PATH_SECURE="/$(cat /proc/sys/kernel/random/uuid | tr -d '-')"
SECRET_KEY_VAL=$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=' | head -c 43)

# Preserve BOT_TOKEN from env
if [ -z "${BOT_TOKEN:-}" ] && [ -f .env ]; then
    BOT_TOKEN=$(grep -oP 'BOT_TOKEN=\K.+' .env 2>/dev/null | head -1 || true)
fi
BOT_TOKEN="${BOT_TOKEN:-dummy_token_to_allow_startup}"

# Create .env
cat > .env << ENVEOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=[]
DATABASE_URL=sqlite+aiosqlite:///./vpn_system.db
WEB_HOST=0.0.0.0
WEB_PORT=$WEB_PORT
SECRET_KEY=$SECRET_KEY_VAL
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_PASSWORD=$ADMIN_PASSWORD
ADMIN_PATH=$ADMIN_PATH_SECURE
REFERRAL_BONUS_DAYS=3
SUPPORT_USERNAME=@admin
PRICE_1_MONTH=290
PRICE_3_MONTHS=690
PRICE_12_MONTHS=2490
ENVEOF

# Python virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt

# Systemd unit
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
    echo "   WARNING: systemd failed, starting directly..."
    source "$INSTALL_DIR/stable_core/venv/bin/activate"
    nohup uvicorn web.main:create_web_app --factory --host 127.0.0.1 --port "$WEB_PORT" > /var/log/smart-vpn.log 2>&1 &
}

# Nginx
cat > /etc/nginx/sites-available/smart-vpn << 'NGINXEOF'
server {
    listen 80;
    server_name _;
    client_max_body_size 100M;
    location /metrics {
        proxy_pass http://127.0.0.1:8000/metrics;
        proxy_set_header Host $host;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/smart-vpn /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx 2>/dev/null || nginx -t && systemctl start nginx

# Prometheus config template
cat > "$INSTALL_DIR/prometheus.yml" << PROMEOF
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

# ═══════════════════════════════════════════════════════════════════════
# Done
# ═══════════════════════════════════════════════════════════════════════

IP=$(_get_ip)
ADMIN_URL="http://${IP}${ADMIN_PATH_SECURE:-/admin}/dashboard"
METRICS_URL="http://${IP}/metrics"

echo ""
echo "================================================="
echo "  Install Complete!"
echo ""
echo "  Panel:   $ADMIN_URL"
echo "  Login:   $ADMIN_USERNAME / $ADMIN_PASSWORD"
echo ""
echo "  Metrics: $METRICS_URL"
echo "  AWG API: http://127.0.0.1:7777 (local)"
echo "  API Key: $AWG_API_TOKEN"
echo ""
echo "  Services:"
echo "    systemctl status smart-vpn"
echo "    systemctl status awg-server"
echo "    systemctl status node_exporter"
echo "    awg show"
echo "================================================="
echo ""
echo "  Next:"
echo "  1. Login → Servers → Add foreign VPS"
echo "  2. BOT_TOKEN in .env → systemctl restart smart-vpn"
echo "  3. docker run -d -p 9090:9090 -v $INSTALL_DIR/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus"
echo ""

# Save credentials
cat > "$INSTALL_DIR/CREDENTIALS.txt" << CREDEOF
Smart VPN Panel
===============
URL:      $ADMIN_URL
Login:    $ADMIN_USERNAME
Password: $ADMIN_PASSWORD
API Key:  $AWG_API_TOKEN
CREDEOF
chmod 600 "$INSTALL_DIR/CREDENTIALS.txt"
echo "  Credentials saved: $INSTALL_DIR/CREDENTIALS.txt"

exit 0
