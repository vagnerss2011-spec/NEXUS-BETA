from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    BACKUP_RETENTION_DAYS: int = 7
    ENCRYPTION_KEY: str
    BACKEND_CORS_ORIGINS: str = "http://localhost:5173"
    # IP que o servidor FTP anuncia em respostas PASV. Quando o backend roda
    # em container Docker bridge, pyftpdlib vê o local IP como 172.18.x.x e
    # mandaria o cliente conectar ali — inalcançável de fora. Setar o IP
    # público (ou o IP de gerência atravessável pelos clientes) faz o PASV
    # funcionar. Sem isso, uploads chegam com 0 bytes (controle OK, dados ✗).
    # Vazio = não anuncia nada (cliente usa o IP da control connection).
    FTP_MASQUERADE_ADDRESS: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
