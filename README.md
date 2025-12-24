# Telegram Scheduling Bot

A production-grade Telegram bot that schedules events in Google Calendar, manages Google Drive folders, and tracks events in Google Sheets. It uses LangGraph for workflow orchestration and MCP tool patterns for Google integrations.

## Architecture

- **Bot**: `python-telegram-bot` for the interface.
- **Workflow**: `LangGraph` state machine for scheduling logic.
- **Tools**: Google Calendar/Drive/Sheets via `google-api-python-client`.
- **State**: SQLite + SQLAlchemy.
- **MCP Server**: A standalone MCP server implementation is available in `src/mcp_server/server.py`.

## Setup

### 1. Prerequisites
- Python 3.10+
- Google Cloud Project with Calendar, Drive, and Sheets APIs enabled.
- Telegram Bot Token (from BotFather).
- OpenAI API Key.

### 2. Installation
Initialize the project and install dependencies:
```bash
uv sync
# Or manually
pip install -r requirements.txt
```

### 3. Configuration
1. Copy `env.example` to `.env`:
   ```bash
   cp env.example .env
   ```
2. Fill in your keys in `.env`.
3. Place your Google Service Account credentials (or OAuth client secrets) in `credentials.json` at the root.

### 4. Running the Bot
Run the bot entrypoint:
```bash
python src/bot.py
```

### 5. Running the MCP Server (Standalone)
To run the Google Tools as a standalone MCP server (e.g., for use with Claude Desktop or other MCP clients):
```bash
python src/mcp_server/server.py
```

## Features
- **Schedule Events**: "Schedule a meeting with X on YYYY-MM-DD HH:MM"
- **Conflict Detection**: Checks Calendar for conflicts.
- **Drive Management**: Creates a dedicated folder for each event.
- **Photo Upload**: Send a photo to the bot to upload it to the current event's folder.
- **Sheet Logging**: Appends event details to a Google Sheet.

## Development
- `src/graph.py`: Defines the LangGraph workflow nodes and edges.
- `src/mcp_server/tools.py`: Implements the Google API wrappers.
- `src/bot.py`: Main Telegram polling loop and message handler.

