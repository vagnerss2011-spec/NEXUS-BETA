from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from datetime import datetime
from models import UserRole, DeviceVendor, Protocolo, DeviceTipo, TipoAtividade


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

class EmpresaOut(BaseModel):
    id: int
    nome: str
    cnpj: Optional[str] = None
    ativo: bool
    criado_em: datetime
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
    criado_em: datetime
    class Config:
        from_attributes = True

# Device
class DeviceCreate(BaseModel):
    nome: str
    ip: str
    porta: int = 22
    fabricante: DeviceVendor = DeviceVendor.outro
    tipo: DeviceTipo = DeviceTipo.roteador
    protocolo: Protocolo = Protocolo.ssh
    usuario_ssh: str
    senha_ssh: str
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
    senha_ssh: Optional[str] = None
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
    usuario_ssh: str
    ativo: bool
    empresa_id: int
    criado_em: datetime
    ultimo_backup_status: Optional[str] = None  # 'sucesso' | 'falha' | None (nunca rodou)
    ultimo_backup_em: Optional[datetime] = None
    class Config:
        from_attributes = True

# Backup
class BackupOut(BaseModel):
    id: int
    device_id: int
    status: str
    conteudo: Optional[str] = None
    erro: Optional[str] = None
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

# Atividade (auditoria)
class AtividadeOut(BaseModel):
    id: int
    tipo: TipoAtividade
    usuario_id: Optional[int] = None
    usuario_nome: str
    empresa_id: Optional[int] = None
    ip: Optional[str] = None
    alvo_tipo: Optional[str] = None
    alvo_nome: Optional[str] = None
    detalhe: Optional[str] = None
    criado_em: datetime
    class Config:
        from_attributes = True
