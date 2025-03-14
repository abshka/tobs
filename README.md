# Telegram-Obsidian

A powerful tool for exporting messages and media from Telegram channels directly to Obsidian-compatible markdown files.

## Features

- Export text messages from Telegram channels to markdown files
- Download and organize media files (images, videos, voice messages, documents)
- Support for grouped messages (albums)
- Smart caching to avoid re-downloading content
- Configurable media size limits
- Concurrent downloads for better performance
- Progress visualization with progress bars

## Requirements

- Python 3.7+
- Telegram API credentials (API ID and API Hash)
- Obsidian vault (or any directory for exported files)

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/abshka/Telegram-Obsidian.git
   cd Telegram-Obsidian
   ```
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Before running the script, you must configure your environment variables in the **.env** file:

    # Telegram API credentials
    TELEGRAM_API_ID=your_api_id_here
    TELEGRAM_API_HASH=your_api_hash_here

    # Channel ID (for private channels, use -100 prefix)
    TELEGRAM_CHANNEL_ID=-1001234567890

    # Path to your Obsidian vault or export directory
    OBSIDIAN_PATH=/path/to/your/obsidian/vault

    # Optional settings (defaults shown)
    MAX_VIDEO_SIZE_MB=50
    MAX_CONCURRENT_DOWNLOADS=5
    CACHE_TTL=86400
    SKIP_PROCESSED=True
    BATCH_SIZE=50

### How to get the necessary credentials:

1. **API ID and API Hash**: Visit https://my.telegram.org, log in and create an application to get these values.

2. **Channel ID**:

   - For public channels: use the username (e.g., channelname)
   - For private channels: you need the numeric ID with -100 prefix (e.g., -1001234567890)
   - To get a private channel ID, forward a message from the channel to @userinfobot or use a third-party Telegram client

## Usage

Run the script:

```bash
python main.py
```

### Command-line arguments:

- --debug: Enable debug logging mode
- --skip-cache: Ignore cache and reprocess all messages
- --limit NUMBER: Process only a specific number of recent messages

Example:

```bash
python main.py --debug --limit 100
```

## How It Works

1. The script connects to Telegram using your API credentials
2. It downloads messages from the specified channel
3. Text contents are formatted as markdown
4. Media files are downloaded and organized by type (images, videos, etc.)
5. All content is saved to your Obsidian vault with proper markdown linking
6. A cache system prevents re-downloading content on subsequent runs

## License

This project is licensed under CC0 1.0 Universal - see the LICENSE file for details.

## First-Time Run

On first run, you'll need to authenticate with Telegram. Follow the prompts to enter your phone number and the authentication code sent to your Telegram account.

## TODO
1. Interactive interface? 
2. Make videos visible by default
3. Checkup for dublicate posts's files
4. Better checkup with link?
