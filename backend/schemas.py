from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
from models import UserRole, DeviceVendor

# Auth
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    user_id: Optional[int] = None

# User
class UserCreate(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    role: UserRole = UserRole.viewer

class UserUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    ativo: Optional[bool] = None

class UserOut(BaseModel):
    id: int
    nome: str
    email: str
    role: UserRole
    ativo: bool
    criado_em: datetime
    class Config:
        from_attributes = True

# Device
class DeviceCreate(BaseModel):
    nome: str
    ip: str
    porta: int = 22
    fabricante: DeviceVendor = DeviceVendor.outro
    usuario_ssh: str
    senha_ssh: str

class DeviceUpdate(BaseModel):
    nome: Optional[str] = None
    ip: Optional[str] = None
    porta: Optional[int] = None
    fabricante: Optional[DeviceVendor] = None
    usuario_ssh: Optional[str] = None
    senha_ssh: Optional[str] = None
    ativo: Optional[bool] = None

class DeviceOut(BaseModel):
    id: int
    nome: str
    ip: str
    porta: int
    fabricante: DeviceVendor
    usuario_ssh: str
    ativo: bool
    criado_em: datetime
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
