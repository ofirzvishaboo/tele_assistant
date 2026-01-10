# Telegram Event Scheduling Bot

A production-grade Telegram bot that schedules events in Google Calendar, manages Google Drive folders, and tracks events in Google Sheets. Built with `python-telegram-bot`, `LangGraph`, and Google Workspace APIs.

## Features

- 📅 **Schedule Events**: Natural language event scheduling with conflict detection
- 🔍 **Check Calendar**: Query your calendar for upcoming events
- 📂 **Drive Management**: Automatic folder creation per event
- 📸 **File Uploads**: Upload photos, videos, and documents to event folders
- 📊 **Sheet Logging**: Automatic logging to Google Sheets
- 🤖 **AI-Powered**: Uses OpenAI for natural language understanding
- 🔄 **State Management**: Persistent conversation state with PostgreSQL

## Architecture

- **Bot**: `python-telegram-bot v21+` for Telegram interface
- **Workflow**: `LangGraph` state machine for multi-step conversations
- **Tools**: Google Calendar/Drive/Sheets via `google-api-python-client`
- **Database**: PostgreSQL + SQLAlchemy (async) for persistence
- **LLM**: OpenAI GPT-4o for intent parsing and general conversation

## Prerequisites

- Python 3.12+
- PostgreSQL 16+ (or use Docker Compose which includes it)
- Google Cloud Project with Calendar, Drive, and Sheets APIs enabled
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- OpenAI API Key
- `uv` package manager (recommended) or `pip`

## Quick Start

### 1. Clone and Install

```bash
git clone <repository-url>
cd tele_assistant
uv sync
```

### 2. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable APIs:
   - Google Calendar API
   - Google Drive API
   - Google Sheets API
4. Create OAuth 2.0 credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Desktop app" as application type
   - Download the JSON file as `credentials.json`
5. Place `credentials.json` in the project root

### 3. Configuration

Create a `.env` file:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token

# OpenAI
OPENAI_API_KEY=your_openai_api_key

# Google
GOOGLE_CREDENTIALS_PATH=credentials.json
GOOGLE_TOKEN_PATH=token.json
GOOGLE_DRIVE_PARENT_FOLDER_ID=your_drive_folder_id
GOOGLE_SHEET_ID=your_google_sheet_id

# App Config
DRY_RUN=false

# Database (PostgreSQL)
DATABASE_URL=postgresql+asyncpg://botuser:botpass@localhost:5432/tele_assistant
POSTGRES_USER=botuser
POSTGRES_PASSWORD=botpass
POSTGRES_DB=tele_assistant
```

**Getting Google IDs:**
- **Drive Folder ID**: Open the folder in Google Drive, copy the ID from the URL
- **Sheet ID**: Open the Google Sheet, copy the ID from the URL (between `/d/` and `/edit`)

### 4. First Run (OAuth Flow)

On first run, the bot will open a browser for Google OAuth authentication:

```bash
uv run main.py
```

After authentication, `token.json` will be created automatically.

### 5. Database Setup

For local development, you can use Docker Compose which includes PostgreSQL:

```bash
docker-compose up -d postgres
```

Or install PostgreSQL locally and create the database:

```sql
CREATE DATABASE tele_assistant;
CREATE USER botuser WITH PASSWORD 'botpass';
GRANT ALL PRIVILEGES ON DATABASE tele_assistant TO botuser;
```

### 6. Running the Bot

**With Docker Compose (recommended):**

```bash
docker-compose up -d
```

This will start both PostgreSQL and the bot.

**Local development:**

```bash
uv run main.py
```

## Production Deployment

### Docker Deployment

1. **Build the image:**
   ```bash
   docker build -t tele-assistant-bot .
   ```

2. **Run with docker-compose:**
   ```bash
   docker-compose up -d
   ```

3. **View logs:**
   ```bash
   docker-compose logs -f
   ```

4. **Stop:**
   ```bash
   docker-compose down
   ```

### Environment Variables

All configuration is done via environment variables. For production:

- Use a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.)
- Never commit `.env` files
- Use `DRY_RUN=true` for testing
- Set appropriate log levels

### Health Checks

The bot includes built-in health checks:
- Docker healthcheck runs every 30 seconds
- Graceful shutdown on SIGTERM/SIGINT
- Automatic retry logic for API calls

### Monitoring

- Logs are structured and include timestamps, file locations, and log levels
- All errors are logged with full stack traces
- API calls include retry logic with exponential backoff

## Usage

### Commands

- `/start` - Start the bot and see available features
- `/help` - Show help message
- `/template` - Get event template (Hebrew format)
- `/browse` - Browse and select a Drive folder for uploads
- `/cancel` - Reset the current conversation

### Scheduling Events

Send natural language messages like:
- "Schedule a meeting with Team on Jan 5th 10:00-11:00"
- "Book a party for 150 people at Tel Aviv Port tomorrow 18:00-22:00"
- "I want to schedule an event for 28-12-2025 10:00 - 16:00"

The bot will:
1. Parse your request
2. Check for calendar conflicts
3. Ask for confirmation if conflicts exist
4. Create the calendar event
5. Create a Drive folder
6. Log to Google Sheets

### Checking Calendar

Ask questions like:
- "Do I have events tomorrow?"
- "What is on my calendar for 2025-01-06?"

### File Uploads

Send photos, videos, or documents to upload them to the active event's folder. Use `/browse` to select a different folder.

## Development

### Project Structure

```
tele_assistant/
├── src/
│   ├── bot.py              # Main Telegram bot
│   ├── graph.py            # LangGraph workflow
│   ├── config.py           # Configuration management
│   ├── db/
│   │   └── models.py       # Database models
│   └── mcp_server/
│       ├── tools.py        # Google API wrappers
│       └── server.py       # Standalone MCP server
├── main.py                 # Entry point
├── Dockerfile              # Production Docker image
├── docker-compose.yml      # Docker Compose config
└── pyproject.toml          # Dependencies
```

### Key Components

- **`src/bot.py`**: Handles all Telegram interactions, message routing, and state persistence
- **`src/graph.py`**: LangGraph state machine with nodes for parsing, checking, confirming, and committing
- **`src/mcp_server/tools.py`**: Google API client with retry logic and error handling
- **`src/db/models.py`**: SQLAlchemy models for user state persistence

### Testing

Run in dry-run mode to test without creating actual events:

```bash
DRY_RUN=true uv run main.py
```

### Error Handling

- All API calls include retry logic (3 attempts with exponential backoff)
- Global error handler catches unhandled exceptions
- User-friendly error messages
- Comprehensive logging for debugging

## Troubleshooting

### Bot not responding
- Check logs: `docker-compose logs -f` or console output
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Ensure bot is running: `docker-compose ps`

### Google API errors
- Verify `credentials.json` exists and is valid
- Check `token.json` is present (created after first OAuth)
- Ensure APIs are enabled in Google Cloud Console
- Check quota limits in Google Cloud Console

### Database errors
- Ensure PostgreSQL is running: `docker-compose ps postgres`
- Check `DATABASE_URL` in `.env` is correct
- Verify PostgreSQL connection: `docker-compose exec postgres psql -U botuser -d tele_assistant -c "SELECT 1;"`
- Check PostgreSQL logs: `docker-compose logs postgres`

## Security

- Never commit `credentials.json`, `token.json`, or `.env` files
- Use environment variables for all secrets
- Rotate tokens regularly
- Use least-privilege IAM roles in Google Cloud
- Enable audit logging in production

## License

[Your License Here]

## Contributing

[Your Contributing Guidelines Here]
