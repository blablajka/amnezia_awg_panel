FROM python:3.12-slim

# Установка системных зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости первыми для кэширования слоёв
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект
COPY . .

# Порт веб-панели
EXPOSE 8000

# Точка входа
CMD ["python", "main.py"]
