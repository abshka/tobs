# TOBS Docker/Podman Quick Start Guide

## 🚀 First Time Setup (5 minutes)

```bash
# 1. Clone and navigate
git clone <repository-url>
cd tobs

# 2. Configure API credentials
cp .env.example .env
nano .env  # Add your API_ID, API_HASH, PHONE_NUMBER

# 3. Find your render group GID (for GPU)
getent group render | cut -d: -f3
# Example output: 988

# 4. Update docker-compose.yml (if using compose)
# Edit line ~60: change "988" to your GID

# 5. Build image
podman-compose build

# 6. Create directories
mkdir -p export cache sessions

# 7. Run!
./run-tobs.sh
```

---

## 📋 Daily Usage

### Option 1: Using run-tobs.sh (Recommended - Clean Output)

```bash
./run-tobs.sh
```

**What it does:**
- ✅ Auto-fixes permissions
- ✅ Enables GPU acceleration
- ✅ Clean interactive interface
- ✅ Files owned by your user

### Option 2: Using Docker Compose (Background Daemon)

```bash
# Start in background
podman-compose up -d

# View logs
podman-compose logs -f

# Stop
podman-compose down
```

---

## 🔧 Common Commands

### Build & Rebuild

```bash
# Clean rebuild (after Dockerfile changes)
podman-compose down -v
podman system prune -af --volumes
podman-compose build --no-cache

# Quick rebuild
podman-compose build
```

### Logs & Debugging

```bash
# View logs (last 50 lines)
podman-compose logs --tail 50

# Follow logs in real-time
podman-compose logs -f

# Check container status
podman-compose ps
```

### File Access

```bash
# View exported files
ls -la export/

# Check permissions
ls -la sessions/

# Fix permissions if needed
sudo chown -R $(id -u):$(id -g) export/ cache/ sessions/
chmod -R 755 export/ cache/ sessions/
chmod 666 sessions/*.session
```

---

## 🐛 Troubleshooting

### Problem: "Permission denied" or "readonly database"

**Solution:**
```bash
chmod 666 sessions/*.session
./run-tobs.sh
```

### Problem: VA-API warnings (GPU not working)

**Check:**
```bash
ls -la /dev/dri/
getent group render | cut -d: -f3
```

**Fix:** Update `run-tobs.sh` or `docker-compose.yml` with correct render GID.

**Note:** If VA-API doesn't work, TOBS will use CPU (slower but functional).

### Problem: Container won't start

**Solution:**
```bash
# Clean everything
podman-compose down -v
podman system prune -af

# Check logs
podman-compose logs

# Rebuild
podman-compose build --no-cache
```

### Problem: "userns and pod cannot be set together"

**Solution:** Use `./run-tobs.sh` instead of `podman-compose up` for GPU support.

---

## 📂 Directory Structure

```
tobs/
├── export/          # Exported conversations (YOUR files)
├── sessions/        # Telegram session database
├── cache/           # Transcription cache
├── .env             # API credentials (create from .env.example)
├── run-tobs.sh      # Easy launcher script
└── docker-compose.yml
```

---

## ⚡ Performance Tips

1. **GPU Acceleration**: Use `./run-tobs.sh` for VA-API support (5-10x faster video processing)
2. **SSD Storage**: Store `export/` on SSD for better I/O performance
3. **RAM**: 8GB+ recommended for large exports
4. **Takeout Mode**: Enable in `.env` for faster exports (`USE_TAKEOUT=True`)

---

## 🔒 Security Notes

- ✅ Session files are stored locally in `sessions/`
- ✅ Never commit `.env` or `sessions/*.session` to git
- ✅ Container runs isolated from host (except mounted volumes)
- ⚠️ `--userns=host` in run script maps host users for file access

---

## 📞 Quick Reference

| Task | Command |
|------|---------|
| **First run** | `./run-tobs.sh` |
| **Daily use** | `./run-tobs.sh` |
| **View logs** | `podman-compose logs -f` |
| **Stop** | Press `Ctrl+C` in terminal |
| **Rebuild** | `podman-compose build` |
| **Clean rebuild** | `podman system prune -af && podman-compose build --no-cache` |
| **Fix permissions** | `chmod 666 sessions/*.session` |
| **Check GPU** | Look for "VA-API" in logs |

---

## 🎯 Next Steps

After successful setup:

1. Run `./run-tobs.sh`
2. Follow interactive prompts
3. Select export targets
4. Configure settings
5. Start export!

Exported files will appear in `export/` directory with your user permissions.
