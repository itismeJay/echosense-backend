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
# Readable by browser clients; X-Total-Count carries totals for paginated lists.
CORS_EXPOSED_HEADERS = ("X-Request-ID", "X-Total-Count")


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
    # Transition switch for edges that predate device credentials on heartbeat, log upload
    # and dictionary sync. Off by default; turn on only while an old edge is still in the
    # field, then turn it off. Wrong credentials are always rejected.
    ECHOSENSE_ALLOW_ANONYMOUS_EDGE: bool = False
    # Failed sign-ins allowed per window before /auth/login answers 429.
    LOGIN_RATE_LIMIT_ENABLED: bool = True
    LOGIN_MAX_FAILURES_PER_ACCOUNT: int = 5
    LOGIN_MAX_FAILURES_PER_IP: int = 30
    LOGIN_FAILURE_WINDOW_SECONDS: int = 900
    # Wrong QR setup codes allowed per window before claim/activate answer 429.
    DEVICE_CLAIM_RATE_LIMIT_ENABLED: bool = True
    DEVICE_CLAIM_MAX_FAILURES_PER_DEVICE: int = 5
    DEVICE_CLAIM_MAX_FAILURES_PER_USER: int = 10
    DEVICE_CLAIM_MAX_FAILURES_PER_IP: int = 30
    DEVICE_CLAIM_FAILURE_WINDOW_SECONDS: int = 900
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
