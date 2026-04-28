from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum

class UserRole(str, enum.Enum):
    admin = "admin"                  # admin master (global)
    admin_empresa = "admin_empresa"  # admin de uma única empresa
    operador = "operador"
    viewer = "viewer"

class DeviceVendor(str, enum.Enum):
    mikrotik = "mikrotik"        # RouterOS v6 — /export inclui senhas por default
    mikrotik_v7 = "mikrotik_v7"  # RouterOS v7 — /export mascara senhas; precisa de show-sensitive
    huawei = "huawei"
    ubiquiti = "ubiquiti"
    intelbras = "intelbras"
    datacom = "datacom"
    cisco = "cisco"
    juniper = "juniper"
    zte = "zte"
    nokia = "nokia"
    fiberhome = "fiberhome"
    vsolutions = "vsolutions"
    outro = "outro"

class Protocolo(str, enum.Enum):
    ssh = "ssh"
    telnet = "telnet"
    # Modos de PUSH: equipamento envia o backup pro servidor.
    # ftp_push   = FTP (porta 21, plano, user+senha+IP)
    # sftp_push  = SFTP via SSH (porta 22, criptografado, user+senha+IP)
    # tftp_push  = TFTP (porta 69/UDP, sem auth, IP /32 obrigatório)
    ftp_push = "ftp_push"
    sftp_push = "sftp_push"
    tftp_push = "tftp_push"

class AuthMethod(str, enum.Enum):
    password = "password"
    ssh_key = "ssh_key"

class DeviceTipo(str, enum.Enum):
    roteador = "roteador"
    olt = "olt"
    switch = "switch"
    wireless = "wireless"

class TipoAtividade(str, enum.Enum):
    login = "login"
    logout = "logout"
    device_criado = "device_criado"
    device_removido = "device_removido"
    device_teste_sucesso = "device_teste_sucesso"
    device_teste_falha = "device_teste_falha"
    usuario_criado = "usuario_criado"
    backup_removido = "backup_removido"
    # FTP push
    ftp_backup_recebido = "ftp_backup_recebido"
    ftp_volume_alto = "ftp_volume_alto"
    ftp_acesso_negado = "ftp_acesso_negado"

class Empresa(Base):
    __tablename__ = "empresas"
    id = Column(Integer, primary_key=True)
    nome = Column(String(120), unique=True, nullable=False)
    cnpj = Column(String(20), nullable=True)
    ativo = Column(Boolean, default=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    # Override do chat_id default global do Telegram. NULL = usa Configuracao.telegram_chat_id_default.
    # Permite que cada empresa tenha seu próprio grupo enquanto o bot é único.
    # Formato: ID numérico (ex.: -1001234567890 para grupo, número positivo pra usuário).
    telegram_chat_id = Column(String(40), nullable=True)
    usuarios = relationship("User", back_populates="empresa")
    devices = relationship("Device", back_populates="empresa", cascade="all, delete-orphan")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.viewer)
    ativo = Column(Boolean, default=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id", ondelete="SET NULL"), nullable=True)
    # Anti-bruteforce: zera no login bem-sucedido; se atinge limite, define bloqueado_ate.
    tentativas_falhas = Column(Integer, nullable=False, default=0)
    bloqueado_ate = Column(DateTime(timezone=True), nullable=True)
    # Forçar troca de senha no primeiro login. Default True para usuários
    # criados a partir de agora; usuários existentes ficam False na migração.
    senha_temporaria = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    empresa = relationship("Empresa", back_populates="usuarios")

class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    ip = Column(String(45), nullable=False)
    porta = Column(Integer, default=22)
    fabricante = Column(Enum(DeviceVendor), default=DeviceVendor.outro)
    tipo = Column(Enum(DeviceTipo), default=DeviceTipo.roteador, nullable=False)
    protocolo = Column(Enum(Protocolo), default=Protocolo.ssh, nullable=False)
    # usuario_ssh é nullable porque devices via ftp_push não têm usuário SSH.
    # Pra SSH/Telnet o router exige no validate; pra ftp_push fica null.
    usuario_ssh = Column(String(100), nullable=True)
    # senha agora é nullable porque o device pode autenticar via chave SSH
    senha_ssh_enc = Column(Text, nullable=True)
    auth_method = Column(Enum(AuthMethod), default=AuthMethod.password, nullable=False)
    chave_privada_enc = Column(Text, nullable=True)      # PEM/OpenSSH cifrado com Fernet
    chave_passphrase_enc = Column(Text, nullable=True)   # opcional, p/ chaves protegidas
    # FTP push (quando protocolo=ftp_push). Credenciais geradas pelo backend,
    # senha mostrada uma única vez ao admin. IP de origem é o filtro principal.
    ftp_user = Column(String(64), nullable=True, unique=True)
    ftp_senha_enc = Column(Text, nullable=True)
    ftp_origem_cidr = Column(String(64), nullable=True)  # ex.: 187.123.45.10/32 ou 187.123.45.0/24
    ativo = Column(Boolean, default=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    empresa = relationship("Empresa", back_populates="devices")
    backups = relationship("Backup", back_populates="device", cascade="all, delete-orphan")

class Backup(Base):
    __tablename__ = "backups"
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    log_scheduler_id = Column(Integer, ForeignKey("log_scheduler.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(10), nullable=False)  # sucesso | falha
    conteudo = Column(Text, nullable=True)
    erro = Column(Text, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    device = relationship("Device", back_populates="backups")

class Configuracao(Base):
    __tablename__ = "configuracoes"
    id = Column(Integer, primary_key=True)
    backup_hour = Column(Integer, nullable=False, default=2)
    backup_minute = Column(Integer, nullable=False, default=0)
    # Retenção de logs do scheduler em dias. 0 = desativado (não purga).
    log_retention_days = Column(Integer, nullable=False, default=30)
    # ===== Telegram (alertas de falha/corrupção) =====
    # Token do bot (Fernet-encrypted). NULL = notificações Telegram desabilitadas.
    # 1 bot único pra toda a instalação; cada empresa pode ter seu chat_id próprio.
    telegram_bot_token_enc = Column(String(500), nullable=True)
    # Chat ID default — usado quando empresa.telegram_chat_id é NULL.
    # Formato: ID numérico do grupo/canal (ex.: -1001234567890).
    telegram_chat_id_default = Column(String(40), nullable=True)
    # Liga/desliga categorias específicas de alerta (default: tudo ON quando token configurado).
    telegram_alerta_falha_backup = Column(Boolean, nullable=False, default=True)
    telegram_alerta_push_negado = Column(Boolean, nullable=False, default=True)
    telegram_alerta_volume_alto = Column(Boolean, nullable=False, default=True)

class Atividade(Base):
    __tablename__ = "atividades"
    id = Column(Integer, primary_key=True)
    tipo = Column(Enum(TipoAtividade), nullable=False)
    usuario_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    usuario_nome = Column(String(120), nullable=False)  # snapshot (não quebra se user for deletado)
    empresa_id = Column(Integer, ForeignKey("empresas.id", ondelete="SET NULL"), nullable=True)
    ip = Column(String(45), nullable=True)
    alvo_tipo = Column(String(30), nullable=True)  # 'device' | 'user'
    alvo_nome = Column(String(150), nullable=True)
    detalhe = Column(String(200), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())

class LogScheduler(Base):
    __tablename__ = "log_scheduler"
    id = Column(Integer, primary_key=True)
    inicio = Column(DateTime(timezone=True), nullable=False)
    fim = Column(DateTime(timezone=True), nullable=True)
    total = Column(Integer, nullable=True)
    sucessos = Column(Integer, nullable=True)
    falhas = Column(Integer, nullable=True)
    erro_geral = Column(Text, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
