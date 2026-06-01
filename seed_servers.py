"""
Скрипт инициализации серверов в базе данных.

Запускать один раз перед первым использованием:
    python seed_servers.py

Добавляет серверы Германии, Нидерландов и Литвы.
Измените данные подключения на реальные!
"""
import sys
sys.path.insert(0, ".")

import asyncio
from database.session import init_db, async_session_factory
from database import crud


# ═══════════════════════════════════════════════════════════════════
# Конфигурация серверов — ИЗМЕНИТЕ НА СВОИ ДАННЫЕ!
# ═══════════════════════════════════════════════════════════════════

SERVERS = [
    {
        "name": "Germany",
        "host": "de.example.com",       # IP или домен сервера
        "port": 22,                      # SSH порт
        "ssh_user": "root",
        "ssh_key_path": None,            # Путь к SSH ключу (/root/.ssh/id_rsa)
        "ssh_password": "your_password", # Или пароль (если нет ключа)
        "docker_container": "amnezia-wg-easy",
        "country_code": "DE",
        "wg_config_path": "/etc/amnezia/amneziawg/wg0.conf",
    },
    {
        "name": "Netherlands",
        "host": "nl.example.com",
        "port": 22,
        "ssh_user": "root",
        "ssh_key_path": None,
        "ssh_password": "your_password",
        "docker_container": "amnezia-wg-easy",
        "country_code": "NL",
        "wg_config_path": "/etc/amnezia/amneziawg/wg0.conf",
    },
    {
        "name": "Lithuania",
        "host": "lt.example.com",
        "port": 22,
        "ssh_user": "root",
        "ssh_key_path": None,
        "ssh_password": "your_password",
        "docker_container": "amnezia-wg-easy",
        "country_code": "LT",
        "wg_config_path": "/etc/amnezia/amneziawg/wg0.conf",
    },
]


async def seed() -> None:
    """Добавить серверы в БД."""
    await init_db()

    async with async_session_factory() as session:
        existing = await crud.get_active_servers(session)
        existing_hosts = {s.host for s in existing}

        added = 0
        for srv_data in SERVERS:
            if srv_data["host"] in existing_hosts:
                print(f"⏭  Сервер {srv_data['name']} ({srv_data['host']}) уже есть")
                continue

            await crud.create_server(session, **srv_data)
            print(f"✅ Добавлен: {srv_data['name']} ({srv_data['country_code']}) — {srv_data['host']}")
            added += 1

        await session.commit()

    print(f"\n{'=' * 40}")
    print(f"Добавлено серверов: {added}")
    print(f"Всего серверов: {len(existing) + added}")


if __name__ == "__main__":
    asyncio.run(seed())
