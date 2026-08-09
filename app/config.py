from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CORS_ORIGINS = ("https://echosense-frontend.vercel.app",)
CORS_ALLOWED_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")
CORS_ALLOWED_HEADERS = (
    "Authorization",
    "Content-Type",
    "X-EchoSense-Device-Id",
    "X-EchoSense-Device-Key",
    "Idempotency-Key",
)


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    APP_NAME: str = "EchoSense API"
    TRUSTED_PROXY_CIDRS: str = ""
    ECHOSENSE_CONTROLLED_TEST_MODE: bool = False
    ECHOSENSE_CONTROLLED_TEST_USER_ID: int | None = None
    ECHOSENSE_ALLOW_TEST_ALERTS: bool = False
    ECHOSENSE_CORS_ORIGINS: str = ",".join(DEFAULT_CORS_ORIGINS)
    EXPO_ACCESS_TOKEN: str | None = None
    RUN_LEGACY_STARTUP_MAINTENANCE: bool = False
    SQL_ECHO: bool = False
    TESTING: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.ECHOSENSE_CORS_ORIGINS.split(",")]
        origins = [origin for origin in origins if origin]
        if "*" in origins:
            raise ValueError(
                "ECHOSENSE_CORS_ORIGINS cannot contain '*' when credentials are enabled"
            )
        return origins


settings = Settings()
