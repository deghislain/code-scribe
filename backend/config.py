from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GROQ_API_KEY: str
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    POLL_INTERVAL_SECONDS: int = 300
    WORKSPACE_DIR: str = "./workspaces"
    OUTPUT_DIR: str = "./outputs"
    DB_PATH: str = "./code_scribe.db"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
