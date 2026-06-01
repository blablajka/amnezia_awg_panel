#!/bin/bash
set -e

echo "================================================="
echo "   Amnezia AWG Panel - Installer (Ideal)"
echo "================================================="

export DEBIAN_FRONTEND=noninteractive

# 1. Update and install minimal deps
echo "=> Обновление пакетов и установка зависимостей..."
apt-get update -y > /dev/null
apt-get install -yq git python3 python3-venv python3-pip nginx curl jq uuid-runtime > /dev/null

# 2. Clone repository
echo "=> Скачивание репозитория..."
rm -rf /opt/panel
git clone -q "https://github.com/blablajka/amnezia_awg_panel.git" /opt/panel

# 3. Generate Secret Environment
echo "=> Генерация секретного конфигурационного файла..."
cd /opt/panel
ADMIN_UUID=$(cat /proc/sys/kernel/random/uuid)
SECRET_KEY=$(head -c 32 /dev/urandom | base64)

cat > .env << ENVEOF
BOT_TOKEN=dummy_token_to_allow_startup
ADMIN_PATH=/$ADMIN_UUID
SECRET_KEY=$SECRET_KEY
ENVEOF

# 4. Setup Python virtual environment
echo "=> Настройка Python окружения..."
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt

# 5. Create Systemd Service
echo "=> Создание системной службы..."
cat > /etc/systemd/system/amnezia-panel.service << 'EOF'
[Unit]
Description=Amnezia VPN Panel
After=network.target

[Service]
WorkingDirectory=/opt/panel
Environment="PATH=/opt/panel/venv/bin"
ExecStart=/opt/panel/venv/bin/uvicorn web.main:create_web_app --factory --host 127.0.0.1 --port 8000
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now amnezia-panel

# 6. Configure Nginx
echo "=> Настройка Nginx..."
cat > /etc/nginx/sites-available/amnezia << 'EOF'
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

ln -sf /etc/nginx/sites-available/amnezia /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

# 7. Done
IP=$(curl -s ifconfig.me)
echo "================================================="
echo "✅ Установка успешно завершена!"
echo "🔥 ВАЖНО! СОХРАНИТЕ ЭТУ ССЫЛКУ 🔥"
echo "🌐 Секретный адрес панели: http://$IP/$ADMIN_UUID/dashboard"
echo "Никому не передавайте этот URL, иначе панель будет доступна извне."
echo "================================================="
