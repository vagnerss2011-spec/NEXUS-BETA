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
    # ===== Checagem de versão (banner de update no painel) =====
    # Repo no formato "owner/repo" — usado pra montar URL da API GitHub.
    # Default aponta pro repo oficial; instâncias custom podem trocar pra
    # apontar pra fork interno.
    GITHUB_REPO: str = "vagnerss2011-spec/NEXUS-BETA"
    # Personal Access Token com scope 'repo' (read). Vazio = checagem
    # desabilitada (endpoint retorna latest=None, banner não aparece).
    # Necessário porque o repo é privado — sem token a API GitHub
    # retorna 404 e nem dá pra ler tags/CHANGELOG.
    GITHUB_TOKEN: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
