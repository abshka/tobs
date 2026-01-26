#!/bin/bash
# TOBS - удобный запуск контейнера с GPU поддержкой

# Убедиться что директории существуют и имеют правильные права
mkdir -p export cache sessions
sudo chown -R $(id -u):$(id -g) export/ cache/ sessions/ 2>/dev/null || true
chmod -R 755 export/ cache/ sessions/

# Дать права на запись для файлов сессий SQLite (только владелец)
# Security: 600 (rw-------) вместо 666 (rw-rw-rw-)
chmod -f 600 sessions/*.session 2>/dev/null || true
chmod -f 600 sessions/*.session-journal 2>/dev/null || true

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