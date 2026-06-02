# Smart VPN Panel

## One-command install

```bash
wget -O install.sh https://raw.githubusercontent.com/blablajka/amnezia_awg_panel/master/stable_core/install.sh && chmod +x install.sh && sudo BOT_TOKEN="твой_токен" bash install.sh
```

## Login / Password

```
URL:    http://ТВОЙ_IP_СЕРВЕРА/<uuid>/dashboard
Login:  admin
Pass:   vpn2026secure
```

URL с UUID печатается в конце установки — сохрани его.

## Bot Token

Передаётся при установке через переменную или уже лежит в `.env` на сервере.

## Что дальше

1. Зайди в панель по URL из вывода установки
2. Servers → Add Server (российский VPS)
3. Servers → Add Server (датский VPS)
4. Bridges → Create Cascade (Russia → Denmark)
5. Бот: /start → /plans → купить → получить .conf
