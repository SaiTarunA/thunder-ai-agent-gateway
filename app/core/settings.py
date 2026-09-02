from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Define variables with type hints and default values
    OPEN_AI_ASSISTANT_API_KEY: str = ""
    OPEN_AI_API_KEY: str = ""
    ADMIN_SUPPORT_ASSISTANT_ID: str = ""

    JWT_SECRET_KEY: str = ""
    JWT_TOKEN_EXPIRY_TIME: str = "2d"
    JWT_ALGORITHM: str = "HS256"
    JWT_LEEWAY: int = 10

    # Tell Pydantic to read from a .env file
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
