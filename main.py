"""
Main entry point for the Telegram Event Scheduling Bot.
"""

import asyncio
import sys
from pathlib import Path

# Ensure the project root is in Python path for imports
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import and run the bot
from src.bot import main

if __name__ == "__main__":
    asyncio.run(main())
