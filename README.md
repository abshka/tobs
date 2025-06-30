# tobs

A powerful tool for exporting messages and media from Telegram channels directly to Obsidian-compatible markdown files.

## Features

- Export text messages from Telegram channels to markdown files
- Download and organize media files (images, videos, voice messages, documents)
- Smart handling of media groups (albums) - all photos/videos in an album appear in a single note
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
   git clone https://github.com/abshka/tobs.git
   cd tobs
   ```
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Before running the script, you must configure your environment variables in the **.env** file. You can copy .env.example to .env and fill in the required values.

```bash
cp .env.example .env
```

### How to get the necessary credentials:

1. **API ID and API Hash**: Visit https://my.telegram.org, log in and create an application to get these values.

## Usage

Run the script:

```bash
python main.py
```

## How It Works

1. The script connects to Telegram using your API credentials
2. It downloads messages from the specified channel
3. Text contents are formatted as markdown
4. Media files are downloaded and organized by type (images, videos, etc.)
5. Messages with multiple photos/videos (albums) are intelligently grouped into a single note
6. All content is saved to your Obsidian vault with proper markdown linking
7. A cache system prevents re-downloading content on subsequent runs

## License

This project is licensed under CC0 1.0 Universal - see the LICENSE file for details.

## First-Time Run

On first run, you'll need to authenticate with Telegram. Follow the prompts to enter your phone number and the authentication code sent to your Telegram account.
