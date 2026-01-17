"""
Configuration management with environment variable validation.

This module provides production-ready configuration with validation,
type checking, and sensible defaults.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, Field, field_validator

class Settings(BaseSettings):
    """Application settings with validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True
    )

    # Telegram
    TELEGRAM_BOT_TOKEN: SecretStr = Field(
        description="Telegram bot token from BotFather"
    )

    # OpenAI
    OPENAI_API_KEY: SecretStr = Field(
        description="OpenAI API key for LLM operations"
    )

    # Google
    GOOGLE_CREDENTIALS_PATH: str = Field(
        default="credentials.json",
        description="Path to Google OAuth credentials JSON file"
    )
    GOOGLE_TOKEN_PATH: str = Field(
        default="token.json",
        description="Path to store Google OAuth token"
    )

    # Drive Config
    GOOGLE_DRIVE_PARENT_FOLDER_ID: str = Field(
        description="ID of the parent folder where event folders are created"
    )
    GOOGLE_SHEET_ID: str = Field(
        description="ID of the 'upcoming_events' Google Sheet"
    )

    # App Config
    DRY_RUN: bool = Field(
        default=False,
        description="Enable dry-run mode (no actual API calls)"
    )

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://botuser:botpass@localhost:5432/tele_assistant",
        description="PostgreSQL database URL (format: postgresql+asyncpg://user:pass@host:port/dbname)"
    )

    # Logging
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )

    # Cocktail API
    COCKTAIL_API_URL: str = Field(
        default="http://localhost:8000",
        description="Base URL for Cocktail Recipe Manager API"
    )
    COCKTAIL_API_EMAIL: str = Field(
        description="Email for Cocktail API service account"
    )
    COCKTAIL_API_PASSWORD: SecretStr = Field(
        description="Password for Cocktail API service account"
    )
    COCKTAIL_API_LOCATION: str = Field(
        default="BAR",
        description="Default location for inventory operations (BAR or WAREHOUSE)"
    )

    @field_validator("GOOGLE_CREDENTIALS_PATH")
    @classmethod
    def validate_credentials_path(cls, v: str) -> str:
        """Validate credentials path format."""
        # Don't check existence here - it will be checked when GoogleService initializes
        if not v or not v.endswith('.json'):
            raise ValueError(f"Credentials path must be a JSON file: {v}")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v.upper()

settings = Settings()

