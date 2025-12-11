#!/bin/bash
# TOBS - удобный запуск контейнера с GPU поддержкой

# Убедиться что директории существуют и имеют правильные права
mkdir -p export cache sessions
sudo chown -R $(id -u):$(id -g) export/ cache/ sessions/ 2>/dev/null || true
chmod -R 755 export/ cache/ sessions/

# Дать права на запись для файлов сессий SQLite
chmod -f 666 sessions/*.session 2>/dev/null || true
chmod -f 666 sessions/*.session-journal 2>/dev/null || true

podman run -it --rm \
  --name tobs \
  --userns=host \
  --user root \
  --env-file .env \
  --group-add video \
  --group-add 988 \
  -e EXPORT_PATH=/home/appuser/export \
  -e SESSION_NAME=sessions/tobs_session \
  -e CACHE_PATH=/home/appuser/cache \
  -e PYTHONUNBUFFERED=1 \
  -v $PWD/export:/home/appuser/export:z \
  -v $PWD/sessions:/app/sessions:z \
  -v $PWD/cache:/home/appuser/cache:z \
  --device /dev/dri:/dev/dri \
  localhost/tobs_tobs:latest