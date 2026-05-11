"""Servidor SFTP embutido — versão criptografada do FTP push.

Roda em uma thread daemon usando paramiko como SSH server. Compartilha
auth log, push_backup helper e DBSession síncrona com o servidor FTP.

Camadas de defesa idênticas ao FTP:
- IP de origem precisa casar com Device.ftp_origem_cidr
- Senha valida contra Device.ftp_senha_enc (Fernet decrypt)
- Permissões: APENAS upload (open com flag de write). list/stat/remove/
  mkdir/rename/symlink retornam SFTP_PERMISSION_DENIED.
- Tamanho máximo: 5MB
- Após upload válido: chama processar_upload (mesmo dedupe diário, retenção
  e audit do FTP)

Diferenças do FTP:
- Transporte criptografado por SSH (chave de host Ed25519/RSA persistida)
- Porta externa 22 (mapeada no docker-compose para 2222 do container).
  SSH do host migrou para 2288 em 2026-04-27 para liberar a 22 default —
  necessário porque a maioria dos equipamentos (OLTs Huawei VRP, ZTE,
  switches em geral) não aceita porta SFTP custom no comando de backup,
  então padronizamos em 22 pra todos os fabricantes.
- Cada conexão roda em thread separada

Compatibilidade com equipamento legado:
- Huawei VRP (MA5800, MA5680T, MA5683T) usa KEX/HMAC/cipher antigos que
  paramiko 4.0 desabilitou por default. As listas preferred_* na função
  _handle_client reativam esses algoritmos. Sem isso, KEX falha com
  "Expecting packet from (30,), got 34" — assinatura de cliente VRP
  forçando diffie-hellman-group-exchange-sha1.
"""
import os
import socket
import stat as stat_mod
import threading
import logging
import paramiko
from sqlalchemy import select
from models import Device, Protocolo
from services.crypto import decrypt
from services.ftp_server import (
    auth_log, _ip_match, MAX_FILE_SIZE,
    _max_file_size_para, ler_arquivo_pra_persistir,
)
from services.push_backup import (
    SyncSessionLocal, processar_upload, auditar_acesso_negado,
    auditar_falha_push,
)

log = logging.getLogger("nexus.sftp")

SFTP_HOST_KEY_PATH = "/var/lib/nexus/sftp_host_key"
SFTP_PORT = 2222
SFTP_UPLOAD_DIR = "/var/ftp/sftp-uploads"


def _load_or_create_host_key() -> paramiko.PKey:
    """Carrega a chave de host SFTP do disco; gera RSA-2048 se não existir.
    Persistir é importante — equipamentos guardam fingerprint da primeira
    conexão e reclamam se mudar a cada restart."""
    os.makedirs(os.path.dirname(SFTP_HOST_KEY_PATH), exist_ok=True)
    if os.path.isfile(SFTP_HOST_KEY_PATH):
        try:
            return paramiko.RSAKey.from_private_key_file(SFTP_HOST_KEY_PATH)
        except Exception as e:
            log.warning("Host key existente inválida (%s) — gerando nova", e)
    key = paramiko.RSAKey.generate(2048)
    key.write_private_key_file(SFTP_HOST_KEY_PATH)
    log.info("Nova host key SFTP gerada em %s", SFTP_HOST_KEY_PATH)
    return key


class NexusSSHServerInterface(paramiko.ServerInterface):
    """Interface do SSH (não SFTP) — cuida da autenticação e abertura do canal."""

    def __init__(self, client_ip: str):
        self.client_ip = client_ip
        self.username: str | None = None
        self.device_id: int | None = None
        self.device_empresa_id: int | None = None
        self.device_nome: str | None = None
        self.event = threading.Event()

    def get_allowed_auths(self, username):
        return "password"

    def homedir(self) -> str | None:
        """Diretório real no disco onde o user pode escrever.
        None se o user não foi autenticado ainda."""
        if not self.username:
            return None
        return os.path.join(SFTP_UPLOAD_DIR, self.username)

    def check_auth_password(self, username, password):
        with SyncSessionLocal() as db:
            dev = db.execute(
                select(Device).where(Device.ftp_user == username)
            ).scalar_one_or_none()
            # Aceita protocolo sftp_push (fluxo push original) E api (Mikrotik
            # que recebe /tool/fetch upload do backend pra empurrar o /export).
            # Em ambos, ftp_user/ftp_senha são auto-gerados ao criar o device.
            protocolos_aceitos = (Protocolo.sftp_push, Protocolo.api)
            if not dev or not dev.ativo or dev.protocolo not in protocolos_aceitos or not dev.ftp_senha_enc:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=user_inexistente", self.client_ip, username)
                auditar_acesso_negado(db, self.client_ip, alvo_nome=username, empresa_id=None,
                                      detalhe="usuário SFTP inexistente ou device desabilitado",
                                      protocolo="SFTP")
                return paramiko.AUTH_FAILED
            try:
                senha_real = decrypt(dev.ftp_senha_enc)
            except Exception:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=decrypt_error", self.client_ip, username)
                return paramiko.AUTH_FAILED
            if password != senha_real:
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=senha_invalida", self.client_ip, username)
                auditar_acesso_negado(db, self.client_ip, alvo_nome=username, empresa_id=dev.empresa_id,
                                      detalhe="senha incorreta", protocolo="SFTP")
                return paramiko.AUTH_FAILED
            # Whitelist IP só vale pra sftp_push (fluxo onde o admin configura
            # explicitamente o CIDR de origem). Pra protocolo=api, o backend
            # dispara o upload sob demanda — origem pode ser qualquer IP
            # roteável que o Mikrotik tenha (LAN, túnel, NAT) e a credencial
            # única gerada por device já garante autorização.
            if dev.protocolo == Protocolo.sftp_push and not _ip_match(self.client_ip, dev.ftp_origem_cidr):
                auth_log.warning("AUTH_FAIL ip=%s user=%s reason=ip_fora_whitelist", self.client_ip, username)
                auditar_acesso_negado(db, self.client_ip, alvo_nome=username, empresa_id=dev.empresa_id,
                                      detalhe=f"IP {self.client_ip} fora da whitelist {dev.ftp_origem_cidr}",
                                      protocolo="SFTP")
                return paramiko.AUTH_FAILED
        self.username = username
        self.device_id = dev.id
        self.device_empresa_id = dev.empresa_id
        self.device_nome = dev.nome
        # Garante que o homedir do user existe no disco — equipamentos como
        # Huawei OLT verificam o diretório antes de abrir o arquivo pra escrever.
        try:
            os.makedirs(self.homedir(), exist_ok=True)
        except Exception:
            log.exception("Falha ao criar homedir SFTP para %s", username)
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class NexusSFTPHandle(paramiko.SFTPHandle):
    """Handle de upload — escreve direto no homedir do user e dispara o
    processamento quando o cliente fecha o handle. O arquivo é APAGADO
    após processar (o conteúdo vai pro Backup row no banco)."""

    def __init__(self, server_iface: "NexusSFTPServerInterface",
                 real_path: str, flags: int):
        super().__init__(flags)
        self.server_iface = server_iface
        self.real_path = real_path
        os.makedirs(os.path.dirname(real_path), exist_ok=True)
        self.f = open(real_path, "wb+")
        self.writefile = self.f
        self.readfile = self.f  # alguns clientes abrem RDWR
        self.fechou = False

    def close(self):
        if self.fechou:
            return
        self.fechou = True
        try:
            self.f.flush()
            self.f.close()
            self.server_iface.processar_upload_local(self.real_path)
        except Exception as e:
            log.exception("Falha ao processar upload SFTP de %s",
                          self.server_iface.ssh_server.username)
            try:
                with SyncSessionLocal() as db:
                    dev = db.execute(
                        select(Device).where(Device.id == self.server_iface.ssh_server.device_id)
                    ).scalar_one_or_none()
                    auditar_falha_push(
                        db, dev, self.server_iface.ssh_server.client_ip,
                        motivo=f"erro ao processar: {type(e).__name__}",
                        protocolo="SFTP",
                        nome_arquivo=os.path.basename(self.real_path),
                    )
            except Exception:
                log.exception("audit de falha SFTP também falhou — segue silencioso")
        finally:
            try:
                os.unlink(self.real_path)
            except OSError:
                pass
            super().close()


class NexusSFTPServerInterface(paramiko.SFTPServerInterface):
    """SFTP com chroot virtual — todas as operações ficam restritas ao
    homedir do user (/var/ftp/sftp-uploads/<ftp_user>/). Permitido:
    open-write, list_folder, stat, lstat, mkdir (transparente).
    Negado: read, remove, rmdir, rename, symlink, chattr."""

    def __init__(self, server, *args, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.ssh_server: NexusSSHServerInterface = server
        # Files que foram abertos pra escrita nesta sessão. Usado por
        # session_ended() pra processar uploads quando o cliente fecha a
        # conexão SSH abruptamente sem enviar SSH_FXP_CLOSE — caso típico
        # de ZTE C320 com `file-server manual-backup`. Sem esse fallback,
        # o file fica órfão no disco e nunca chega no banco.
        self._dirty_files: set[str] = set()

    def _homedir(self) -> str:
        d = self.ssh_server.homedir()
        if not d:
            raise paramiko.SFTPError(paramiko.SFTP_PERMISSION_DENIED, "no homedir")
        os.makedirs(d, exist_ok=True)
        return d

    def _real_path(self, sftp_path: str) -> str | None:
        """Mapeia path do cliente pra path real no disco, preso ao homedir.
        Rejeita parent traversal."""
        try:
            home = self._homedir()
        except Exception:
            return None
        # path do cliente: '/' = homedir, 'arq.cfg' ou '/arq.cfg' = home/arq.cfg
        clean = sftp_path.lstrip("/").replace("\\", "/")
        if clean == "" or clean == ".":
            return home
        if ".." in clean.split("/"):
            return None
        candidato = os.path.normpath(os.path.join(home, clean))
        # Garante que o resultado AINDA está dentro do homedir (defesa
        # contra symlinks ou paths absolutos malformados)
        if not (candidato == home or candidato.startswith(home + os.sep)):
            return None
        return candidato

    def open(self, path, flags, attr):
        # Aceita só abertura para escrita. Read-only é negado.
        if not (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
            return paramiko.SFTP_PERMISSION_DENIED
        real = self._real_path(path)
        if real is None:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            handle = NexusSFTPHandle(self, real, flags)
            self._dirty_files.add(real)
            return handle
        except Exception:
            log.exception("Falha ao abrir handle de upload")
            return paramiko.SFTP_FAILURE

    def session_ended(self):
        """Hook chamado pelo paramiko quando o subsystem SFTP termina.

        Processa qualquer file que foi escrito nesta sessão e ainda existe
        no disco — caso o cliente tenha fechado a conexão sem mandar
        SSH_FXP_CLOSE (ZTE C320 com `file-server manual-backup` faz isso).
        Sem esse fallback, o file órfão fica indefinidamente em disco e
        nunca chega na tabela de backups.

        Em fluxo normal (cliente envia CLOSE), o NexusSFTPHandle.close()
        já chamou processar_upload_local e deletou o file — `os.path.exists`
        retorna False, e nós pulamos. Idempotente.
        """
        try:
            for fpath in list(self._dirty_files):
                try:
                    if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                        log.warning(
                            "SFTP %s: cliente fechou sem CLOSE no handle — "
                            "processando %s no session_ended fallback",
                            self.ssh_server.client_ip, fpath,
                        )
                        self.processar_upload_local(fpath)
                        try:
                            os.unlink(fpath)
                        except OSError:
                            pass
                except Exception:
                    log.exception("session_ended: falha processando %s", fpath)
            self._dirty_files.clear()
        finally:
            super().session_ended()

    def list_folder(self, path):
        real = self._real_path(path)
        if real is None or not os.path.isdir(real):
            return paramiko.SFTP_NO_SUCH_FILE
        try:
            entries = []
            for nome in os.listdir(real):
                full = os.path.join(real, nome)
                attr = paramiko.SFTPAttributes.from_stat(os.stat(full))
                attr.filename = nome
                entries.append(attr)
            return entries
        except Exception:
            return paramiko.SFTP_FAILURE

    def stat(self, path):
        real = self._real_path(path)
        if real is None:
            return paramiko.SFTP_NO_SUCH_FILE
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(real))
        except FileNotFoundError:
            return paramiko.SFTP_NO_SUCH_FILE
        except Exception:
            return paramiko.SFTP_FAILURE

    def lstat(self, path):
        return self.stat(path)

    def mkdir(self, path, attr):
        # Aceita transparente — alguns clientes criam dir antes de subir.
        # Como o homedir já existe, é no-op em 99% dos casos.
        real = self._real_path(path)
        if real is None:
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            os.makedirs(real, exist_ok=True)
            return paramiko.SFTP_OK
        except Exception:
            return paramiko.SFTP_FAILURE

    def processar_upload_local(self, real_path: str):
        nome_arquivo = os.path.basename(real_path)
        try:
            tamanho = os.path.getsize(real_path)
        except OSError:
            return
        if tamanho == 0:
            log.warning("SFTP upload de %s rejeitado: tamanho=0",
                        self.ssh_server.username)
            with SyncSessionLocal() as db:
                dev = db.execute(
                    select(Device).where(Device.id == self.ssh_server.device_id)
                ).scalar_one_or_none()
                auditar_falha_push(db, dev, self.ssh_server.client_ip,
                                   motivo="arquivo vazio (0 bytes)",
                                   protocolo="SFTP", nome_arquivo=nome_arquivo)
            return
        with SyncSessionLocal() as db:
            dev = db.execute(
                select(Device).where(Device.id == self.ssh_server.device_id)
            ).scalar_one_or_none()
            if not dev:
                return
            limite = _max_file_size_para(dev)
            if tamanho > limite:
                log.warning("SFTP upload de %s rejeitado: tamanho=%s > limite=%s",
                            self.ssh_server.username, tamanho, limite)
                auditar_falha_push(
                    db, dev, self.ssh_server.client_ip,
                    motivo=f"tamanho excedido ({tamanho} bytes > limite {limite})",
                    protocolo="SFTP", nome_arquivo=nome_arquivo,
                )
                return
            # Preserva o nome original que o cliente enviou — chave pra UNM2000
            # diferenciar arquivos de OLTs distintas que chegam com mesma cred.
            conteudo = ler_arquivo_pra_persistir(real_path, nome_arquivo)

            # Plano C de coleta via API Mikrotik: se há run_backup_via_api
            # esperando upload desse device, entrega conteúdo direto na fila
            # e PULA o processar_upload (que aplicaria dedupe diário e
            # apagaria backups históricos). O caller cria backup normal
            # com origem='manual'.
            from services.mikrotik_api import has_pending_api_upload, deliver_api_upload
            if has_pending_api_upload(dev.id) and deliver_api_upload(dev.id, conteudo):
                log.info("SFTP %s: upload entregue ao Plano C da coleta API (device %s)",
                         self.ssh_server.client_ip, dev.id)
                return

            processar_upload(dev, conteudo, self.ssh_server.client_ip, tamanho, db,
                             nome_arquivo=nome_arquivo, protocolo="SFTP")
            db.commit()

    # Operações ainda negadas — SFTP é write-only do ponto de vista do cliente.
    def remove(self, path): return paramiko.SFTP_PERMISSION_DENIED
    def rmdir(self, path): return paramiko.SFTP_PERMISSION_DENIED
    def chattr(self, path, attr): return paramiko.SFTP_PERMISSION_DENIED
    def rename(self, oldpath, newpath): return paramiko.SFTP_PERMISSION_DENIED
    def readlink(self, path): return paramiko.SFTP_PERMISSION_DENIED
    def symlink(self, target_path, path): return paramiko.SFTP_PERMISSION_DENIED


def _aplicar_compat_legacy(transport: paramiko.Transport) -> None:
    """Reativa KEX/ciphers/MACs legados que paramiko 4.0 desabilitou por default.

    Necessário para que OLTs Huawei VRP (MA5800/MA5680T) consigam negociar.
    Mantém os algoritmos modernos no topo da lista — paramiko escolhe o
    primeiro que ambos os lados suportam, então não há perda de segurança
    em conexões com clientes modernos.

    Validado em 2026-04-27 com Huawei OLT MA5800 (firmware Gaia_X2) — sem
    este patch, paramiko 4.0 quebra o handshake com erro:
    'Expecting packet from (30,), got 34' (cliente VRP força DH-GEX-sha1).

    Implementação: paramiko 4.0 tornou as properties preferred_* read-only.
    A forma correta de configurar listas de algoritmos é via
    get_security_options() (digests = MACs, key_types = host key + pubkey).
    Algoritmos desconhecidos pelo paramiko lançam ValueError, então
    filtramos as listas pelo que ele realmente registra (_kex_info etc.) —
    sobrevive upgrades futuros que removam algos legados completamente.
    """
    # Atributos com todas as algos conhecidas (mesmo as desabilitadas por default).
    # Estáveis nas versões 2.x→4.x do paramiko.
    known_kex = set(paramiko.Transport._kex_info.keys())
    known_ciphers = set(paramiko.Transport._cipher_info.keys())
    known_macs = set(paramiko.Transport._mac_info.keys())
    known_keys = set(paramiko.Transport._key_info.keys())

    # NOTA: 'diffie-hellman-group-exchange-sha1' e 'diffie-hellman-group-exchange-sha256'
    # estão registrados em paramiko._kex_info mas a implementação server-side em paramiko
    # 4.0 está quebrada (validado em 2026-04-27 com smoke test: client OpenSSH negociava
    # GEX-sha1 e a sessão fechava na troca DH_GEX_GROUP). Não inclua eles aqui — paramiko
    # escolheria por ordem e falharia. Felizmente VRP da Huawei também aceita group14-sha1
    # como fallback, que é a próxima preferência de equipamento legado.
    desired_kex = (
        # Modernos (preferidos)
        "curve25519-sha256",
        "curve25519-sha256@libssh.org",
        "ecdh-sha2-nistp256",
        "ecdh-sha2-nistp384",
        "ecdh-sha2-nistp521",
        "diffie-hellman-group16-sha512",
        "diffie-hellman-group14-sha256",
        # Legacy fixed-group DH para Huawei VRP / equipamento antigo
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
    )
    desired_ciphers = (
        # Modernos
        "aes128-ctr", "aes192-ctr", "aes256-ctr",
        "aes128-gcm@openssh.com", "aes256-gcm@openssh.com",
        # Legacy CBC para Huawei VRP
        "aes128-cbc", "aes192-cbc", "aes256-cbc",
        "3des-cbc",
    )
    # 'digests' no paramiko = MACs (HMACs aplicados após cifra)
    desired_macs = (
        # Modernos
        "hmac-sha2-256-etm@openssh.com",
        "hmac-sha2-512-etm@openssh.com",
        "hmac-sha2-256",
        "hmac-sha2-512",
        # Legacy
        "hmac-sha1",
        "hmac-sha1-96",
        "hmac-md5",
        "hmac-md5-96",
    )
    # 'key_types' = algoritmos de assinatura aceitos para host key / client pubkey.
    # Inclui ssh-rsa (SHA-1) porque alguns VRP só assinam com isso.
    desired_keys = (
        "ssh-ed25519",
        "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
        "rsa-sha2-512", "rsa-sha2-256",
        "ssh-rsa",
    )

    opts = transport.get_security_options()
    opts.kex = tuple(k for k in desired_kex if k in known_kex)
    opts.ciphers = tuple(c for c in desired_ciphers if c in known_ciphers)
    opts.digests = tuple(m for m in desired_macs if m in known_macs)
    opts.key_types = tuple(k for k in desired_keys if k in known_keys)


def _handle_client(client_sock: socket.socket, client_addr: tuple, host_key: paramiko.PKey):
    client_ip = client_addr[0]
    transport = None
    try:
        transport = paramiko.Transport(client_sock)
        _aplicar_compat_legacy(transport)
        transport.add_server_key(host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, NexusSFTPServerInterface)
        ssh_iface = NexusSSHServerInterface(client_ip=client_ip)
        try:
            transport.start_server(server=ssh_iface)
        except paramiko.SSHException:
            log.warning("Negociação SSH falhou com %s", client_ip)
            return
        # Espera o canal ser aberto + autenticado + subsystem ativado.
        chan = transport.accept(60)
        if chan is None:
            return
        # Mantém a conexão viva enquanto o cliente faz uploads.
        while transport.is_active():
            chan.event.wait(timeout=60)
            if not chan.event.is_set():
                continue
            break
    except Exception:
        log.exception("Erro na conexão SFTP de %s", client_ip)
    finally:
        try:
            if transport:
                transport.close()
        except Exception:
            pass
        try:
            client_sock.close()
        except Exception:
            pass


_listener_socket: socket.socket | None = None
_listener_thread: threading.Thread | None = None
_stopping = False


def _accept_loop(host_key: paramiko.PKey):
    global _stopping
    assert _listener_socket is not None
    log.info("SFTP server escutando em 0.0.0.0:%d", SFTP_PORT)
    while not _stopping:
        try:
            client_sock, client_addr = _listener_socket.accept()
        except OSError:
            if _stopping:
                return
            continue
        threading.Thread(
            target=_handle_client, args=(client_sock, client_addr, host_key),
            name=f"sftp-{client_addr[0]}", daemon=True,
        ).start()


def iniciar_sftp_server() -> None:
    """Inicia o servidor SFTP em thread daemon. Idempotente."""
    global _listener_socket, _listener_thread, _stopping
    if _listener_thread is not None and _listener_thread.is_alive():
        return
    os.makedirs(SFTP_UPLOAD_DIR, exist_ok=True)
    host_key = _load_or_create_host_key()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", SFTP_PORT))
    except OSError as e:
        log.error("Não foi possível bind em 0.0.0.0:%d — %s", SFTP_PORT, e)
        return
    sock.listen(64)
    _listener_socket = sock
    _stopping = False
    _listener_thread = threading.Thread(target=_accept_loop, args=(host_key,),
                                        name="sftp-listener", daemon=True)
    _listener_thread.start()


def parar_sftp_server() -> None:
    global _listener_socket, _stopping
    _stopping = True
    if _listener_socket is not None:
        try:
            _listener_socket.close()
        except Exception:
            pass
        _listener_socket = None
