# 🎵 YouTube to MP3 Telegram Bot

A powerful Telegram bot that converts YouTube videos to high-quality MP3 audio files and delivers them directly within Telegram. Features fast downloads, easy usage, and robust YouTube audio extraction with multiple fallback strategies.

## ✨ Features

- **High-Quality Audio**: Downloads and converts YouTube videos to MP3 format at 192kbps
- **Fast Downloads**: Optimized with multiple download strategies for maximum success rate
- **Large File Support**: Handles files larger than 50MB using Pyrogram for enhanced delivery
- **User-Friendly Interface**: Simple inline keyboard with clear instructions
- **Progress Tracking**: Real-time upload progress for large files
- **Error Handling**: Comprehensive error handling with fallback strategies
- **Auto Cleanup**: Automatic cleanup of temporary files after processing

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- FFmpeg installed on your system
- Telegram Bot Token
- Telegram API credentials (for large file support)

### Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd YTTGBot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install FFmpeg**
   
   **On macOS:**
   ```bash
   brew install ffmpeg
   ```
   
   **On Ubuntu/Debian:**
   ```bash
   sudo apt update
   sudo apt install ffmpeg
   ```
   
   **On Windows:**
   - Download from [FFmpeg official website](https://ffmpeg.org/download.html)
   - Add to your system PATH

4. **Set up environment variables**
   
   Create a `.env` file in the project root:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   API_ID=your_telegram_api_id
   API_HASH=your_telegram_api_hash
   ```

### Getting Telegram Credentials

1. **Bot Token:**
   - Message [@BotFather](https://t.me/botfather) on Telegram
   - Create a new bot with `/newbot`
   - Copy the token to your `.env` file

2. **API ID and Hash (for large files):**
   - Go to [my.telegram.org](https://my.telegram.org)
   - Log in with your phone number
   - Go to "API development tools"
   - Create a new application
   - Copy API ID and API Hash to your `.env` file

### Running the Bot

```bash
python bot.py
```

## 📱 Usage

1. **Start the bot**: Send `/start` to your bot
2. **Send a YouTube link**: Simply paste any YouTube video URL in the chat
3. **Wait for processing**: The bot will download and convert the video
4. **Receive your MP3**: The audio file will be sent directly to your chat

### Supported URL Formats

- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `youtube.com/watch?v=VIDEO_ID`

## 🔧 Technical Details

### Download Strategies

The bot uses multiple download strategies to ensure maximum success:

1. **Primary Strategy**: Firefox cookies with TV client
2. **Fallback Strategy**: Basic approach with Android/web client

### File Size Handling

- **Small files (<50MB)**: Sent via python-telegram-bot
- **Large files (>50MB)**: Sent via Pyrogram with progress tracking

### Audio Quality

- **Format**: MP3
- **Bitrate**: 192kbps
- **Codec**: FFmpeg with optimized settings

## 📁 Project Structure

```
YTTGBot/
├── bot.py              # Main bot application
├── requirements.txt    # Python dependencies
├── .env               # Environment variables (create this)
└── README.md          # This file
```

## 🛠️ Dependencies

- `python-telegram-bot`: Telegram Bot API wrapper
- `yt-dlp`: YouTube video downloader
- `pyrogram`: Telegram client library for large files
- `python-dotenv`: Environment variable management
- `TgCrypto`: Fast Telegram crypto library
- `httpx`: HTTP client for requests

## 🔒 Security & Privacy

- All temporary files are automatically deleted after processing
- No user data is stored permanently
- Environment variables keep sensitive credentials secure
- Bot only processes YouTube URLs sent directly to it

## 🐛 Troubleshooting

### Common Issues

1. **"FFmpeg not found"**
   - Ensure FFmpeg is installed and in your system PATH
   - Test with: `ffmpeg -version`

2. **"API_ID/API_HASH not found"**
   - Large files won't work, but small files will still process
   - Add credentials to `.env` file for full functionality

3. **"Download failed"**
   - The bot tries multiple strategies automatically
   - Some videos may be region-restricted or private
   - Check bot logs for detailed error information

4. **"File too large"**
   - Ensure Pyrogram credentials are properly configured
   - Check your Telegram API limits

### Logs

The bot provides detailed logging. Check the console output for:
- Download progress
- Error messages
- File processing status
- Upload progress

## ⚠️ Disclaimer

This bot is for educational and personal use only. Users are responsible for ensuring they have the right to download and convert the content they process. The developers are not responsible for any misuse of this software.

---

**Happy converting! 🎵**
