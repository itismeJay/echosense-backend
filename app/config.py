from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str = "echosense-secret-key"
    APP_NAME: str = "EchoSense API"

    class Config:
        env_file = ".env"

settings = Settings()