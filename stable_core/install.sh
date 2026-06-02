#!/bin/bash
set -e

echo "================================================="
echo "   Smart VPN Panel — One-Command Installer"
echo "================================================="

export DEBIAN_FRONTEND=noninteractive

REPO_URL="${REPO_URL:-https://github.com/blablajka/amnezia_awg_panel.git}"
BOT_TOKEN="${BOT_TOKEN:-dummy_token_to_allow_startup}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"
WEB_PORT="${WEB_PORT:-8000}"

echo "=> Installing dependencies..."
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip nginx curl jq uuid-runtime

echo "=> Cloning repository..."
rm -rf /opt/smart-vpn
git clone "$REPO_URL" /opt/smart-vpn
cd /opt/smart-vpn/stable_core

echo "=> Generating config..."
ADMIN_UUID="${ADMIN_PATH:-/$(cat /proc/sys/kernel/random/uuid)}"
SECRET_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '\n')

cat > .env << ENVEOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=[]
DATABASE_URL=sqlite+aiosqlite:///./vpn_system.db
WEB_HOST=0.0.0.0
WEB_PORT=$WEB_PORT
SECRET_KEY=$SECRET_KEY
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_PASSWORD=$ADMIN_PASSWORD
ADMIN_PATH=$ADMIN_UUID
REFERRAL_BONUS_DAYS=3
SUPPORT_USERNAME=@admin
PRICE_1_MONTH=290
PRICE_3_MONTHS=690
PRICE_12_MONTHS=2490
ENVEOF

echo "=> Installing Python dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "=> Creating systemd service..."
cat > /etc/systemd/system/smart-vpn.service << 'EOF'
[Unit]
Description=Smart VPN Panel
After=network.target

[Service]
WorkingDirectory=/opt/smart-vpn/stable_core
Environment="PATH=/opt/smart-vpn/stable_core/venv/bin"
ExecStart=/opt/smart-vpn/stable_core/venv/bin/uvicorn web.main:create_web_app --factory --host 0.0.0.0 --port 8000
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

echo "=> Starting panel..."
systemctl enable --now smart-vpn 2>/dev/null || (
    echo "WARNING: systemd failed, starting directly..."
    source venv/bin/activate
    nohup uvicorn web.main:create_web_app --factory --host 0.0.0.0 --port 8000 > /dev/null 2>&1 &
)

echo "=> Setting up Nginx..."
cat > /etc/nginx/sites-available/smart-vpn << NGINXEOF
server {
    listen 80;
    server_name _;
    client_max_body_size 100M;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/smart-vpn /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

IP=$(curl -s ifconfig.me 2>/dev/null || echo "YOUR_SERVER_IP")
echo ""
echo "================================================="
echo "  Install Complete!"
echo ""
echo "  Panel:  http://$IP$ADMIN_UUID/dashboard"
echo "  Login:  $ADMIN_USERNAME / $ADMIN_PASSWORD"
echo ""
echo "  SAVE THIS URL — random-path protected."
echo "================================================="
echo ""
echo "  Next:"
echo "  1. Login → Servers → Add Server (IP + SSH)"
echo "  2. Bridges → Create Cascade (Russia → Foreign)"
echo "  3. Set BOT_TOKEN in .env for Telegram bot"
echo "     systemctl restart smart-vpn"
echo "================================================="
