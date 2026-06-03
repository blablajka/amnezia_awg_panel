#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# Smart VPN Panel — One-Command Installer for Debian 12/Ubuntu
# ═══════════════════════════════════════════════════════════════════════
#
# INITIAL:
#   wget -qO install.sh https://raw.githubusercontent.com/blablajka/amnezia_awg_panel/master/stable_core/install.sh && bash install.sh
#
# Handles bivlked installer reboots automatically via systemd oneshot.
# After each reboot, the service resumes from saved phase.

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
SKIP_ZAPRET="${SKIP_ZAPRET:-1}"
SKIP_AS_LIST="${SKIP_AS_LIST:-0}"
SKIP_NODE_EXPORTER="${SKIP_NODE_EXPORTER:-0}"

INSTALL_DIR="/opt/smart-vpn"
AWG_DIR="/root/awg"
PHASE_FILE="$INSTALL_DIR/.install_phase"
AWG_INSTALLER_PATH="$AWG_DIR/install_awg.sh"
AWG_INSTALLER_ARGS="$AWG_DIR/.awg_installer_args"
SELF_PATH="$AWG_DIR/smart-vpn-install.sh"
RESUME_FLAG="${1:-}"

# ANSI colors for readable output
GREEN='\033[1;32m'; CYAN='\033[1;36m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'

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

# ── Phase helpers ───────────────────────────────────────────────────

get_phase() { cat "$PHASE_FILE" 2>/dev/null || echo "0"; }
set_phase() { mkdir -p "$(dirname "$PHASE_FILE")"; echo "$1" > "$PHASE_FILE"; }

# ── Resume entry point ──────────────────────────────────────────────

if [ "$RESUME_FLAG" = "--resume" ]; then
    PHASE=$(get_phase)
    echo "=> Resuming from phase $PHASE..."
    # Jump to the right step
    case "$PHASE" in
        2) ;;
        3) ;;
        4) ;;
        5) ;;
        *) echo "=> Nothing to resume (phase=$PHASE)"; exit 0 ;;
    esac
else
    PHASE="0"
    # First run: save ourselves to persistent location for systemd resume
    mkdir -p "$INSTALL_DIR" "$AWG_DIR"
    if [ ! -f "$SELF_PATH" ] || [ "$(realpath "$0" 2>/dev/null || echo "$0")" != "$SELF_PATH" ]; then
        cp "$0" "$SELF_PATH" 2>/dev/null || true
    fi
fi

# ── Trap for debugging ──────────────────────────────────────────────

trap 'echo "ERROR at line $LINENO: $BASH_COMMAND" >&2' ERR

# ── Detect installed state ──────────────────────────────────────────

_is_awg_installed() { command -v awg >/dev/null 2>&1 && ip link show awg0 >/dev/null 2>&1; }
_is_panel_installed() { systemctl is-active smart-vpn >/dev/null 2>&1; }
_is_awg_server_installed() { systemctl is-active awg-server >/dev/null 2>&1; }

echo "================================================="
echo " Smart VPN Panel — Installer v2.3"
echo " Phase: $PHASE | $(lsb_release -ds 2>/dev/null || echo 'Debian/Ubuntu')"
echo "================================================="

# ── Already installed? Show mini dashboard ──────────────────────────

if _is_awg_installed && _is_panel_installed && _is_awg_server_installed && [ "$PHASE" = "0" ]; then
    IP=$(_get_ip)
    ADMIN_PATH_VAL=$(grep -oP 'ADMIN_PATH=\K.+' "$INSTALL_DIR/stable_core/.env" 2>/dev/null || echo "/admin")
    ADMIN_USER_VAL=$(grep -oP 'ADMIN_USERNAME=\K.+' "$INSTALL_DIR/stable_core/.env" 2>/dev/null || echo "admin")
    AWG_TOKEN_VAL=$(grep -oP 'AWG_API_TOKEN=\K.+' /etc/systemd/system/awg-server.service 2>/dev/null || echo "unknown")
    PEERS=$(awg show 2>/dev/null | grep -c "peer:" || echo "0")

    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║     Smart VPN Panel — Already Installed      ║"
    echo "  ╠══════════════════════════════════════════════╣"
    echo "  ║  Panel:  http://${IP}${ADMIN_PATH_VAL}/dashboard"
    echo "  ║  Login:  $ADMIN_USER_VAL"
    echo "  ║  Peers:  $PEERS"
    echo "  ║  API:    http://127.0.0.1:7777"
    echo "  ╠══════════════════════════════════════════════╣"
    echo "  ║  Services:                                   ║"
    echo "  ║    awg-quick@awg0  $(systemctl is-active awg-quick@awg0 2>/dev/null || echo '-')"
    echo "  ║    awg-server      $(systemctl is-active awg-server 2>/dev/null || echo '-')"
    echo "  ║    smart-vpn       $(systemctl is-active smart-vpn 2>/dev/null || echo '-')"
    echo "  ║    node_exporter   $(systemctl is-active node_exporter 2>/dev/null || echo '-')"
    echo "  ╚══════════════════════════════════════════════╝"
    echo ""
    echo "  Choose action:"
    echo "    [1] Show credentials again"
    echo "    [2] Reinstall everything (preserves clients)"
    echo "    [3] Reinstall everything (FULL WIPE)"
    echo "    [q] Quit"
    echo ""
    read -p "  Choice [1]: " CHOICE
    CHOICE="${CHOICE:-1}"

    case "$CHOICE" in
        1)
            cat "$INSTALL_DIR/CREDENTIALS.txt" 2>/dev/null || echo "No credentials file found"
            echo "  Panel: http://${IP}${ADMIN_PATH_VAL}/dashboard"
            echo "  Login: $ADMIN_USER_VAL"
            exit 0
            ;;
        2)
            echo "  Reinstalling (preserving /root/awg and /data)..."
            rm -rf "$INSTALL_DIR/stable_core/venv"
            ;;
        3)
            echo "  FULL WIPE..."
            systemctl stop smart-vpn awg-server 2>/dev/null || true
            rm -rf "$INSTALL_DIR" /data/clients.json /data/usage.json
            rm -f "$AWG_DIR/setup_state"
            echo "  Cleaned. Re-run installer."
            exit 0
            ;;
        *)
            exit 0
            ;;
    esac
fi

echo ""

# ═══════════════════════════════════════════════════════════════════════
# Phase 0-1: System Deps + bivlked AWG 2.0 Installer
# ═══════════════════════════════════════════════════════════════════════

if [ "$PHASE" -le 1 ]; then

    # ── Step 1: System Dependencies ──────────────────────────────────

    echo ""
    echo "=> [1/8] System dependencies..."

    # Wait for apt lock (bivlked or unattended-upgrades may hold it)
    for i in $(seq 1 30); do
        if fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend 2>/dev/null; then
            echo "   Waiting for apt lock (attempt $i/30)..."
            sleep 5
        else
            break
        fi
    done

    # Kill stale apt processes if still locked
    fuser -k /var/lib/apt/lists/lock 2>/dev/null || true
    fuser -k /var/lib/dpkg/lock-frontend 2>/dev/null || true
    sleep 2

    apt-get update -qq || apt-get update -qq || apt-get update -qq
    apt-get install -y -qq linux-headers-amd64 2>/dev/null || \
        apt-get install -y -qq linux-headers-"$(uname -r)" 2>/dev/null || true
    apt-get install -y -qq \
        git python3 python3-venv python3-pip nginx curl wget jq \
        uuid-runtime ipset iptables iproute2 qrencode \
        build-essential dkms gnupg gawk perl || {
            apt-get install -y -qq --fix-broken
            apt-get install -y -qq \
                git python3 python3-venv python3-pip nginx curl wget jq \
                uuid-runtime ipset iptables iproute2 qrencode \
                build-essential dkms gnupg gawk perl
        }
    mkdir -p /usr/share/keyrings
    echo "   System packages: OK"

    # ── Step 2: AmneziaWG 2.0 via bivlked installer ──────────────────

    if [ "$SKIP_AWG_INSTALLER" != "1" ] && ! _is_awg_installed; then
        echo ""
        echo "=> [2/8] AmneziaWG 2.0 via bivlked installer..."
        echo "   (Server will reboot 2 times — automatic resume after each)"

        AWG_INSTALLER_URL="https://raw.githubusercontent.com/bivlked/amneziawg-installer/v5.15.2/install_amneziawg.sh"

        # Download installer to persistent location
        if [ ! -f "$AWG_INSTALLER_PATH" ]; then
            curl -fsSL "$AWG_INSTALLER_URL" -o "$AWG_INSTALLER_PATH" 2>/dev/null || \
                wget -qO "$AWG_INSTALLER_PATH" "$AWG_INSTALLER_URL"
            chmod +x "$AWG_INSTALLER_PATH"
        fi

        # Save installer args for resume (add --force if previous state exists)
        AWG_EXTRA_ARGS=""
        [ -f "$AWG_DIR/setup_state" ] && AWG_EXTRA_ARGS="--force"
        AWG_FULL_ARGS="--yes --port=$AWG_LISTEN_PORT --preset=$AWG_PRESET --route-amnezia --no-tweaks $AWG_EXTRA_ARGS"
        echo "$AWG_FULL_ARGS" > "$AWG_INSTALLER_ARGS"

        # Create resume service (runs after reboot to continue bivlked installer)
        cat > /etc/systemd/system/smart-vpn-awg.service << SVCEOF
[Unit]
Description=Resume AmneziaWG 2.0 installer after reboot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash $AWG_INSTALLER_PATH --yes --port=$AWG_LISTEN_PORT --preset=$AWG_PRESET --route-amnezia --no-tweaks --force
ExecStartPost=/bin/bash $SELF_PATH --resume
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

        systemctl daemon-reload
        systemctl enable smart-vpn-awg.service 2>/dev/null || true

        # Set phase 2 (after bivlked) and mark resume needed
        set_phase "2"

        # Run bivlked installer NOW (it will reboot)
        echo "   Running bivlked installer..."
        bash "$AWG_INSTALLER_PATH" --yes --port="$AWG_LISTEN_PORT" \
            --preset="$AWG_PRESET" --route-amnezia --no-tweaks $AWG_EXTRA_ARGS 2>&1 | tail -10 || true

        # Check if bivlked actually needs reboot vs. already done
        if _is_awg_installed && [ -f "$AWG_DIR/server_private.key" ]; then
            echo "   AWG 2.0 installed and running."
            systemctl disable smart-vpn-awg.service 2>/dev/null || true
        elif [ -f "$AWG_DIR/setup_state" ]; then
            # bivlked needs reboot to continue — let systemd resume it
            echo ""
            echo "   SERVER REBOOTING — smart-vpn-awg.service will auto-resume."
            echo "   DO NOT RUN ANYTHING MANUALLY."
            reboot
            exit 0
        else
            echo "   ERROR: bivlked installer failed (no state file, no AWG interface)"
            echo "   Check $AWG_DIR/install_amneziawg.log"
            exit 1
        fi
    else
        echo ""
        echo "=> [2/8] AWG already installed. Skip."
    fi

    set_phase "3"
fi

# ═══════════════════════════════════════════════════════════════════════
# Phase 3: Network Hardening
# ═══════════════════════════════════════════════════════════════════════

if [ "$PHASE" -le 3 ]; then
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

    if command -v ufw >/dev/null 2>&1; then
        ufw --force reset 2>/dev/null || true
        ufw default deny incoming
        ufw default allow outgoing
        ufw limit 22/tcp comment 'SSH rate-limit'
        ufw allow "$AWG_LISTEN_PORT"/udp comment 'AWG VPN'
        ufw allow 80/tcp comment 'Web Panel'
        ufw allow 7777/tcp comment 'AWG API (local only)'
        ufw --force enable 2>/dev/null || true
    fi
    echo "   Hardening: OK"
    set_phase "4"
fi

# ═══════════════════════════════════════════════════════════════════════
# Phase 4: awg-server REST API
# ═══════════════════════════════════════════════════════════════════════

if [ "$PHASE" -le 4 ]; then
    echo ""
    echo "=> [4/8] awg-server REST API..."

    ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
    AWG_SERVER_BIN="/usr/local/bin/awg-server"

    if [ ! -f "$AWG_SERVER_BIN" ]; then
        AWG_SERVER_URL="https://github.com/stealthsurf-vpn/awg-server/releases/latest/download/awg-server-linux-${ARCH}"
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
    echo "   awg-server: $(ls -lh "$AWG_SERVER_BIN" | awk '{print $5}')"

    AWG_API_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d '\n+/=' | head -c 32)
    mkdir -p /data && chmod 700 /data

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

    # Read bivlked params if available
    if [ -f "$AWG_DIR/awgsetup_cfg.init" ]; then
        while IFS='=' read -r key value; do
            case "$key" in
                AWG_JC)   echo "Environment=AWG_JC=${value//\"/}"   >> /etc/systemd/system/awg-server.service ;;
                AWG_JMIN) echo "Environment=AWG_JMIN=${value//\"/}" >> /etc/systemd/system/awg-server.service ;;
                AWG_JMAX) echo "Environment=AWG_JMAX=${value//\"/}" >> /etc/systemd/system/awg-server.service ;;
                AWG_S3)   echo "Environment=AWG_S3=${value//\"/}"   >> /etc/systemd/system/awg-server.service ;;
                AWG_S4)   echo "Environment=AWG_S4=${value//\"/}"   >> /etc/systemd/system/awg-server.service ;;
                AWG_I1)   echo "Environment=AWG_I1=${value//\"/}"   >> /etc/systemd/system/awg-server.service ;;
            esac
        done < "$AWG_DIR/awgsetup_cfg.init"
    fi

    cat >> /etc/systemd/system/awg-server.service << 'UNITEOF'
[Install]
WantedBy=multi-user.target
UNITEOF

    systemctl daemon-reload
    systemctl enable --now awg-server 2>/dev/null || true
    echo "   awg-server: installed (API key: ${AWG_API_TOKEN:0:8}...)"
    set_phase "5"
fi

# ═══════════════════════════════════════════════════════════════════════
# Phase 5-7: Security + Monitoring
# ═══════════════════════════════════════════════════════════════════════

if [ "$PHASE" -le 5 ]; then
    if [ "$SKIP_AS_LIST" != "1" ]; then
        echo ""; echo "=> [5/8] AS Network List (scanner blocking)..."
        wget -qO- https://raw.githubusercontent.com/blablajka/AS_Network_List_for-debian/main/install.sh 2>/dev/null | bash || true
        echo "   AS list: OK"
    fi

    if [ "$SKIP_ZAPRET" != "1" ]; then
        echo ""; echo "=> [6/8] Zapret (DPI bypass)..."
        if [ ! -d /opt/zapret ]; then
            # Zapret needs these build deps
            apt-get install -y -qq libnetfilter-queue-dev libnfnetlink-dev libmnl-dev libsystemd-dev 2>/dev/null || true
            git clone -q --depth=1 https://github.com/bol-van/zapret.git /tmp/zapret 2>/dev/null && {
                cd /tmp/zapret; echo 5 | ./install_easy.sh || true; cd /
                rm -rf /tmp/zapret
            }
        fi
        echo "   Zapret: $([ -d /opt/zapret ] && echo 'OK' || echo 'SKIPPED')"
    fi

    if [ "$SKIP_NODE_EXPORTER" != "1" ]; then
        echo ""; echo "=> [7/8] Prometheus node_exporter..."
        if ! command -v node_exporter >/dev/null 2>&1; then
            NODE_VER="1.8.2"
            NODE_ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
            curl -fsSL \
                "https://github.com/prometheus/node_exporter/releases/download/v${NODE_VER}/node_exporter-${NODE_VER}.linux-${NODE_ARCH}.tar.gz" \
                -o /tmp/ne.tgz
            tar xzf /tmp/ne.tgz -C /tmp
            mv /tmp/node_exporter-*.linux-*/node_exporter /usr/local/bin/
            rm -rf /tmp/ne.tgz /tmp/node_exporter-*
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
    fi
    set_phase "8"
fi

# ═══════════════════════════════════════════════════════════════════════
# Phase 8: Web Panel
# ═══════════════════════════════════════════════════════════════════════

if [ "$PHASE" -le 8 ]; then
    echo ""; echo "=> [8/8] Web Panel..."

    # Clone repo to INSTALL_DIR (repo contains stable_core/ inside)
    rm -rf "$INSTALL_DIR"
    git clone -q "$REPO_URL" "$INSTALL_DIR" 2>/dev/null || {
        mkdir -p "$INSTALL_DIR"
        git clone -q "$REPO_URL" "$INSTALL_DIR"
    }
    cd "$INSTALL_DIR/stable_core"

    ADMIN_PATH_SECURE="/$(cat /proc/sys/kernel/random/uuid | tr -d '-')"
    SECRET_KEY_VAL=$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=' | head -c 43)
    BOT_TOKEN_VAL="${BOT_TOKEN:-dummy_token_to_allow_startup}"

    cat > .env << ENVEOF
BOT_TOKEN=$BOT_TOKEN_VAL
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

    python3 -m venv venv
    source venv/bin/activate
    pip install -q -r requirements.txt

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
[Install]
WantedBy=multi-user.target
SVCEEOF

    systemctl daemon-reload
    systemctl enable --now smart-vpn 2>/dev/null || {
        source "$INSTALL_DIR/stable_core/venv/bin/activate"
        nohup uvicorn web.main:create_web_app --factory --host 127.0.0.1 --port "$WEB_PORT" > /var/log/smart-vpn.log 2>&1 &
    }

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
    systemctl restart nginx 2>/dev/null || (nginx -t && systemctl start nginx)

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

    set_phase "done"
fi

# ═══════════════════════════════════════════════════════════════════════
# Done
# ═══════════════════════════════════════════════════════════════════════

# Cleanup resume service
systemctl disable smart-vpn-awg.service 2>/dev/null || true
rm -f /etc/systemd/system/smart-vpn-awg.service
systemctl daemon-reload 2>/dev/null || true
rm -f "$PHASE_FILE"

IP=$(_get_ip)
ADMIN_URL="http://${IP}${ADMIN_PATH_SECURE:-/admin}/dashboard"

echo ""
echo "================================================="
echo -e "  ${GREEN}Install Complete!${NC}"
echo ""
echo -e "  ${CYAN}Panel:${NC}   ${YELLOW}$ADMIN_URL${NC}"
echo -e "  ${CYAN}Login:${NC}   ${GREEN}$ADMIN_USERNAME / $ADMIN_PASSWORD${NC}"
echo -e "  ${CYAN}API Key:${NC} $AWG_API_TOKEN"
echo ""
echo -e "  ${YELLOW}Next:${NC} Add foreign VPS → Create bridge"
echo "================================================="

cat > "$INSTALL_DIR/CREDENTIALS.txt" << CREDEOF
URL:      $ADMIN_URL
Login:    $ADMIN_USERNAME
Password: $ADMIN_PASSWORD
API Key:  $AWG_API_TOKEN
CREDEOF
chmod 600 "$INSTALL_DIR/CREDENTIALS.txt"

exit 0
