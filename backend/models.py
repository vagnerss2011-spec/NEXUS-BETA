from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum

class UserRole(str, enum.Enum):
    admin = "admin"
    operador = "operador"
    viewer = "viewer"

class DeviceVendor(str, enum.Enum):
    mikrotik = "mikrotik"
    huawei = "huawei"
    ubiquiti = "ubiquiti"
    intelbras = "intelbras"
    outro = "outro"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.viewer)
    ativo = Column(Boolean, default=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())

class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    ip = Column(String(45), nullable=False)
    porta = Column(Integer, default=22)
    fabricante = Column(Enum(DeviceVendor), default=DeviceVendor.outro)
    usuario_ssh = Column(String(100), nullable=False)
    senha_ssh_enc = Column(Text, nullable=False)
    ativo = Column(Boolean, default=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    backups = relationship("Backup", back_populates="device", cascade="all, delete-orphan")

class Backup(Base):
    __tablename__ = "backups"
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    status = Column(String(10), nullable=False)  # sucesso | falha
    conteudo = Column(Text, nullable=True)
    erro = Column(Text, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    device = relationship("Device", back_populates="backups")
