"""Servidor TFTP minimal (RFC 1350 + RFC 2348 blksize).

TFTP é o protocolo "burro" — UDP, sem auth, sem login. A única defesa
real é IP whitelist via Device.ftp_origem_cidr (forçado /32 no router pra
evitar ambiguidade entre devices). O fluxo de upload:

  Device → WRQ filename "octet" [opções: blksize=N tsize=N timeout=N]
  Server → OACK opções (se negociou) OU ACK 0
  Device → DATA bloco 1
  Server → ACK 1
  ... loop até DATA com tamanho < blksize (último)
  Server → ACK final
  fim

Cada WRQ vira uma "sessão" rodando em uma thread própria. A sessão usa
um socket UDP efêmero (porta 30000-30099) pra responder — assim cabe
no firewall NAT igual ao FTP.

Não suporta:
  - RRQ (read request) — não servimos arquivos
  - mode=netascii — só octet (binário, padrão moderno)
  - Mail mode (deprecated desde RFC 783)
"""
import os
import socket
import struct
import threading
import logging
import random
from ipaddress import ip_address, ip_network
from sqlalchemy import select
from models import Device, Protocolo
from services.push_backup import SyncSessionLocal, processar_upload, auditar_falha_push

log = logging.getLogger("nexus.tftp")

TFTP_PORT = 69
TFTP_PASSIVE_RANGE = (30000, 30099)  # mesma faixa do FTP — sessões respondem daqui

# Constantes do RFC 1350
OP_RRQ = 1
OP_WRQ = 2
OP_DATA = 3
OP_ACK = 4
OP_ERROR = 5
OP_OACK = 6  # RFC 2347

# Códigos de erro
ERR_NOT_DEFINED = 0
ERR_FILE_NOT_FOUND = 1
ERR_ACCESS_VIOLATION = 2
ERR_DISK_FULL = 3
ERR_ILLEGAL_OP = 4

DEFAULT_BLKSIZE = 512
MAX_BLKSIZE = 65464   # limite do RFC 2348
MAX_FILE_SIZE = 5 * 1024 * 1024
SESSION_TIMEOUT = 30  # segundos de idle antes de abortar


def _resolve_device_por_ip(client_ip: str) -> Device | None:
    """Encontra o device cujo CIDR contém o IP. Como CIDR p/ TFTP é forçado
    a /32 no router, em uso normal há no máximo 1 match. Se houver mais
    (admin afrouxou pra /24, ex.), retorna None pra evitar ambiguidade."""
    try:
        ip_obj = ip_address(client_ip)
    except ValueError:
        return None
    with SyncSessionLocal() as db:
        rows = db.execute(
            select(Device).where(
                Device.protocolo == Protocolo.tftp_push,
                Device.ativo == True,  # noqa: E712
                Device.ftp_origem_cidr.is_not(None),
            )
        ).scalars().all()
    matches = []
    for d in rows:
        try:
            net = ip_network((d.ftp_origem_cidr or "").strip(), strict=False)
        except ValueError:
            continue
        if ip_obj in net:
            matches.append(d)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        log.warning("TFTP %s: ambiguidade — %d devices casaram com o IP", client_ip, len(matches))
    return None


def _send_error(sock: socket.socket, addr: tuple, code: int, msg: str):
    pkt = struct.pack("!HH", OP_ERROR, code) + msg.encode("ascii", errors="replace") + b"\x00"
    try:
        sock.sendto(pkt, addr)
    except OSError:
        pass


def _parse_options(payload: bytes) -> tuple[str, str, dict]:
    """Lê filename, mode, opções de WRQ. Cada campo é null-terminated."""
    parts = payload.split(b"\x00")
    if len(parts) < 3:
        raise ValueError("WRQ malformado")
    filename = parts[0].decode("ascii", errors="replace")
    mode = parts[1].decode("ascii", errors="replace").lower()
    options = {}
    extras = parts[2:]
    # Pares alternados nome/valor
    for i in range(0, len(extras) - 1, 2):
        nome = extras[i].decode("ascii", errors="replace").lower()
        valor = extras[i + 1].decode("ascii", errors="replace")
        if nome:
            options[nome] = valor
    return filename, mode, options


def _bind_session_socket() -> socket.socket:
    """Pega uma porta da faixa passiva. Random em vez de sequential pra
    evitar colisão entre threads concorrentes."""
    portas = list(range(TFTP_PASSIVE_RANGE[0], TFTP_PASSIVE_RANGE[1] + 1))
    random.shuffle(portas)
    for porta in portas:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("0.0.0.0", porta))
            return s
        except OSError:
            s.close()
            continue
    # Última tentativa: porta efêmera do SO
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    return s


def _handle_wrq(client_addr: tuple, filename: str, options: dict):
    client_ip = client_addr[0]
    log.info("TFTP WRQ de %s arquivo=%s opções=%s", client_ip, filename, options)

    # 1. Whitelist por IP — antes de gastar buffer/socket
    device = _resolve_device_por_ip(client_ip)
    if device is None:
        log.warning("TFTP %s: nenhum device casa com o IP — descartando", client_ip)
        return

    # 2. Negociar blksize (se cliente pediu) e tsize (se enviou)
    blksize = DEFAULT_BLKSIZE
    oack_options: dict = {}
    if "blksize" in options:
        try:
            req = int(options["blksize"])
            blksize = max(8, min(MAX_BLKSIZE, req))
            oack_options["blksize"] = str(blksize)
        except ValueError:
            pass
    tsize_anunciado = None
    if "tsize" in options:
        try:
            tsize_anunciado = int(options["tsize"])
            if tsize_anunciado > MAX_FILE_SIZE:
                # Cliente já avisou que vai exceder limite
                sock_tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                _send_error(sock_tmp, client_addr, ERR_DISK_FULL,
                            f"file too large (max {MAX_FILE_SIZE})")
                sock_tmp.close()
                with SyncSessionLocal() as db:
                    auditar_falha_push(
                        db, device, client_ip,
                        motivo=f"tamanho anunciado excede limite ({tsize_anunciado} > {MAX_FILE_SIZE})",
                        protocolo="TFTP", nome_arquivo=filename,
                    )
                return
            oack_options["tsize"] = str(tsize_anunciado)
        except ValueError:
            pass

    # 3. Socket de sessão
    sock = _bind_session_socket()
    sock.settimeout(SESSION_TIMEOUT)
    try:
        # 4. Resposta inicial: OACK se negociou opções, senão ACK 0
        if oack_options:
            payload = b""
            for k, v in oack_options.items():
                payload += k.encode("ascii") + b"\x00" + v.encode("ascii") + b"\x00"
            sock.sendto(struct.pack("!H", OP_OACK) + payload, client_addr)
        else:
            sock.sendto(struct.pack("!HH", OP_ACK, 0), client_addr)

        # 5. Loop de DATA/ACK
        buffer = bytearray()
        bloco_esperado = 1
        while True:
            try:
                data, src = sock.recvfrom(4 + blksize)
            except socket.timeout:
                log.warning("TFTP %s: timeout na sessão", client_ip)
                return
            if src[0] != client_ip:
                continue  # ignora pacote de outro IP no mesmo socket
            if len(data) < 4:
                continue
            opcode, num = struct.unpack("!HH", data[:4])
            if opcode != OP_DATA:
                _send_error(sock, src, ERR_ILLEGAL_OP, "expected DATA")
                return
            if num != bloco_esperado:
                # Duplicado: re-ACK pra evitar retransmissão eterna
                if num == bloco_esperado - 1:
                    sock.sendto(struct.pack("!HH", OP_ACK, num), src)
                continue
            chunk = data[4:]
            buffer.extend(chunk)
            if len(buffer) > MAX_FILE_SIZE:
                _send_error(sock, src, ERR_DISK_FULL, "file too large")
                with SyncSessionLocal() as db:
                    auditar_falha_push(
                        db, device, client_ip,
                        motivo=f"tamanho excedido durante recebimento (>{MAX_FILE_SIZE})",
                        protocolo="TFTP", nome_arquivo=filename,
                    )
                return
            sock.sendto(struct.pack("!HH", OP_ACK, num), src)
            if len(chunk) < blksize:
                # Último bloco recebido (RFC 1350)
                break
            bloco_esperado += 1
            # Block number wrap em 65535 → 0 (interoperabilidade com clientes
            # que respeitam o wrap em vez de erro)
            if bloco_esperado > 0xFFFF:
                bloco_esperado = 0

        # 6. Persiste backup
        tamanho = len(buffer)
        if tamanho == 0:
            log.warning("TFTP %s: arquivo vazio recebido — descartando", client_ip)
            with SyncSessionLocal() as db:
                auditar_falha_push(db, device, client_ip,
                                   motivo="arquivo vazio (0 bytes)",
                                   protocolo="TFTP", nome_arquivo=filename)
            return
        try:
            conteudo = buffer.decode("utf-8", errors="replace")
        except Exception:
            conteudo = buffer.decode("latin-1", errors="replace")
        with SyncSessionLocal() as db:
            # Refetch device pra garantir estado atual
            db_device = db.execute(
                select(Device).where(Device.id == device.id)
            ).scalar_one_or_none()
            if not db_device:
                return
            processar_upload(db_device, conteudo, client_ip, tamanho, db,
                             nome_arquivo=filename, protocolo="TFTP")
            db.commit()
        log.info("TFTP %s: backup recebido (%d bytes) → device #%d", client_ip, tamanho, device.id)

    except Exception:
        log.exception("TFTP %s: erro na sessão", client_ip)
    finally:
        try:
            sock.close()
        except Exception:
            pass


_listener_socket: socket.socket | None = None
_listener_thread: threading.Thread | None = None
_stopping = False


def _accept_loop():
    global _stopping
    assert _listener_socket is not None
    log.info("TFTP server escutando em 0.0.0.0:%d/udp", TFTP_PORT)
    while not _stopping:
        try:
            data, addr = _listener_socket.recvfrom(2048)
        except OSError:
            if _stopping:
                return
            continue
        if len(data) < 2:
            continue
        opcode = struct.unpack("!H", data[:2])[0]
        if opcode == OP_RRQ:
            _send_error(_listener_socket, addr, ERR_ACCESS_VIOLATION, "RRQ não permitido")
            continue
        if opcode != OP_WRQ:
            _send_error(_listener_socket, addr, ERR_ILLEGAL_OP, "esperado WRQ")
            continue
        try:
            filename, mode, options = _parse_options(data[2:])
        except ValueError:
            _send_error(_listener_socket, addr, ERR_NOT_DEFINED, "WRQ malformado")
            continue
        if mode != "octet":
            _send_error(_listener_socket, addr, ERR_NOT_DEFINED, "apenas mode=octet")
            continue
        threading.Thread(
            target=_handle_wrq, args=(addr, filename, options),
            name=f"tftp-{addr[0]}", daemon=True,
        ).start()


def iniciar_tftp_server() -> None:
    global _listener_socket, _listener_thread, _stopping
    if _listener_thread is not None and _listener_thread.is_alive():
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", TFTP_PORT))
    except OSError as e:
        log.error("Não foi possível bind em 0.0.0.0:%d/udp — %s", TFTP_PORT, e)
        return
    _listener_socket = sock
    _stopping = False
    _listener_thread = threading.Thread(target=_accept_loop, name="tftp-listener", daemon=True)
    _listener_thread.start()


def parar_tftp_server() -> None:
    global _listener_socket, _stopping
    _stopping = True
    if _listener_socket is not None:
        try:
            _listener_socket.close()
        except Exception:
            pass
        _listener_socket = None
