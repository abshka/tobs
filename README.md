# TOBS - Telegram Exporter to Markdown

**TOBS** (Telegram Exporter to Markdown) is a high-performance, enterprise-grade tool for exporting Telegram conversations to Markdown format with advanced optimization features.

## 🚀 Features

### Core Functionality

- **Multi-format Export**: Channels, groups, private chats, and forum topics
- **Media Downloads**: Photos, videos, documents, audio files, stickers
- **Message Threading**: Reply chains and forwarded message tracking
- **Rich Formatting**: Preserves Telegram formatting in Markdown
- **Progress Tracking**: Real-time export progress with detailed statistics

## 🛠️ Installation

> **📘 Docker/Podman Users:** See [DOCKER_QUICKSTART.md](DOCKER_QUICKSTART.md) for a comprehensive quick-start guide with troubleshooting.

### Method 1: Docker/Podman (Recommended)

#### Prerequisites
- Docker or Podman installed
- 4GB RAM minimum (8GB recommended)
- 10GB available disk space
- For GPU acceleration (optional): Intel/AMD GPU with VA-API support

#### Quick Start with Docker/Podman

```bash
# 1. Clone the repository
git clone <repository-url>
cd tobs

# 2. Create required directories
mkdir -p export cache sessions

# 3. Configure Telegram API credentials
cp .env.example .env
# Edit .env with your API_ID, API_HASH, PHONE_NUMBER

# 4. Build the Docker image
docker compose build
# OR for Podman users:
podman-compose build

# 5. Run TOBS
./run-tobs.sh
```

#### What the Script Does

The `run-tobs.sh` script automatically:
- ✅ Creates required directories (`export/`, `cache/`, `sessions/`)
- ✅ Fixes file permissions for host access
- ✅ Enables GPU acceleration (Intel/AMD VA-API)
- ✅ Mounts volumes for persistent data
- ✅ Runs in interactive mode with clean output

#### Manual Docker Run (Advanced)

```bash
podman run -it --rm \
  --name tobs \
  --userns=host \
  --user root \
  --env-file .env \
  --group-add video \
  --group-add 988 \
  -v $PWD/export:/home/appuser/export:z \
  -v $PWD/sessions:/app/sessions:z \
  -v $PWD/cache:/home/appuser/cache:z \
  --device /dev/dri:/dev/dri \
  localhost/tobs_tobs:latest
```

**Note:** Replace `988` with your system's `render` group GID:
```bash
getent group render | cut -d: -f3
```

#### GPU Acceleration (VA-API)

TOBS supports hardware-accelerated video transcoding via VA-API:

- **Supported**: Intel UHD Graphics, AMD GPUs
- **Benefit**: 5-10x faster video processing
- **Auto-configured**: Script enables GPU by default

To check if VA-API is working, look for logs like:
```
✓ VA-API hardware encoder h264_vaapi available
```

If you see warnings about VA-API, the app will fallback to CPU transcoding (slower but still works).

#### Using Docker Compose (Alternative)

If you prefer using Docker Compose instead of the run script:

```bash
# Update docker-compose.yml with your render group GID
# Find GID: getent group render | cut -d: -f3
# Edit docker-compose.yml: change "988" to your GID in group_add section

# Run with compose (shows logs with [tobs] prefix)
podman-compose up

# Or run in background
podman-compose up -d
podman-compose logs -f

# Stop
podman-compose down
```

**Note:** Docker Compose runs in daemon mode and shows logs with `[tobs] |` prefix. Use `./run-tobs.sh` for cleaner interactive output.

#### Accessing Exported Files

All exported data is stored in the `export/` directory on your host machine with your user permissions:

```bash
ls -la export/
# Files belong to your user (ab), not root
```

#### Troubleshooting Docker/Podman

**Problem: Permission denied errors**
```bash
# Fix permissions
sudo chown -R $(id -u):$(id -g) export/ cache/ sessions/
chmod -R 755 export/ cache/ sessions/
chmod 666 sessions/*.session
```

**Problem: VA-API not working**
```bash
# Check GPU device access
ls -la /dev/dri/

# Get render group GID and update run-tobs.sh
getent group render | cut -d: -f3
```

**Problem: "readonly database" error**
```bash
# Fix session file permissions
chmod 666 sessions/*.session
```

---

### Method 2: Local Python Installation

#### Prerequisites

- Python 3.11 or higher
- 4GB RAM minimum (8GB recommended)
- 10GB available disk space

#### Quick Installation

```bash
# Clone the repository
git clone <repository-url>
cd tobs

# Install dependencies (using uv for best performance)
uv sync

# Or use pip
pip install -e .

# Run TOBS
python tobs.py
```

### Telegram API Setup

1. Go to [my.telegram.org](https://my.telegram.org)
2. Create a new application
3. Get your `api_id` and `api_hash`
4. Create a `.env` file:

```env
API_ID=your_api_id
API_HASH=your_api_hash
PHONE_NUMBER=your_phone_number
```

## 🚀 Quick Start

### Basic Usage

```bash
# Interactive mode (recommended)
python tobs.py

# Or directly
python main.py
```

### Configuration

TOBS now uses an interactive configuration system. Simply run the application and follow the on-screen prompts to:

1. **Configure Media Downloads**: Choose which types of media to download (photos, videos, audio, other)
2. **Select Export Targets**: Choose channels, groups, or users to export
3. **Set Export Options**: Configure paths, performance settings, and other options
4. **Start Export**: Begin the export process with real-time progress tracking

### Media Download Configuration

TOBS provides granular control over media downloads:

- **Photos**: Images and profile pictures
- **Videos**: Video files and GIFs
- **Audio**: Voice messages and audio files
- **Other**: Stickers, documents, and other attachments

All media types are enabled by default, but you can customize this in the interactive configuration menu.

## 🚀 Telegram Takeout (Turbo Mode)

TOBS supports **Telegram Takeout**, a feature that allows exporting data at much higher speeds with reduced rate limits.

### Enabling Takeout

1.  Add `USE_TAKEOUT=True` to your `.env` file.
2.  Run TOBS.
3.  You will receive a **Service Notification** from Telegram asking to allow the Takeout request.
4.  **Allow** the request in Telegram.
5.  TOBS will detect the approval (or you may need to restart if it times out) and begin the high-speed export.

### Configuration

```env
USE_TAKEOUT=True              # Enable Takeout mode
TAKEOUT_FALLBACK_DELAY=1.0    # Delay (seconds) if Takeout fails or is disabled
```

### Fallback Behavior

If Takeout fails or is not approved, TOBS will automatically fallback to the standard API with a safe rate-limit delay (default: 1.0s) to prevent FloodWait errors.

## ⚙️ Configuration

### Performance Profiles

- **Conservative**: Stable operation for low-end systems
- **Balanced**: Optimal for most hardware configurations (default)
- **Aggressive**: Maximum performance for high-end systems
- **Custom**: Manual fine-tuning of all parameters

## 📊 Monitoring and Observability

### Real-time Dashboard

TOBS includes a comprehensive monitoring system that tracks:

- System resource utilization (CPU, memory, disk, network)
- Export performance metrics and throughput
- Cache hit rates and compression ratios
- Error rates and retry statistics
- Performance adaptations and optimizations

### Alert Levels

- **INFO**: Informational messages and status updates
- **WARNING**: Performance degradation detected
- **ERROR**: Significant issues requiring attention
- **CRITICAL**: Immediate intervention required

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
