# Smart VPN Panel — Default Credentials

## Admin Panel

| Field | Default Value |
|-------|---------------|
| **Username** | `admin` |
| **Password** | `vpn2026secure` |

Change via `.env`:
```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=vpn2026secure
```

Or pass during install:
```bash
sudo ADMIN_USERNAME="myadmin" ADMIN_PASSWORD="mystrongpass" bash install.sh
```

## Bot Token

Set in `.env` or pass during install:
```bash
sudo BOT_TOKEN="123456:ABCdef..." bash install.sh
```

## Panel URL

Panel is mounted at a random UUID path for protection:
```
http://YOUR_SERVER_IP/<random-uuid>/dashboard
```

The exact URL is printed at the end of `install.sh` output — **save it**.
