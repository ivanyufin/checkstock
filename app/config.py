from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "CheckStock"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://checkstock:checkstock@localhost:5432/checkstock"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
