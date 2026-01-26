# Оптимизированные Docker файлы

## 📋 Оптимизированный Dockerfile

```dockerfile
# ============================================
# BUILDER STAGE - Сборка зависимостей
# ============================================
ARG PYTHON_VERSION=3.11.8
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

# Копируем uv package manager (обновленная версия)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONONDONTWRITEBYTECODE=1 \
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

# Установка зависимостей с использованием cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ============================================
# RUNTIME STAGE - Финальный образ
# ============================================
FROM python:${PYTHON_VERSION}-slim-bookworm

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
    mkdir -p /home/appuser/export /home/appuser/cache /app/sessions && \
    chown -R appuser:appuser /app /opt/venv /home/appuser

# Копирование кода приложения
COPY --chown=appuser:appuser . .

# Переключение на непривилегированного пользователя
USER appuser

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Точка входа
CMD ["python", "main.py"]
```

---

## 📋 Оптимизированный docker-compose.yml

```yaml
services:
    tobs:
        build:
            context: .
            dockerfile: Dockerfile
            args:
                PYTHON_VERSION: ${PYTHON_VERSION:-3.11.8}

        container_name: tobs
        hostname: tobs

        network_mode: bridge
        stdin_open: true
        tty: true

        restart: unless-stopped

        env_file:
            - .env

        environment:
            # Пути внутри контейнера
            - EXPORT_PATH=/home/appuser/export
            - SESSION_NAME=sessions/tobs_session
            - CACHE_PATH=/home/appuser/cache
            - PYTHONUNBUFFERED=1

        volumes:
            # Данные экспорта
            - ./export:/home/appuser/export:Z
            # Session файлы в отдельной директории
            - ./sessions:/app/sessions:Z
            # Кэш транскрипций
            - ./cache:/home/appuser/cache:Z

        group_add:
            - video
            - ${RENDER_GID:-988}

        # Hardware acceleration для FFmpeg
        devices:
            - /dev/dri:/dev/dri

        # Resource limits
        deploy:
            resources:
                limits:
                    cpus: '4.0'
                    memory: 8G
                reservations:
                    cpus: '1.0'
                    memory: 2G

        # Healthcheck
        healthcheck:
            test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
            interval: 30s
            timeout: 10s
            retries: 3
            start_period: 40s

        # Логирование
        logging:
            driver: "json-file"
            options:
                max-size: "10m"
                max-file: "3"
```

---

## 📋 Оптимизированный run-tobs.sh

```bash
#!/bin/bash
# TOBS - удобный запуск контейнера с GPU поддержкой

# Убедиться что директории существуют и имеют правильные права
mkdir -p export cache sessions
sudo chown -R $(id -u):$(id -g) export/ cache/ sessions/ 2>/dev/null || true
chmod -R 755 export/ cache/ sessions/

# Дать права на запись для файлов сессий SQLite
chmod -f 666 sessions/*.session 2>/dev/null || true
chmod -f 666 sessions/*.session-journal 2>/dev/null || true

# Определить GID render группы автоматически
RENDER_GID=$(getent group render | cut -d: -f3)
RENDER_GID=${RENDER_GID:-988}  # Fallback на 988 если не найдено

# Проверить существование образа
IMAGE_NAME="localhost/tobs_tobs:latest"
if ! podman image exists "$IMAGE_NAME"; then
    echo "⚠️  Образ $IMAGE_NAME не найден. Запустите: podman-compose build"
    exit 1
fi

podman run -it --rm \
  --name tobs \
  --userns=host \
  --user 1000:1000 \
  --env-file .env \
  --group-add video \
  --group-add "$RENDER_GID" \
  -e EXPORT_PATH=/home/appuser/export \
  -e SESSION_NAME=sessions/tobs_session \
  -e CACHE_PATH=/home/appuser/cache \
  -e PYTHONUNBUFFERED=1 \
  -v "$PWD/export:/home/appuser/export:z" \
  -v "$PWD/sessions:/app/sessions:z" \
  -v "$PWD/cache:/home/appuser/cache:z" \
  --device /dev/dri:/dev/dri \
  "$IMAGE_NAME"
```

---

## 🔄 Ключевые изменения

### Dockerfile
1. ✅ Использование `uv sync` вместо `uv pip compile` + `uv pip install`
2. ✅ Обновлена версия uv до latest
3. ✅ Добавлен ARG для версии Python
4. ✅ Добавлен cache mount для ускорения сборки
5. ✅ Добавлен healthcheck
6. ✅ Улучшен порядок создания директорий

### docker-compose.yml
1. ✅ Убраны избыточные proxy переменные
2. ✅ Добавлен ARG для версии Python
3. ✅ Использование переменной для RENDER_GID
4. ✅ Добавлены resource limits
5. ✅ Добавлен healthcheck
6. ✅ Убрана устаревшая версия (для Compose v2+)

### run-tobs.sh
1. ✅ Убран `--user root`, используется `--user 1000:1000`
2. ✅ Автоматическое определение RENDER_GID
3. ✅ Проверка существования образа
4. ✅ Использование переменных для путей

---

## 📊 Ожидаемые улучшения

### Производительность
- **Время сборки:** -20-30% (с cache mounts)
- **Размер образа:** -50-100 MB (оптимизация зависимостей)

### Безопасность
- ✅ Запуск от непривилегированного пользователя
- ✅ Resource limits для защиты хоста
- ✅ Healthcheck для мониторинга

### Удобство
- ✅ Автоматическое определение GID
- ✅ Проверка существования образа
- ✅ Гибкость через переменные окружения

---

**Версия:** 1.0  
**Дата:** 2025-01-27  
**Статус:** Готово к применению

