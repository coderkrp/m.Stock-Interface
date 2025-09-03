import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    M_API_KEY: str
    M_API_SECRET: str
    M_USERNAME: str | None = None
    M_PASSWORD: str | None = None

    APP_ADMIN_TOKEN: str = Field(
        default_factory=lambda: os.getenv("APP_ADMIN_TOKEN", "change-me")
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
