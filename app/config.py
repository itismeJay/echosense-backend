from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    APP_NAME: str = "EchoSense API"
    TRUSTED_PROXY_CIDRS: str = ""
    RUN_LEGACY_STARTUP_MAINTENANCE: bool = False
    SQL_ECHO: bool = False
    TESTING: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
