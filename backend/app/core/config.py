from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT_ENV), ".env"), env_file_encoding="utf-8"
    )

    PROJECT_NAME: str = "Musée des Pirates"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/musee_pirates"


settings = Settings()
