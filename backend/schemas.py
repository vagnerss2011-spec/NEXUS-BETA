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
    duracao_segundos: Optional[int] = None  # tempo da coleta (NULL = push ou pré-feature)
    criado_em: datetime
    class Config:
        from_attributes = True

class BackupWithDevice(BackupOut):
    device: DeviceOut


# Schema LEVE de listagem (v2.2.4) — NÃO inclui `conteudo`. A listagem de
# backups trazia o conteúdo inline de até 200 backups (config pode ter MBs),
# gerando payloads de dezenas de MB que travavam o frontend. Aqui só vão
# metadados + `tamanho_bytes` (calculado via length(conteudo) no SQL, sem
# puxar o conteúdo). O conteúdo é buscado sob demanda no preview/download.
class BackupListItem(BaseModel):
    id: int
    device_id: int
    status: str
    erro: Optional[str] = None
    log_scheduler_id: Optional[int] = None
    origem: str = "manual"
    nome_arquivo: Optional[str] = None
    duracao_segundos: Optional[int] = None
    criado_em: datetime
    tamanho_bytes: Optional[int] = None  # length(conteudo) — preenchido pelo router
    class Config:
        from_attributes = True


class BackupListWithDevice(BackupListItem):
    device: DeviceOut

# Schedule
class ScheduleOut(BaseModel):
    backup_hour: int
    backup_minute: int
    log_retention_days: int
    # Tuning do scheduler diário (v2 — delay adaptativo entre devices)
    backup_delay_min_seg: int = 10
    backup_delay_fator: float = 0.2
    backup_pico_fator_critico: float = 3.0
    # Paralelismo adaptativo (Zabbix-like)
    backup_workers_max_api: int = 4
    backup_workers_max_ssh: int = 2
    backup_cpu_limite_pct: int = 80
    backup_mem_limite_pct: int = 80
    backup_workers_auto: bool = True
    class Config:
        from_attributes = True

class ScheduleUpdate(BaseModel):
    backup_hour: int
    backup_minute: int
    log_retention_days: Optional[int] = None  # 0 = desativa a purga
    backup_delay_min_seg: Optional[int] = None
    backup_delay_fator: Optional[float] = None
    backup_pico_fator_critico: Optional[float] = None
    backup_workers_max_api: Optional[int] = None
    backup_workers_max_ssh: Optional[int] = None
    backup_cpu_limite_pct: Optional[int] = None
    backup_mem_limite_pct: Optional[int] = None
    backup_workers_auto: Optional[bool] = None

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


# ===== Export do banco (.nxbak — v2.0.0) =====

class DbExportConfigOut(BaseModel):
    """Estado da config do export do banco. db_export_key_configurada é
    derivado (settings.DB_EXPORT_KEY existe e válida) — admin vê se a
    chave do .env está populada sem expor o valor."""
    db_export_enabled: bool = True
    db_export_hour: int = 3
    db_export_minute: int = 30
    db_export_remote_enabled: bool = False
    db_export_remote_protocolo: str = "sftp"
    db_export_remote_host: Optional[str] = None
    db_export_remote_porta: int = 22
    db_export_remote_user: Optional[str] = None
    db_export_remote_senha_configurada: bool = False  # True se há senha salva (sem expor)
    db_export_remote_path: str = "/"
    db_export_remote_dia_semana: int = 0
    db_export_remote_hora: int = 4
    db_export_remote_minute: int = 0
    # Sinaliza pro frontend se a chave Fernet está configurada — sem ela
    # o export não acontece, e o admin deve preencher DB_EXPORT_KEY no .env.
    chave_configurada: bool = False
    # Lista dos arquivos .nxbak presentes localmente (nome + tamanho + data).
    arquivos_locais: list[dict] = []


class DbExportConfigUpdate(BaseModel):
    """Update parcial da config — só envia os campos que mudaram."""
    db_export_enabled: Optional[bool] = None
    db_export_hour: Optional[int] = None
    db_export_minute: Optional[int] = None
    db_export_remote_enabled: Optional[bool] = None
    db_export_remote_protocolo: Optional[str] = None  # 'sftp' | 'ftp'
    db_export_remote_host: Optional[str] = None
    db_export_remote_porta: Optional[int] = None
    db_export_remote_user: Optional[str] = None
    # Senha em texto puro. None = não tocar; '' (vazio) = limpar; valor = setar.
    db_export_remote_senha: Optional[str] = None
    db_export_remote_path: Optional[str] = None
    db_export_remote_dia_semana: Optional[int] = None
    db_export_remote_hora: Optional[int] = None
    db_export_remote_minute: Optional[int] = None

# ===== Firmware Mirror FTP (v2.2.0) =====

class FirmwareOut(BaseModel):
    """Metadados de firmware armazenado no /var/firmware/.

    Não devolve o conteúdo do arquivo — download é endpoint separado.
    """
    id: int
    nome: str
    descricao: Optional[str] = None
    arquivo_nome: str
    tamanho_bytes: int
    sha256: str
    fabricante: Optional[str] = None
    modelo_alvo: Optional[str] = None
    versao: Optional[str] = None
    criado_em: datetime
    criado_por_nome: str
    class Config:
        from_attributes = True


class FirmwareUpdate(BaseModel):
    """Update de metadados (não troca arquivo — re-upload é delete + create)."""
    nome: Optional[str] = None
    descricao: Optional[str] = None
    fabricante: Optional[str] = None
    modelo_alvo: Optional[str] = None
    versao: Optional[str] = None


class FirmwareOrfaoOut(BaseModel):
    """Arquivo presente em /var/firmware/ mas SEM row na tabela firmwares.

    Foi subido via FTP por uma origem (write permitido) e ainda não foi
    promovido ou deletado pelo admin. UI mostra em seção separada de
    'Uploads externos' pra ação manual.
    """
    arquivo_nome: str
    tamanho_bytes: int
    modificado_em: datetime


class FirmwareOrigemCreate(BaseModel):
    nome: str
    descricao: Optional[str] = None
    # Whitelist de IP(s)/CIDR(s) de origem — vazio = sem restrição.
    # Múltiplos separados por vírgula: "200.1.2.3/32, 10.0.0.0/24".
    origem_cidr: Optional[str] = None
    empresa_id: Optional[int] = None  # NULL = global (visível a todos os admins master)


class FirmwareOrigemUpdate(BaseModel):
    nome: Optional[str] = None
    descricao: Optional[str] = None
    ativo: Optional[bool] = None
    # None = não tocar; "" (vazio) = limpar whitelist; valor = setar.
    origem_cidr: Optional[str] = None


class FirmwareOrigemOut(BaseModel):
    """Listagem/leitura — NÃO inclui senha (cifrada no banco)."""
    id: int
    nome: str
    descricao: Optional[str] = None
    usuario_ftp: str
    ativo: bool
    origem_cidr: Optional[str] = None
    empresa_id: Optional[int] = None
    ultimo_acesso_em: Optional[datetime] = None
    ultimo_ip: Optional[str] = None
    criado_em: datetime
    criado_por_nome: str
    class Config:
        from_attributes = True


class ReleaseInfoOut(BaseModel):
    """Metadados de uma GitHub Release (ou tag, no fallback) — v2.3.0."""
    tag: str
    version: str
    name: Optional[str] = None
    published_at: Optional[str] = None
    body: Optional[str] = None
    prerelease: bool = False
    is_lts: bool = False
    url: Optional[str] = None


class VersionCheckOut(BaseModel):
    """Estado de versão da instância vs canais LTS/Edge do GitHub Releases."""
    current: str                                # APP_VERSION ('2.2.4')
    channel: str                                # 'lts' | 'edge'
    update_available: bool = False
    checked_at: float
    current_release: Optional[ReleaseInfoOut] = None
    current_dias_em_producao: Optional[int] = None
    latest_lts: Optional[ReleaseInfoOut] = None
    latest_edge: Optional[ReleaseInfoOut] = None
    target: Optional[ReleaseInfoOut] = None     # latest do canal escolhido
    # Compat: o UpdateBanner original lia esses 2 campos. Mantidos pra não
    # quebrar caches/clients enquanto a UI nova rolling out.
    latest: Optional[str] = None
    changelog_summary: Optional[str] = None


class UpdateChannelIn(BaseModel):
    channel: str  # 'lts' | 'edge'

    @field_validator("channel")
    @classmethod
    def _valida_channel(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("lts", "edge"):
            raise ValueError("channel deve ser 'lts' ou 'edge'")
        return v


class UpdateChannelOut(BaseModel):
    channel: str  # 'lts' | 'edge'


class FirmwareOrigemCredencial(FirmwareOrigemOut):
    """Retornado SOMENTE no momento da criação/regeneração da senha — senha
    em texto puro, mostrada uma vez. Frontend deve forçar copy + warn."""
    senha_ftp: str


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
