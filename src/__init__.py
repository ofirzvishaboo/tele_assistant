"""
Telegram Event Scheduling Bot.

A production-grade Telegram bot for scheduling events with Google Workspace integration.
"""

__version__ = "0.1.0"

# Expose main entry point for easier imports
from src.bot import main as bot_main

__all__ = ["__version__", "bot_main"]

