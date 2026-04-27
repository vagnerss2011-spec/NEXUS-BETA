"""Servidor FTP embutido para receber backups de equipamentos que enviam push.

Roda em uma thread daemon dentro do mesmo processo do uvicorn. Compartilha
o banco com o resto do app, mas via engine SÍNCRONO separado (psycopg2)
porque pyftpdlib é threaded e mistura mal com o SQLAlchemy async do FastAPI.

Camadas de defesa:
- IP de origem precisa casar com o ftp_origem_cidr cadastrado no Device
- Senha é validada por Fernet decrypt + comparação direta
- Permissão única: 'w' (STOR). LIST/RETR/DELE/MKD são todos negados
- Tamanho máximo de arquivo: 5MB (configurável aqui)
- Após upload válido: cria Backup row, dedupe diário, retenção
- Alerta de volume alto: >5 uploads em 24h dispara atividade ftp_volume_alto
"""
import os
import threading
import logging
import logging.handlers
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import sessionmaker, Session
from pyftpdlib.authorizers import DummyAuthorizer, AuthenticationFailed
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer
from config import settings
from models import Device, Backup, Atividade, TipoAtividade, Protocolo
from services.crypto import decrypt

log = logging.getLogger("nexus.ftp")

# Logger dedicado pra falhas de autenticação. Escreve em arquivo dentro de
# /var/log/nexus (volume montado do host) num formato simples que o fail2ban
# consegue parsear via regex. Cada linha: "<timestamp> [LEVEL] AUTH_FAIL ip=X user=Y reason=...".
auth_log = logging.getLogger("nexus.ftp.auth")
auth_log.setLevel(logging.WARNING)
auth_log.propagate = False  # não duplica nos logs do uvicorn

_LOG_DIR = "/var/log/nexus"
_LOG_FILE = os.path.join(_LOG_DIR, "ftp-auth.log")
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=10_000_000, backupCount=3, encoding="utf-8"
    )
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    auth_log.addHandler(_handler)
except Exception as e:
    # Sem volume mount disponível: cai pra stderr (não quebra o servidor)
    log.warning("Não foi possível abrir %s: %s — auth log vai pra stderr", _LOG_FILE, e)
    auth_log.addHandler(logging.StreamHandler())

# Engine síncrono dedicado. A DATABASE_URL é configurada com +asyncpg para
# o resto do app; aqui trocamos pelo driver síncrono (psycopg2-binary já
# está no requirements).
_sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
sync_engine = create_engine(_sync_url, pool_size=5, max_overflow=10, pool_pre_ping=True)
SyncSessionLocal = sessionmaker(sync_engine, autoflush=False, autocommit=False)

FTP_UPLOAD_DIR = "/var/ftp/uploads"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
VOLUME_ALTO_LIMITE = 5            # uploads/24h que disparam alerta


def _ip_match(remote_ip: str, allowed_cidr: str | None) -> bool:
    if not allowed_cidr:
        return False
    try:
        return ip_address(remote_ip) in ip_network(allowed_cidr.strip(), strict=False)
    except Exception:
        return False


def _audit(db: Session, tipo: TipoAtividade, ip: str | None,
           alvo_nome: str | None = None, empresa_id: int | None = None,
           detalhe: str | None = None):
    db.add(Atividade(
        tipo=tipo, usuario_id=None, usuario_nome="ftp",
        empresa_id=empresa_id, ip=ip, alvo_tipo="device",
        alvo_nome=alvo_nome, detalhe=detalhe,
    ))
    db.commit()


class DBAuthorizer(DummyAuthorizer):
    """Authorizer baseado em DB. As permissões são fixas em 'w' (apenas STOR)."""

    def validate_authentication(self, username, password, handler):
        ip = handler.remote_ip
        with SyncSessionLocal() as db:
            dev = db.execute(
                select(Device).where(Device.ftp_user == username)
            ).scalar_one_or_none()
            if not dev or not dev.ativo or dev.protocolo != Protocolo.ftp_push or not dev.ftp_senha_enc:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=user_inexistente", ip, username)
                _audit(db, TipoAtividade.ftp_acesso_negado, ip, alvo_nome=username,
                       detalhe="usuário FTP inexistente ou device desabilitado")
                raise AuthenticationFailed("Authentication failed.")
            try:
                senha_real = decrypt(dev.ftp_senha_enc)
            except Exception:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=decrypt_error", ip, username)
                raise AuthenticationFailed("Authentication failed.")
            if password != senha_real:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=senha_invalida", ip, username)
                _audit(db, TipoAtividade.ftp_acesso_negado, ip, alvo_nome=username,
                       empresa_id=dev.empresa_id, detalhe="senha incorreta")
                raise AuthenticationFailed("Authentication failed.")
            if not _ip_match(ip, dev.ftp_origem_cidr):
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=ip_fora_whitelist", ip, username)
                _audit(db, TipoAtividade.ftp_acesso_negado, ip, alvo_nome=username,
                       empresa_id=dev.empresa_id,
                       detalhe=f"IP {ip} fora da whitelist {dev.ftp_origem_cidr}")
                raise AuthenticationFailed("Authentication failed.")

    def get_home_dir(self, username):
        d = os.path.join(FTP_UPLOAD_DIR, username)
        os.makedirs(d, exist_ok=True)
        return d

    def has_user(self, username):
        return True

    def has_perm(self, username, perm, path=None):
        # 'w' = STOR / STOU / APPE — único permitido
        return perm == "w"

    def get_perms(self, username):
        return "w"

    def get_msg_login(self, username):
        return "Login OK. Envie o arquivo de configuração."

    def get_msg_quit(self, username):
        return "Backup recebido. Encerrando."

    def impersonate_user(self, username, password):
        pass

    def terminate_impersonation(self, username):
        pass


class NexusFTPHandler(FTPHandler):
    """Hook do upload completo. Limite de tamanho aplicado no nível do DTPHandler
    via setting do servidor; aqui só processamos depois do arquivo no disco."""

    def on_file_received(self, filepath):
        try:
            self._processar_upload(filepath)
        except Exception:
            log.exception("Falha ao processar upload FTP de %s", self.username)
        finally:
            try:
                os.unlink(filepath)
            except OSError:
                pass

    def on_incomplete_file_received(self, filepath):
        try:
            os.unlink(filepath)
        except OSError:
            pass

    def _processar_upload(self, filepath: str):
        username = self.username
        ip = self.remote_ip
        try:
            tamanho = os.path.getsize(filepath)
        except OSError:
            return
        if tamanho == 0 or tamanho > MAX_FILE_SIZE:
            log.warning("FTP upload de %s rejeitado: tamanho=%s", username, tamanho)
            return

        with open(filepath, "r", errors="replace") as f:
            conteudo = f.read()

        with SyncSessionLocal() as db:
            dev = db.execute(
                select(Device).where(Device.ftp_user == username)
            ).scalar_one_or_none()
            if not dev:
                return

            # Dedupe diário: substitui qualquer backup do dia atual.
            agora = datetime.now(timezone.utc)
            inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
            db.execute(
                delete(Backup).where(
                    Backup.device_id == dev.id,
                    Backup.criado_em >= inicio_dia,
                )
            )

            db.add(Backup(
                device_id=dev.id, status="sucesso", conteudo=conteudo,
                erro=None, log_scheduler_id=None,
            ))

            # Retenção: mantém últimos N (BACKUP_RETENTION_DAYS), apaga o resto.
            ids_excedentes = db.execute(
                select(Backup.id)
                .where(Backup.device_id == dev.id)
                .order_by(Backup.criado_em.desc())
                .offset(settings.BACKUP_RETENTION_DAYS)
            ).scalars().all()
            if ids_excedentes:
                db.execute(delete(Backup).where(Backup.id.in_(ids_excedentes)))

            db.add(Atividade(
                tipo=TipoAtividade.ftp_backup_recebido, usuario_id=None,
                usuario_nome="ftp", empresa_id=dev.empresa_id, ip=ip,
                alvo_tipo="device", alvo_nome=dev.nome,
                detalhe=f"{tamanho} bytes",
            ))

            # Volume alto: 1 alerta por device por janela de 24h.
            corte = agora - timedelta(hours=24)
            uploads = db.execute(
                select(func.count(Atividade.id)).where(
                    Atividade.tipo == TipoAtividade.ftp_backup_recebido,
                    Atividade.alvo_nome == dev.nome,
                    Atividade.criado_em >= corte,
                )
            ).scalar() or 0
            if uploads >= VOLUME_ALTO_LIMITE:
                ja = db.execute(
                    select(Atividade.id).where(
                        Atividade.tipo == TipoAtividade.ftp_volume_alto,
                        Atividade.alvo_nome == dev.nome,
                        Atividade.criado_em >= corte,
                    )
                ).scalar_one_or_none()
                if not ja:
                    db.add(Atividade(
                        tipo=TipoAtividade.ftp_volume_alto, usuario_id=None,
                        usuario_nome="ftp", empresa_id=dev.empresa_id, ip=ip,
                        alvo_tipo="device", alvo_nome=dev.nome,
                        detalhe=f"{uploads} uploads em 24h",
                    ))

            db.commit()


_server: ThreadedFTPServer | None = None


def iniciar_ftp_server() -> ThreadedFTPServer | None:
    """Inicia o servidor FTP em thread daemon. Idempotente."""
    global _server
    if _server is not None:
        return _server
    os.makedirs(FTP_UPLOAD_DIR, exist_ok=True)

    authorizer = DBAuthorizer()
    handler = NexusFTPHandler
    handler.authorizer = authorizer
    handler.banner = "NEXUS BETA backup server ready."
    handler.passive_ports = range(30000, 30100)
    handler.use_sendfile = False
    handler.max_login_attempts = 3
    handler.tcp_no_delay = True

    server = ThreadedFTPServer(("0.0.0.0", 21), handler)
    server.max_cons = 256
    server.max_cons_per_ip = 5

    t = threading.Thread(target=server.serve_forever, name="ftp-server", daemon=True)
    t.start()
    _server = server
    log.info("FTP server iniciado em 0.0.0.0:21 (passive=30000-30099)")
    return server


def parar_ftp_server():
    global _server
    if _server is not None:
        try:
            _server.close_all()
        except Exception:
            pass
        _server = None
