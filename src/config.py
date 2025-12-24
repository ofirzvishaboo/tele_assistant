from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    TELEGRAM_BOT_TOKEN: SecretStr

    # OpenAI
    OPENAI_API_KEY: SecretStr

    # Google
    # Path to the credentials.json file (Service Account or OAuth Client ID)
    GOOGLE_CREDENTIALS_PATH: str = "credentials.json"
    # Token path for saving user credentials (if using OAuth user flow)
    GOOGLE_TOKEN_PATH: str = "token.json"

    # Drive Config
    GOOGLE_DRIVE_PARENT_FOLDER_ID: str = Field(description="ID of the parent folder where event folders are created")
    GOOGLE_SHEET_ID: str = Field(description="ID of the 'upcoming_events' Google Sheet")

    # App Config
    DRY_RUN: bool = False
    DB_PATH: str = "src/db/bot_state.sqlite"

settings = Settings()

