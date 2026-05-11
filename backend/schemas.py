from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from datetime import datetime
from models import UserRole, DeviceVendor, Protocolo, DeviceTipo, TipoAtividade, AuthMethod


def _normalize_ip(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    s = v.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    if not s:
        raise ValueError("IP não pode ficar vazio")
    return s

# Auth
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    user_id: Optional[int] = None

# Empresa
class EmpresaCreate(BaseModel):
    nome: str
    cnpj: Optional[str] = None

class EmpresaUpdate(BaseModel):
    nome: Optional[str] = None
    cnpj: Optional[str] = None
    ativo: Optional[bool] = None
    # NULL/string-vazia limpa (volta ao default global); valor numérico
    # (-1001234567890 ou similar) define o chat-grupo desta empresa.
    telegram_chat_id: Optional[str] = None

class EmpresaOut(BaseModel):
    id: int
    nome: str
    cnpj: Optional[str] = None
    ativo: bool
    criado_em: datetime
    telegram_chat_id: Optional[str] = None
    class Config:
        from_attributes = True

class UltimaAlteracaoDevice(BaseModel):
    tipo: str  # device_criado | device_removido
    usuario_nome: str
    alvo_nome: Optional[str] = None
    criado_em: datetime

class EmpresaStatsOut(EmpresaOut):
    total_devices: int = 0
    sucessos: int = 0
    falhas: int = 0
    sem_backup: int = 0
    ultima_alteracao: Optional[UltimaAlteracaoDevice] = None

# User
class UserCreate(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    role: UserRole = UserRole.viewer
    empresa_id: Optional[int] = None

class UserUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[EmailStr] = None
    senha: Optional[str] = None
    role: Optional[UserRole] = None
    ativo: Optional[bool] = None
    empresa_id: Optional[int] = None

class UserOut(BaseModel):
    id: int
    nome: str
    email: str
    role: UserRole
    ativo: bool
    empresa_id: Optional[int] = None
    senha_temporaria: bool = False
    criado_em: datetime
    class Config:
        from_attributes = True


class ChangePasswordIn(BaseModel):
    senha_atual: str
    senha_nova: str

    @field_validator("senha_nova")
    @classmethod
    def _valida_nova(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Nova senha precisa ter pelo menos 8 caracteres")
        return v

# Device
class DeviceCreate(BaseModel):
    nome: str
    ip: str
    porta: int = 22
    fabricante: DeviceVendor = DeviceVendor.outro
    tipo: DeviceTipo = DeviceTipo.roteador
    protocolo: Protocolo = Protocolo.ssh
    usuario_ssh: Optional[str] = None  # opcional para protocolo=ftp_push
    auth_method: AuthMethod = AuthMethod.password
    senha_ssh: Optional[str] = None
    chave_privada: Optional[str] = None      # PEM/OpenSSH em texto puro; o router cifra antes de salvar
    chave_passphrase: Optional[str] = None
    # FTP push (apenas quando protocolo=ftp_push)
    ftp_origem_cidr: Optional[str] = None
    # API Mikrotik (apenas quando protocolo=api): True = porta 8729 TLS, False = 8728 plain.
    api_tls: bool = False
    # True = device fica de fora do scheduler diário; só roda backup quando o
    # admin clica "Executar backup" no painel. Default False mantém o
    # comportamento legado (todo device entra no scheduler).
    backup_manual_apenas: bool = False
    empresa_id: Optional[int] = None  # exigido p/ admin master; ignorado p/ demais (usa a do token)

    @field_validator("ip")
    @classmethod
    def _norm_ip(cls, v):
        return _normalize_ip(v)

class DeviceUpdate(BaseModel):
    nome: Optional[str] = None
    ip: Optional[str] = None
    porta: Optional[int] = None
    fabricante: Optional[DeviceVendor] = None
    tipo: Optional[DeviceTipo] = None
    protocolo: Optional[Protocolo] = None
    usuario_ssh: Optional[str] = None
    auth_method: Optional[AuthMethod] = None
    senha_ssh: Optional[str] = None
    chave_privada: Optional[str] = None
    chave_passphrase: Optional[str] = None
    ftp_origem_cidr: Optional[str] = None
    api_tls: Optional[bool] = None
    backup_manual_apenas: Optional[bool] = None
    ativo: Optional[bool] = None
    empresa_id: Optional[int] = None  # só admin master pode mover entre empresas

    @field_validator("ip")
    @classmethod
    def _norm_ip(cls, v):
        return _normalize_ip(v)

class DeviceOut(BaseModel):
    id: int
    nome: str
    ip: str
    porta: int
    fabricante: DeviceVendor
    tipo: DeviceTipo
    protocolo: Protocolo
    usuario_ssh: Optional[str] = None
    auth_method: AuthMethod = AuthMethod.password
    ativo: bool
    empresa_id: int
    criado_em: datetime
    # FTP push: ftp_senha só aparece UMA VEZ no retorno da criação/regeneração
    # (populada manualmente pelo router; em listagens fica None pois o model
    # SQLAlchemy não tem esse atributo).
    ftp_user: Optional[str] = None
    ftp_origem_cidr: Optional[str] = None
    ftp_senha: Optional[str] = None
    # API Mikrotik (só relevante quando protocolo='api')
    api_tls: bool = False
    # True = scheduler diário pula este device; backup só por clique manual.
    backup_manual_apenas: bool = False
    ultimo_backup_status: Optional[str] = None  # 'sucesso' | 'falha' | None (nunca rodou)
    ultimo_backup_em: Optional[datetime] = None
    class Config:
        from_attributes = True


# Retornado SOMENTE no momento de gerar/regenerar credencial FTP — senha em texto puro,
# nunca persistida em logs nem retornada por listagens. UX: mostrar e copiar.
class FTPCredentialOut(BaseModel):
    ftp_user: str
    ftp_senha: str
    ftp_origem_cidr: Optional[str] = None

# Backup
class BackupOut(BaseModel):
    id: int
    device_id: int
    status: str
    conteudo: Optional[str] = None
    erro: Optional[str] = None
    log_scheduler_id: Optional[int] = None  # preenchido = scheduler do painel
    origem: str = "manual"  # manual | scheduler | push (vide models.Backup.origem)
    nome_arquivo: Optional[str] = None  # nome do arquivo original (push) — chave pra identificar fonte
    criado_em: datetime
    class Config:
        from_attributes = True

class BackupWithDevice(BackupOut):
    device: DeviceOut

# Schedule
class ScheduleOut(BaseModel):
    backup_hour: int
    backup_minute: int
    log_retention_days: int
    class Config:
        from_attributes = True

class ScheduleUpdate(BaseModel):
    backup_hour: int
    backup_minute: int
    log_retention_days: Optional[int] = None  # 0 = desativa a purga

# Telegram (alertas de falha/corrupção)
class TelegramConfigOut(BaseModel):
    """Estado atual do Telegram para o admin master visualizar.

    Token nunca é exposto cru — só um booleano 'configurado' indicando se há
    valor cadastrado. Pra trocar, manda valor novo via TelegramConfigUpdate.
    """
    bot_configurado: bool
    chat_id_default: Optional[str] = None
    alerta_falha_backup: bool
    alerta_push_negado: bool
    alerta_volume_alto: bool

class TelegramConfigUpdate(BaseModel):
    """Atualização do Telegram global. Campos opcionais — só os enviados mudam.

    bot_token vazio (string vazia) = limpa/desabilita; None = mantém valor atual.
    """
    bot_token: Optional[str] = None
    chat_id_default: Optional[str] = None
    alerta_falha_backup: Optional[bool] = None
    alerta_push_negado: Optional[bool] = None
    alerta_volume_alto: Optional[bool] = None

class TelegramTestRequest(BaseModel):
    """Payload do botão 'Enviar teste' no painel — chat_id opcional pra testar
    o de uma empresa específica antes de salvar."""
    chat_id: Optional[str] = None  # se None, usa o default global

# Atividade (auditoria)
class AtividadeOut(BaseModel):
    id: int
    tipo: TipoAtividade
    usuario_id: Optional[int] = None
    usuario_nome: str
    empresa_id: Optional[int] = None
    empresa_nome: Optional[str] = None
    ip: Optional[str] = None
    alvo_tipo: Optional[str] = None
    alvo_nome: Optional[str] = None
    detalhe: Optional[str] = None
    criado_em: datetime
    class Config:
        from_attributes = True
