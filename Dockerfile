# ============================================
# BUILDER STAGE - Сборка зависимостей
# ============================================
FROM python:3.11.8-slim-bookworm AS builder

# Копируем uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Установка build-зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Создание виртуального окружения
RUN uv venv /opt/venv

# Копирование файлов зависимостей (для кэширования слоёв)
COPY pyproject.toml uv.lock ./

# Компиляция и установка зависимостей (отдельные команды для лучшего кэширования)
RUN uv pip compile pyproject.toml -o requirements.txt
RUN uv pip install --no-cache -r requirements.txt

# ============================================
# RUNTIME STAGE - Финальный образ
# ============================================
FROM python:3.11.8-slim-bookworm

# Метаданные образа
LABEL maintainer="TOBS Project" \
    version="1.0.0" \
    description="TOBS - Telegram Chat Export Tool with Media Support and Transcription"

# Установка runtime-зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    intel-media-va-driver \
    libva2 \
    libva-drm2 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Копирование виртуального окружения из builder stage
COPY --from=builder /opt/venv /opt/venv

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Создание пользователя и необходимых директорий
RUN useradd --create-home --uid 1000 appuser && \
    mkdir -p /home/appuser/export /home/appuser/cache && \
    chown -R appuser:appuser /app /opt/venv /home/appuser

# Копирование кода приложения
COPY --chown=appuser:appuser . .

# Переключение на непривилегированного пользователя
USER appuser

# Точка входа
CMD ["python", "main.py"]
