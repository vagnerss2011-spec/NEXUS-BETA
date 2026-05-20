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
from datetime import datetime, timezone
from models import Device, Protocolo, FirmwareOrigem, Atividade, TipoAtividade
from services.crypto import decrypt
from services.push_backup import (
    SyncSessionLocal, processar_upload, auditar_acesso_negado,
    auditar_falha_push, _humanizar_bytes as _humanizar_bytes_local,
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
# Diretório compartilhado pelas origens de firmware (mirror FTP — v2.2.0).
# Chroot read+write das origens. Arquivos colocados aqui pelo painel (via
# router /api/firmwares) ficam catalogados em DB. Arquivos subidos via FTP
# por origens ficam "órfãos" — aparecem no painel como uploads externos
# pra admin promover ou deletar.
FTP_FIRMWARE_DIR = "/var/firmware"
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


# Cache thread-local pra discriminar o tipo de user logado entre
# validate_authentication e os métodos chamados por path (has_perm,
# get_home_dir, get_perms). pyftpdlib chama os 3 últimos sem passar o
# objeto handler, então não dá pra olhar handler.remote_ip ali — usamos
# o cache populado no auth pra responder coerentemente.
#
# Estrutura: {username: "device" | "firmware_origem"}
_USER_TIPO_CACHE: dict[str, str] = {}


class DBAuthorizer(DummyAuthorizer):
    """Authorizer baseado em DB. Suporta DOIS tipos de credencial:

    1. Device.ftp_user (push de backup, write-only, chroot por device)
    2. FirmwareOrigem.usuario_ftp (mirror firmware, read+write, chroot compartilhado)

    Ordem de lookup: primeiro tenta Device (fluxo legado, IP whitelist).
    Se não acha, tenta FirmwareOrigem (sem whitelist de IP). Falha em
    ambos → AuthenticationFailed + audit.
    """

    def validate_authentication(self, username, password, handler):
        ip = handler.remote_ip
        with SyncSessionLocal() as db:
            # ─── Tentativa 1: Device push (fluxo legado) ───
            dev = db.execute(
                select(Device).where(Device.ftp_user == username)
            ).scalar_one_or_none()
            if dev:
                self._auth_device(db, dev, password, ip, username)
                _USER_TIPO_CACHE[username] = "device"
                return

            # ─── Tentativa 2: FirmwareOrigem (mirror) ───
            origem = db.execute(
                select(FirmwareOrigem).where(FirmwareOrigem.usuario_ftp == username)
            ).scalar_one_or_none()
            if origem:
                self._auth_firmware_origem(db, origem, password, ip, username)
                _USER_TIPO_CACHE[username] = "firmware_origem"
                return

            # ─── Nenhum dos dois ───
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=user_inexistente", ip, username)
            auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=None,
                                  detalhe="usuário FTP inexistente",
                                  protocolo="FTP")
            raise AuthenticationFailed("Authentication failed.")

    def _auth_device(self, db, dev, password, ip, username):
        """Auth de Device push — fluxo original, write-only + whitelist CIDR."""
        # Aceita protocolo ftp_push (fluxo push original) E api (Mikrotik
        # que recebe /tool/fetch upload do backend pra empurrar o /export).
        # RouterOS não tem mode=sftp em /tool/fetch — só ftp/http/https/scp —
        # então Plano C usa FTP plain em vez de SFTP.
        protocolos_aceitos = (Protocolo.ftp_push, Protocolo.api)
        if not dev.ativo or dev.protocolo not in protocolos_aceitos or not dev.ftp_senha_enc:
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=device_desabilitado", ip, username)
            auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=dev.empresa_id,
                                  detalhe="device desabilitado ou protocolo inválido",
                                  protocolo="FTP")
            raise AuthenticationFailed("Authentication failed.")
        try:
            senha_real = decrypt(dev.ftp_senha_enc)
        except Exception:
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=decrypt_error", ip, username)
            raise AuthenticationFailed("Authentication failed.")
        if password != senha_real:
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=senha_invalida", ip, username)
            auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=dev.empresa_id,
                                  detalhe="senha incorreta", protocolo="FTP")
            raise AuthenticationFailed("Authentication failed.")
        # Whitelist só pra ftp_push (admin configura CIDR). Pra protocolo=api
        # pula: backend dispara o upload sob demanda, cred única por device
        # já garante autorização.
        if dev.protocolo == Protocolo.ftp_push and not _ip_match(ip, dev.ftp_origem_cidr):
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=ip_fora_whitelist", ip, username)
            auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=dev.empresa_id,
                                  detalhe=f"IP {ip} fora da whitelist {dev.ftp_origem_cidr}",
                                  protocolo="FTP")
            raise AuthenticationFailed("Authentication failed.")

    def _auth_firmware_origem(self, db, origem, password, ip, username):
        """Auth de FirmwareOrigem — sem whitelist CIDR, mas precisa estar ativa."""
        if not origem.ativo or not origem.senha_ftp_enc:
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=origem_desativada", ip, username)
            auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=origem.empresa_id,
                                  detalhe="origem firmware desativada",
                                  protocolo="FTP")
            raise AuthenticationFailed("Authentication failed.")
        try:
            senha_real = decrypt(origem.senha_ftp_enc)
        except Exception:
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=decrypt_error", ip, username)
            raise AuthenticationFailed("Authentication failed.")
        if password != senha_real:
            auth_log.warning("AUTH_FAIL ip=%s user=%s reason=senha_invalida", ip, username)
            auditar_acesso_negado(db, ip, alvo_nome=username, empresa_id=origem.empresa_id,
                                  detalhe="senha incorreta (origem firmware)", protocolo="FTP")
            raise AuthenticationFailed("Authentication failed.")
        # Telemetria — atualiza ultimo_acesso da origem
        try:
            origem.ultimo_acesso_em = datetime.now(timezone.utc)
            origem.ultimo_ip = ip
            db.commit()
        except Exception:
            log.exception("falha ao atualizar ultimo_acesso da origem firmware")
            db.rollback()

    def get_home_dir(self, username):
        # Origens de firmware: chroot compartilhado em /var/firmware/.
        # Devices push: chroot isolado por user.
        if _USER_TIPO_CACHE.get(username) == "firmware_origem":
            os.makedirs(FTP_FIRMWARE_DIR, exist_ok=True)
            return FTP_FIRMWARE_DIR
        d = os.path.join(FTP_UPLOAD_DIR, username)
        os.makedirs(d, exist_ok=True)
        return d

    def has_user(self, username):
        return True

    def has_perm(self, username, perm, path=None):
        # Origem firmware: 'e' (cd), 'l' (list), 'r' (RETR), 'w' (STOR).
        # NÃO inclui 'd' (DELE) — origem não pode apagar firmwares; só admin
        # via painel. NÃO inclui 'f' (rename), 'm' (MKD).
        if _USER_TIPO_CACHE.get(username) == "firmware_origem":
            return perm in ("e", "l", "r", "w")
        # Device push: 'e' (changedir), 'l' (list), 'w' (STOR/STOU/APPE).
        # Equipamentos legados (Huawei, Fiberhome) fazem CWD/LIST antes de
        # abrir o arquivo. Sem 'e' e 'l' o upload aborta.
        # NÃO inclui 'r' (RETR) — atacante com credencial não baixa nada.
        # NÃO inclui 'd' (DELE), 'm' (MKD), 'f' (RNFR/RNTO) — write-only.
        return perm in ("e", "l", "w")

    def get_perms(self, username):
        if _USER_TIPO_CACHE.get(username) == "firmware_origem":
            return "elrw"
        return "elw"

    def get_msg_login(self, username):
        if _USER_TIPO_CACHE.get(username) == "firmware_origem":
            return "Login OK. Acesso ao mirror de firmwares (read+write)."
        return "Login OK. Envie o arquivo de configuração."

    def get_msg_quit(self, username):
        if _USER_TIPO_CACHE.get(username) == "firmware_origem":
            return "Encerrando sessão de firmware."
        return "Backup recebido. Encerrando."

    def impersonate_user(self, username, password):
        pass

    def terminate_impersonation(self, username):
        pass


class NexusFTPHandler(FTPHandler):
    """Hook do upload completo. Limite de tamanho aplicado no nível do DTPHandler
    via setting do servidor; aqui só processamos depois do arquivo no disco."""

    def on_file_received(self, filepath):
        # Discrimina por tipo cacheado no auth — origem firmware NÃO entra no
        # fluxo de push de backup (não vira row em backups, não dispara dedupe).
        # Arquivo fica no /var/firmware/ pra admin ver como "órfão" no painel.
        if _USER_TIPO_CACHE.get(self.username) == "firmware_origem":
            self._processar_upload_firmware(filepath)
            return
        try:
            self._processar_upload(filepath)
        except Exception as e:
            log.exception("Falha ao processar upload FTP de %s", self.username)
            # Audita falha pra ficar visível no painel (se conseguir resolver o device).
            try:
                with SyncSessionLocal() as db:
                    dev = db.execute(
                        select(Device).where(Device.ftp_user == self.username)
                    ).scalar_one_or_none()
                    auditar_falha_push(
                        db, dev, self.remote_ip,
                        motivo=f"erro ao processar: {type(e).__name__}",
                        protocolo="FTP",
                        nome_arquivo=os.path.basename(filepath),
                    )
            except Exception:
                log.exception("audit de falha FTP também falhou — segue silencioso")
        finally:
            try:
                os.unlink(filepath)
            except OSError:
                pass

    def on_file_sent(self, filepath):
        """Hook após RETR completo — registra download de firmware no audit."""
        if _USER_TIPO_CACHE.get(self.username) != "firmware_origem":
            return
        try:
            nome = os.path.basename(filepath)
            tamanho = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            with SyncSessionLocal() as db:
                origem = db.execute(
                    select(FirmwareOrigem).where(FirmwareOrigem.usuario_ftp == self.username)
                ).scalar_one_or_none()
                emp = origem.empresa_id if origem else None
                db.add(Atividade(
                    tipo=TipoAtividade.firmware_baixado,
                    usuario_id=None,
                    usuario_nome=f"ftp:{self.username}",
                    empresa_id=emp,
                    ip=self.remote_ip,
                    alvo_tipo="firmware",
                    alvo_nome=nome,
                    detalhe=f"FTP · download · {_humanizar_bytes_local(tamanho)}",
                ))
                db.commit()
        except Exception:
            log.exception("Audit firmware_baixado falhou — segue silencioso")

    def _processar_upload_firmware(self, filepath):
        """Upload feito por origem firmware — fica como arquivo órfão no diretório.
        Registra atividade e NÃO apaga o arquivo (admin promove/deleta pelo painel)."""
        try:
            nome = os.path.basename(filepath)
            tamanho = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            with SyncSessionLocal() as db:
                origem = db.execute(
                    select(FirmwareOrigem).where(FirmwareOrigem.usuario_ftp == self.username)
                ).scalar_one_or_none()
                emp = origem.empresa_id if origem else None
                db.add(Atividade(
                    tipo=TipoAtividade.firmware_enviado,
                    usuario_id=None,
                    usuario_nome=f"ftp:{self.username}",
                    empresa_id=emp,
                    ip=self.remote_ip,
                    alvo_tipo="firmware",
                    alvo_nome=nome,
                    detalhe=f"FTP · upload externo · {_humanizar_bytes_local(tamanho)}",
                ))
                db.commit()
            log.info("FTP firmware: upload de %s (%s) por %s @ %s — ficou como órfão",
                     nome, _humanizar_bytes_local(tamanho), self.username, self.remote_ip)
        except Exception:
            log.exception("Falha ao registrar upload órfão de firmware")

    def on_incomplete_file_received(self, filepath):
        try:
            os.unlink(filepath)
        except OSError:
            pass

    def _processar_upload(self, filepath: str):
        username = self.username
        ip = self.remote_ip
        nome_arquivo = os.path.basename(filepath)
        try:
            tamanho = os.path.getsize(filepath)
        except OSError:
            return
        if tamanho == 0:
            log.warning("FTP upload de %s rejeitado: tamanho=0", username)
            with SyncSessionLocal() as db:
                dev = db.execute(
                    select(Device).where(Device.ftp_user == username)
                ).scalar_one_or_none()
                auditar_falha_push(db, dev, ip, motivo="arquivo vazio (0 bytes)",
                                   protocolo="FTP", nome_arquivo=nome_arquivo)
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
                auditar_falha_push(
                    db, dev, ip,
                    motivo=f"tamanho excedido ({tamanho} bytes > limite {limite})",
                    protocolo="FTP", nome_arquivo=nome_arquivo,
                )
                return

            # Preserva nome original do arquivo (pra UNM2000 distinguir qual OLT
            # mandou cada cfg). filepath aqui é o destino completo no disco;
            # basename pega só o nome enviado pelo cliente.
            conteudo = ler_arquivo_pra_persistir(filepath, nome_arquivo)

            # Plano C de coleta via API Mikrotik (mesmo hook do SFTP server):
            # se há run_backup_via_api esperando upload, entrega conteúdo direto
            # na fila e pula processar_upload (que aplicaria dedupe diário e
            # apagaria backups históricos). Caller cria backup origem='manual'.
            from services.mikrotik_api import has_pending_api_upload, deliver_api_upload
            if has_pending_api_upload(dev.id) and deliver_api_upload(dev.id, conteudo):
                log.info("FTP %s: upload entregue ao Plano C da coleta API (device %s)",
                         ip, dev.id)
                return

            processar_upload(dev, conteudo, ip, tamanho, db,
                             nome_arquivo=nome_arquivo, protocolo="FTP")
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
    # Em container Docker bridge, pyftpdlib veria local IP como 172.18.x.x e
    # mandaria o cliente conectar nesse IP privado — quebra a conexão de
    # dados PASV (control OK, dados falham → arquivo de 0 bytes). Settar o
    # IP público/roteável faz o PASV funcionar. Configurável via env.
    from config import settings as _settings
    if _settings.FTP_MASQUERADE_ADDRESS:
        handler.masquerade_address = _settings.FTP_MASQUERADE_ADDRESS
        log.info("FTP PASV masquerade address: %s", _settings.FTP_MASQUERADE_ADDRESS)

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
