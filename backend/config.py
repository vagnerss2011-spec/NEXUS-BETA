from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    BACKUP_RETENTION_DAYS: int = 7
    ENCRYPTION_KEY: str
    BACKEND_CORS_ORIGINS: str = "http://localhost:5173"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
