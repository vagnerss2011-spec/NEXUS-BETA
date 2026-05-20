from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Enum, Float, JSON
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
    # RouterOS API binária (Mikrotik v6+v7): porta 8728 (plain) ou 8729 (TLS).
    # Coleta /export sem precisar abrir SSH/Telnet no equipamento. TLS é
    # configurado por device via Device.api_tls (default False).
    api = "api"
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
    # NMS (Fiberhome UNM2000) — não é equipamento de rede físico, é um servidor
    # gerenciador. Recebe push backup do próprio EMS (Control and Monitor Tools
    # → Set Backup Server). UI separa em aba própria pois o fluxo é diferente:
    # nunca é polado via SSH, só recebe via FTP/SFTP. Senha FTP limitada a 20
    # chars por restrição do EMS.
    unm2000 = "unm2000"

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
    # Falha de transfer/processamento APÓS auth ok (tamanho 0, > limite, exceção
    # no parser binário, OSError, etc). Distinto de ftp_acesso_negado: nesse
    # caso a credencial foi aceita mas o upload em si quebrou.
    ftp_backup_falha = "ftp_backup_falha"
    # Firmware Mirror (v2.2.0): mirror FTP de firmwares pra devices baixarem
    # via /tool fetch (Mikrotik) ou comando equivalente. Origens são credenciais
    # FTP separadas do fluxo de push de backup — chroot em /var/firmware/.
    firmware_baixado = "firmware_baixado"            # device baixou arquivo via FTP
    firmware_enviado = "firmware_enviado"            # origem subiu arquivo via FTP (fica como órfão)
    firmware_upload_painel = "firmware_upload_painel"  # admin/operador fez upload pelo painel
    firmware_removido = "firmware_removido"
    firmware_origem_criada = "firmware_origem_criada"
    firmware_origem_removida = "firmware_origem_removida"

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
    # RouterOS API: true = TLS na porta 8729, false = plain na porta 8728.
    # Só consultado quando protocolo == 'api'. Default False (sem TLS) porque
    # Mikrotik não vem com cert válido out-of-the-box; TLS exige config extra
    # no equipamento (gerar + bind cert). Plain é OK em LAN privada confiável.
    api_tls = Column(Boolean, nullable=False, default=False)
    # Quando True, este device NÃO entra no scheduler diário; só roda backup
    # quando o admin clica "Executar backup" no painel. A retenção (max N
    # backups por device, controlada por BACKUP_RETENTION_DAYS no .env)
    # continua valendo — mesmo em manual o histórico fica limitado.
    # Default False preserva comportamento pré-feature (automático todo dia).
    backup_manual_apenas = Column(Boolean, nullable=False, default=False)
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
    # Origem do backup — diferencia 3 fluxos distintos para UI/auditoria:
    #  - manual:    botão "Testar agora" no painel (ação consciente do usuário)
    #  - scheduler: scheduler interno do painel polando o device via SSH
    #  - push:      o próprio equipamento enviou via servidor embutido (FTP/SFTP/TFTP),
    #               geralmente disparado por um scheduler configurado no device.
    # Default 'manual' cobre rows antigas (pré-migração) sem quebrar.
    origem = Column(String(16), nullable=False, server_default="manual", default="manual")
    # Nome do arquivo original (push) — preserva identificação da fonte. Crítico
    # quando 1 device cadastrado (ex.: UNM2000) recebe múltiplos arquivos por dia
    # de OLTs distintas que ele gerencia. Sem esse campo, vira "qual backup veio
    # de qual OLT?". NULL = backup sem origem-arquivo (manual/scheduler ou push
    # antigo pré-migração).
    nome_arquivo = Column(String(255), nullable=True)
    # Duração em segundos da coleta (tempo do paramiko/netmiko/api). Usada
    # pelo scheduler pra calcular delay adaptativo do próximo device e
    # detectar picos. NULL = backup pré-feature (rodou antes da v2 do
    # scheduler) ou ftp-push (não medimos tempo de chegada).
    duracao_segundos = Column(Integer, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    device = relationship("Device", back_populates="backups")

class Configuracao(Base):
    __tablename__ = "configuracoes"
    id = Column(Integer, primary_key=True)
    backup_hour = Column(Integer, nullable=False, default=2)
    backup_minute = Column(Integer, nullable=False, default=0)
    # Retenção de logs do scheduler em dias. 0 = desativado (não purga).
    log_retention_days = Column(Integer, nullable=False, default=30)
    # ===== Tuning do scheduler diário (delay entre devices) =====
    # Delay MÍNIMO (segundos) entre o fim de um backup e o início do próximo.
    # Default 10s — dá fôlego pro container/rede entre coletas sequenciais.
    # 0 = sem delay (comportamento legado pré-feature, válido em prod pequena).
    backup_delay_min_seg = Column(Integer, nullable=False, default=10)
    # Fator multiplicador da duração do device anterior pra calcular o delay
    # adaptativo: delay = max(min_seg, fator × duração_anterior).
    # Default 0.2 — backup de 5min → 60s de pausa; 30s → fica no piso.
    # 0.0 = só piso fixo (sem adaptação).
    backup_delay_fator = Column(Float, nullable=False, default=0.2)
    # Fator de detecção de pico: se duração do device > fator × média histórica,
    # registra WARNING crítico e dispara alerta Telegram (categoria volume_alto).
    # Default 3.0 — backup que normalmente leva 60s mas levou 180s vira alerta.
    backup_pico_fator_critico = Column(Float, nullable=False, default=3.0)
    # ===== Paralelismo adaptativo (Zabbix-like auto-tuning) =====
    # Cap MÁXIMO de workers paralelos. Hard limit no código é 8 — valores
    # acima são truncados pra evitar disaster (estouro de threads paramiko).
    # Pool API é mais agressivo (default 4) porque API binária é leve;
    # pool SSH/Telnet é conservador (default 2) porque Paramiko/Netmiko
    # consomem mais socket+thread por sessão.
    backup_workers_max_api = Column(Integer, nullable=False, default=4)
    backup_workers_max_ssh = Column(Integer, nullable=False, default=2)
    # Limites de stress: acima desses %, o scheduler corta workers pela metade.
    # CPU média 60s e memória média 60s. Defaults 80% — folga pro overhead
    # do Docker/uvicorn sem strangular.
    backup_cpu_limite_pct = Column(Integer, nullable=False, default=80)
    backup_mem_limite_pct = Column(Integer, nullable=False, default=80)
    # Liga/desliga o auto-tuning AIMD (Additive Increase Multiplicative Decrease).
    # ON: workers começam em 1 e sobem gradativamente conforme histórico de
    # sucesso + telemetria; descem pela metade ao detectar stress.
    # OFF: trava em 1 worker (comportamento legado pré-v2.x, sequencial).
    backup_workers_auto = Column(Boolean, nullable=False, default=True)
    # ===== Export do banco em .nxbak (major v2.0.0) =====
    # Snapshot diário criptografado de todos os backups armazenados — pra
    # poder recuperar o histórico mesmo se o servidor queimar/corromper.
    # Aberto apenas pela ferramenta externa que conhece o formato + chave Fernet.
    db_export_enabled = Column(Boolean, nullable=False, default=True)
    # Hora local da exportação diária. Default 03:30 — depois do scheduler
    # diário (02:00) terminar de coletar backups novos.
    db_export_hour = Column(Integer, nullable=False, default=3)
    db_export_minute = Column(Integer, nullable=False, default=30)
    # ===== Upload semanal pra "nuvem de segurança" externa =====
    # Default OFF — admin precisa configurar conscientemente. Quando ON,
    # 1× por semana o .nxbak mais recente é enviado pro servidor remoto.
    db_export_remote_enabled = Column(Boolean, nullable=False, default=False)
    db_export_remote_protocolo = Column(String(8), nullable=False, default="sftp")  # 'sftp' | 'ftp'
    db_export_remote_host = Column(String(120), nullable=True)
    db_export_remote_porta = Column(Integer, nullable=False, default=22)
    db_export_remote_user = Column(String(120), nullable=True)
    db_export_remote_senha_enc = Column(Text, nullable=True)  # cifrada com ENCRYPTION_KEY
    db_export_remote_path = Column(String(255), nullable=False, default="/")
    db_export_remote_dia_semana = Column(Integer, nullable=False, default=0)  # 0=segunda ... 6=domingo (cron-style)
    db_export_remote_hora = Column(Integer, nullable=False, default=4)
    db_export_remote_minute = Column(Integer, nullable=False, default=0)
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

class OperacaoMassaLog(Base):
    """Auditoria de execuções em massa de ações Mikrotik (módulo Operações).

    Toda execução iniciada no painel grava 1 row aqui ANTES de disparar os
    workers (pra ter rastro mesmo se o backend crashar no meio). Os
    resultados por device são preenchidos depois, quando cada worker termina.

    `resultados` é JSON com formato:
        {"<device_id>": {"status": "sucesso|falha", "output": "...",
                         "duracao_ms": 123}}
    """
    __tablename__ = "operacao_massa_log"
    id = Column(Integer, primary_key=True)
    # Slug da ação executada (ex.: 'checar_versao', 'remover_user', 'comando_livre').
    # Não usa Enum pra simplificar adicionar ações novas sem migração.
    acao = Column(String(40), nullable=False)
    # Usuário que disparou. SET NULL pra não quebrar histórico se o user for deletado.
    usuario_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Snapshot do nome (sobrevive a delete do user).
    usuario_nome = Column(String(120), nullable=False)
    empresa_id = Column(Integer, ForeignKey("empresas.id", ondelete="SET NULL"), nullable=True)
    # IDs dos devices alvo (snapshot — devices podem ser deletados depois).
    device_ids = Column(JSON, nullable=False)
    # Parâmetros da ação (ex.: {"username": "admin", "group": "full"}).
    # Senhas em texto NUNCA são salvas aqui — sanitizadas no router antes de gravar.
    params = Column(JSON, nullable=True)
    # Resultados por device. Preenchido quando workers terminam.
    resultados = Column(JSON, nullable=True)
    # Totais agregados pra UI mostrar sem precisar parsear `resultados`.
    total = Column(Integer, nullable=False, default=0)
    sucessos = Column(Integer, nullable=False, default=0)
    falhas = Column(Integer, nullable=False, default=0)
    iniciado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    concluido_em = Column(DateTime(timezone=True), nullable=True)


class Firmware(Base):
    """Firmware armazenado pra ser baixado por devices via FTP mirror (v2.2.0).

    Arquivo físico fica em /var/firmware/<arquivo_nome>. O nome no disco é
    sanitizado pelo router (sem path traversal, sem espaços problemáticos) e
    pode coincidir ou divergir do nome original do upload (em caso de colisão
    o router adiciona sufixo). `nome` é o display amigável que o admin
    visualiza/digita; `arquivo_nome` é o que device baixa via FTP.

    Não tem dedupe por sha256 — admin pode propositalmente subir 2 versões
    do mesmo arquivo (ex.: testando rollback). Cleanup é manual via DELETE.
    """
    __tablename__ = "firmwares"
    id = Column(Integer, primary_key=True)
    nome = Column(String(200), nullable=False)            # display amigável
    descricao = Column(Text, nullable=True)
    # Nome do arquivo no disco /var/firmware/. Único pra evitar sobrescrita.
    arquivo_nome = Column(String(255), nullable=False, unique=True)
    tamanho_bytes = Column(Integer, nullable=False)
    # SHA256 calculado durante upload — útil pra device validar integridade
    # depois do download (ex.: Mikrotik tem /file/hash). Hex lowercase.
    sha256 = Column(String(64), nullable=False)
    # Metadados opcionais — fabricante/modelo/versão pra organizar catálogo.
    # Não são enums pra permitir valores livres (firmware de fornecedor
    # custom, beta, etc.) sem precisar migração.
    fabricante = Column(String(40), nullable=True)
    modelo_alvo = Column(String(100), nullable=True)
    versao = Column(String(40), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    # Snapshot do criador (sobrevive a delete do user). NULL = upload externo
    # via FTP que foi promovido depois (caso não usado hoje, mas reservado).
    criado_por_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    criado_por_nome = Column(String(120), nullable=False)


class FirmwareOrigem(Base):
    """Credencial FTP que um cliente/device usa pra baixar firmwares.

    O username é gerado pelo backend (fwm_NNNNN) e a senha é mostrada UMA
    única vez no momento da criação/regeneração (alfanumérica, 24 chars).
    Senha é armazenada cifrada com Fernet — autorização em runtime decrypt+match.

    Diferente de Device.ftp_user (push de backup, write-only, chroot por
    device), a origem firmware tem chroot compartilhado em /var/firmware/
    com perms `elrw` (list + read + write). Uploads externos via FTP ficam
    como arquivos órfãos no painel (admin promove ou deleta).

    Sem whitelist de CIDR — geralmente quem precisa de firmware está em
    redes dinâmicas (ISPs, NATs móveis). Defesa é fail2ban + senha 24 chars
    + flag `ativo` que admin pode desligar pra revogar acesso.
    """
    __tablename__ = "firmware_origens"
    id = Column(Integer, primary_key=True)
    nome = Column(String(120), nullable=False)            # display amigável (ex.: "Filial Brasília")
    descricao = Column(Text, nullable=True)
    usuario_ftp = Column(String(64), nullable=False, unique=True)
    senha_ftp_enc = Column(Text, nullable=False)          # Fernet
    ativo = Column(Boolean, nullable=False, default=True)
    # Empresa opcional — origem pode ser global (NULL) ou amarrada a uma
    # empresa específica. Sem efeito no FTP auth atualmente (todas as origens
    # veem o mesmo /var/firmware/), só pra UI segmentar quem cadastrou.
    empresa_id = Column(Integer, ForeignKey("empresas.id", ondelete="SET NULL"), nullable=True)
    # Telemetria: último uso do FTP (atualizado em validate_authentication ok)
    ultimo_acesso_em = Column(DateTime(timezone=True), nullable=True)
    ultimo_ip = Column(String(45), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    criado_por_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    criado_por_nome = Column(String(120), nullable=False)


class LogScheduler(Base):
    __tablename__ = "log_scheduler"
    id = Column(Integer, primary_key=True)
    inicio = Column(DateTime(timezone=True), nullable=False)
    fim = Column(DateTime(timezone=True), nullable=True)
    total = Column(Integer, nullable=True)
    sucessos = Column(Integer, nullable=True)
    falhas = Column(Integer, nullable=True)
    erro_geral = Column(Text, nullable=True)
    # Métricas v2 (delay adaptativo) — preenchidas pelo scheduler quando termina:
    # - duracao_total_segundos: tempo total da janela (incluindo delays entre devices)
    # - duracao_media_segundos: média de duração POR DEVICE (coleta pura, sem delays)
    # - picos_detectados: número de devices que levaram > N× a média histórica deles
    # - alertas_tamanho: número de devices com backup < 50% do último sucesso
    # NULL nos campos novos = log pré-feature.
    duracao_total_segundos = Column(Integer, nullable=True)
    duracao_media_segundos = Column(Float, nullable=True)
    picos_detectados = Column(Integer, nullable=True)
    alertas_tamanho = Column(Integer, nullable=True)
    # Métricas de paralelismo adaptativo (v2.x do scheduler):
    # - workers_max_atingido_api/ssh: pico de concorrência alcançado em cada pool
    # - tempo_sob_stress_seg: segundos da janela em que CPU/RAM ficaram >limite
    # NULL = log pré-feature ou rodado com backup_workers_auto=False.
    workers_max_atingido_api = Column(Integer, nullable=True)
    workers_max_atingido_ssh = Column(Integer, nullable=True)
    tempo_sob_stress_seg = Column(Integer, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
