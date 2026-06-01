# Database migrations (Alembic)

Для создания и применения миграций:

```bash
# Создать миграцию (autogenerate из моделей)
alembic revision --autogenerate -m "описание"

# Применить все миграции
alembic upgrade head

# Откатить на одну миграцию назад
alembic downgrade -1

# Показать текущую ревизию
alembic current
```
