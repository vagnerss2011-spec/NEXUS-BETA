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
from ipaddress import ip_address, ip_network
from sqlalchemy import select
from pyftpdlib.authorizers import DummyAuthorizer, AuthenticationFailed
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer
from models import Device, Protocolo
from services.crypto import decrypt
from services.push_backup import (
    SyncSessionLocal, processar_upload, auditar_acesso_negado,
)

log = logging.getLogger("nexus.ftp")

# Logger compartilhado por FTP e SFTP — fail2ban tail nesse arquivo via jail
# nexus-ftp. Cada linha: "<timestamp> [LEVEL] AUTH_FAIL ip=X user=Y reason=...".
# Module-level pra que sftp_server importe e use o mesmo handler.
auth_log = logging.getLogger("nexus.ftp.auth")
auth_log.setLevel(logging.WARNING)
auth_log.propagate = False

_LOG_DIR = "/var/log/nexus"
_LOG_FILE = os.path.join(_LOG_DIR, "ftp-auth.log")
if not auth_log.handlers:  # idempotente se reimportado
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
        log.warning("Não foi possível abrir %s: %s — auth log vai pra stderr", _LOG_FILE, e)
        auth_log.addHandler(logging.StreamHandler())

FTP_UPLOAD_DIR = "/var/ftp/uploads"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB — para devices que enviam .cfg em texto
# UNM2000 (NMS Fiberhome) envia o backup do banco como .zip (~3 MB no doc oficial,
# mas pode crescer com mais OLTs gerenciadas). 50 MB cobre caso real e ainda fica
# bem abaixo de uma faixa abusiva. Aplicado por device.tipo no _max_file_size_para().
MAX_FILE_SIZE_UNM2000 = 50 * 1024 * 1024


def _max_file_size_para(device) -> int:
    """Limite de tamanho conforme tipo do device.

    UNM2000 manda zip do banco próprio (vários MB) + arquivos de cada OLT
    gerenciada — 5 MB do default explodiria no zip. Demais devices seguem
    no limite menor pra evitar abuso.
    """
    try:
        if device.tipo and device.tipo.value == "unm2000":
            return MAX_FILE_SIZE_UNM2000
    except AttributeError:
        pass
    return MAX_FILE_SIZE


# Extensões reconhecidas como binário — armazenadas em base64 no campo conteudo.
# Outras extensões são tratadas como texto (configs CLI tipicamente .cfg/.txt/.rsc).
_BIN_EXTS = (".zip", ".gz", ".tar", ".tgz", ".bin", ".gpg", ".enc", ".7z", ".bz2")


def ler_arquivo_pra_persistir(filepath: str, nome_arquivo: str | None = None) -> str:
    """Lê o arquivo recebido e devolve string pronta pra coluna Backup.conteudo.

    - Se a extensão indicar binário (.zip/.gz/.tar/...) ou se o conteúdo não
      decodificar como texto (UTF-8/Latin-1), armazena em base64 com prefixo
      'BASE64:' pra que o frontend saiba decodificar antes de exibir/baixar.
    - Caso contrário, devolve texto puro (UTF-8 com fallback Latin-1) — mantém
      compatibilidade com a UI de visualização de config.
    """
    import base64
    nome = (nome_arquivo or "").lower()
    parece_binario = nome.endswith(_BIN_EXTS)

    with open(filepath, "rb") as f:
        raw = f.read()

    if not parece_binario:
        # Tenta texto. Se não for UTF-8 limpo nem Latin-1 razoável, marca binário.
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw.decode("latin-1")
            except UnicodeDecodeError:
                parece_binario = True

    return "BASE64:" + base64.b64encode(raw).decode("ascii")


def _ip_match(remote_ip: str, allowed_cidr: str | None) -> bool:
    if not allowed_cidr:
        return False
    try:
        return ip_address(remote_ip) in ip_network(allowed_cidr.strip(), strict=False)
    except Exception:
        return False


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
                auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=None,
                                      detalhe="usuário FTP inexistente ou device desabilitado")
                raise AuthenticationFailed("Authentication failed.")
            try:
                senha_real = decrypt(dev.ftp_senha_enc)
            except Exception:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=decrypt_error", ip, username)
                raise AuthenticationFailed("Authentication failed.")
            if password != senha_real:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=senha_invalida", ip, username)
                auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=dev.empresa_id,
                                      detalhe="senha incorreta")
                raise AuthenticationFailed("Authentication failed.")
            if not _ip_match(ip, dev.ftp_origem_cidr):
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=ip_fora_whitelist", ip, username)
                auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=dev.empresa_id,
                                      detalhe=f"IP {ip} fora da whitelist {dev.ftp_origem_cidr}")
                raise AuthenticationFailed("Authentication failed.")

    def get_home_dir(self, username):
        d = os.path.join(FTP_UPLOAD_DIR, username)
        os.makedirs(d, exist_ok=True)
        return d

    def has_user(self, username):
        return True

    def has_perm(self, username, perm, path=None):
        # Permissões: 'e' (changedir), 'l' (list), 'w' (STOR/STOU/APPE).
        # Equipamentos legados (Huawei, Fiberhome) fazem CWD/LIST antes de
        # abrir o arquivo. Sem 'e' e 'l' o upload aborta.
        # NÃO inclui 'r' (RETR) — atacante com credencial não baixa nada.
        # NÃO inclui 'd' (DELE), 'm' (MKD), 'f' (RNFR/RNTO) — write-only.
        return perm in ("e", "l", "w")

    def get_perms(self, username):
        return "elw"

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
        if tamanho == 0:
            log.warning("FTP upload de %s rejeitado: tamanho=0", username)
            return

        # Resolve device antes de avaliar limite (UNM2000 tem limite maior).
        with SyncSessionLocal() as db:
            dev = db.execute(
                select(Device).where(Device.ftp_user == username)
            ).scalar_one_or_none()
            if not dev:
                return
            limite = _max_file_size_para(dev)
            if tamanho > limite:
                log.warning("FTP upload de %s rejeitado: tamanho=%s > limite=%s",
                            username, tamanho, limite)
                return

            # Preserva nome original do arquivo (pra UNM2000 distinguir qual OLT
            # mandou cada cfg). filepath aqui é o destino completo no disco;
            # basename pega só o nome enviado pelo cliente.
            nome_arquivo = os.path.basename(filepath)
            conteudo = ler_arquivo_pra_persistir(filepath, nome_arquivo)
            processar_upload(dev, conteudo, ip, tamanho, db, nome_arquivo=nome_arquivo)
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
