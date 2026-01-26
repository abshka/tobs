#!/bin/bash
# Диагностический скрипт для определения причины регрессии

echo "=== TOBS Performance Diagnostics ==="
echo ""

# 1. Проверка сетевого соединения
echo "1️⃣ Network check to Telegram DC1..."
ping -c 5 149.154.167.51 | tail -2

echo ""
echo "2️⃣ Checking Telethon version..."
python3 -c "import telethon; print(f'Telethon: {telethon.__version__}')"

echo ""
echo "3️⃣ Checking system resources..."
echo "Memory: $(free -h | grep Mem | awk '{print $3 "/" $2}')"
echo "CPU Load: $(uptime | awk -F'load average:' '{print $2}')"

echo ""
echo "4️⃣ Checking for active rate limits..."
# Проверка логов на наличие FloodWait
if [ -f "tobs.log" ]; then
    echo "FloodWait errors in last run:"
    grep -c "FloodWait" tobs.log 2>/dev/null || echo "0"
fi

echo ""
echo "=== Run the export again and compare results ==="
