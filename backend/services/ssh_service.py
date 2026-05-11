import re
import os
import time
import io
import paramiko
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
from paramiko import SSHClient, AutoAddPolicy
from paramiko.ssh_exception import SSHException, AuthenticationException as ParamikoAuthError, PasswordRequiredException
from socket import timeout as SocketTimeout, gaierror as SocketGAIError
from models import Device, DeviceVendor, Protocolo, AuthMethod
from services.crypto import decrypt


def load_pkey(pem: str, passphrase: str | None = None) -> paramiko.PKey:
    """Carrega uma chave privada (RSA/Ed25519/ECDSA/DSA) em formato OpenSSH ou PEM
    a partir de uma string em memória. Tenta cada classe na ordem mais comum."""
    pwd = passphrase if passphrase else None
    last_err: Exception | None = None
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(pem), password=pwd)
        except PasswordRequiredException:
            # chave protegida por passphrase, mas não enviamos uma — propaga já
            raise
        except SSHException as e:
            last_err = e
            continue
    raise SSHException(f"Formato de chave privada não reconhecido: {last_err}")

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Casa o More prompt + espaços adjacentes na MESMA linha. Inclui o padding
# que o Huawei manda pra limpar visualmente o prompt na tela (~15 espaços
# entre "----" e o início da próxima linha de config). [^\S\n] = whitespace
# que NÃO é newline, pra não comer quebras de linha legítimas.
_MORE_RE = re.compile(r"[^\S\n]*-{2,}\s*[Mm]ore[^-\n]*-{2,}[^\S\n]*")


def _apply_bs(s: str) -> str:
    """Emula cursor de terminal: \\x08 (backspace) apaga o char anterior.
    Necessário para Huawei/Datacom que apagam o prompt de paginação com
    sequências \\b\\b\\b... antes de mandar a próxima página."""
    out = []
    for c in s:
        if c == "\x08":
            if out:
                out.pop()
        else:
            out.append(c)
    return "".join(out)


def _clean_output(raw: str) -> str:
    """Pipeline padrão de limpeza pra coleta com paginação manual."""
    s = _ANSI_RE.sub("", raw)
    s = _apply_bs(s)
    s = _MORE_RE.sub("", s)
    s = s.replace("\r", "")
    return s

DEVICE_TYPES_SSH = {
    DeviceVendor.huawei:    "huawei",
    DeviceVendor.ubiquiti:  "ubiquiti_edge",
    DeviceVendor.intelbras: "cisco_ios",
    DeviceVendor.datacom:   "cisco_ios",
    DeviceVendor.cisco:     "cisco_ios",
    DeviceVendor.juniper:   "juniper_junos",
    DeviceVendor.zte:        "zte_zxros",      # ZTE ZXR10/ZXA10 — CLI Cisco-like
    DeviceVendor.nokia:      "nokia_sros",     # Nokia SR OS / 7750
    DeviceVendor.fiberhome:  "generic",        # AN5516 etc — varia por firmware, generic é o mais seguro
    DeviceVendor.vsolutions: "generic",        # V-SOL OLT — CLI varia, generic é o seguro
    DeviceVendor.outro:      "generic",
}

DEVICE_TYPES_TELNET = {
    DeviceVendor.mikrotik:    "generic_termserver",
    DeviceVendor.mikrotik_v7: "generic_termserver",
    DeviceVendor.huawei:    "huawei_telnet",
    DeviceVendor.ubiquiti:  "generic_termserver",
    DeviceVendor.intelbras: "cisco_ios_telnet",
    DeviceVendor.datacom:   "cisco_ios_telnet",
    DeviceVendor.cisco:     "cisco_ios_telnet",
    DeviceVendor.juniper:   "juniper_junos_telnet",
    DeviceVendor.zte:        "zte_zxros_telnet",
    DeviceVendor.nokia:      "nokia_sros_telnet",
    DeviceVendor.fiberhome:  "generic_termserver",
    DeviceVendor.vsolutions: "generic_termserver",
    DeviceVendor.outro:      "generic_termserver",
}

COMMANDS = {
    # Mikrotik v6: /export já inclui senhas. Mikrotik v7: precisa de
    # show-sensitive (caso contrário retorna PSKs/secrets/etc mascarados).
    DeviceVendor.mikrotik:    "/export",
    DeviceVendor.mikrotik_v7: "/export show-sensitive",
    DeviceVendor.huawei:    "display current-configuration",
    DeviceVendor.ubiquiti:  "show configuration",
    DeviceVendor.intelbras: "show running-config",
    DeviceVendor.datacom:   "show running-config",
    DeviceVendor.cisco:     "show running-config",
    DeviceVendor.juniper:   "show configuration | display set",
    DeviceVendor.zte:        "show running-config",
    DeviceVendor.nokia:      "admin display-config",
    DeviceVendor.fiberhome:  "show running-config",
    DeviceVendor.vsolutions: "show running-config",
    DeviceVendor.outro:      "show running-config",
}

def _clean_host(ip: str) -> str:
    if not ip:
        return ip
    s = ip.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    return s

def _build_auth_kwargs(device: Device) -> dict:
    """Monta o dict de auth pro Netmiko/Paramiko a partir do método configurado.
    Caller deve fazer ** nesse dict junto com host/port/username/timeouts."""
    if device.auth_method == AuthMethod.ssh_key:
        if not device.chave_privada_enc:
            raise SSHException("Dispositivo configurado para chave SSH mas sem chave cadastrada")
        pem = decrypt(device.chave_privada_enc)
        passphrase = decrypt(device.chave_passphrase_enc) if device.chave_passphrase_enc else None
        pkey = load_pkey(pem, passphrase)
        # use_keys=True + pkey faz o Netmiko/Paramiko usar a chave em memória
        # e ignorar tentativa de senha. allow_agent/look_for_keys=False evita
        # vazamento pra chaves do sistema do servidor.
        return {
            "pkey": pkey,
            "use_keys": True,
            "allow_agent": False,
            "key_file": None,
        }
    # password
    if not device.senha_ssh_enc:
        raise SSHException("Dispositivo configurado para senha mas sem senha cadastrada")
    return {
        "password": decrypt(device.senha_ssh_enc),
        "allow_agent": False,
        "use_keys": False,
    }


def _is_telnet_banner_error(msg: str) -> bool:
    msg_lower = msg.lower()
    return (
        "0xff" in msg_lower
        or ("banner" in msg_lower and "ssh" in msg_lower)
        or "codec can't decode" in msg_lower
        or "invalid start byte" in msg_lower
    )

def _run_datacom_netmiko(device: Device) -> tuple[str, str]:
    is_telnet = device.protocolo == Protocolo.telnet
    conn = {
        "device_type": "cisco_ios_telnet" if is_telnet else "cisco_ios",
        "host": _clean_host(device.ip),
        "port": device.porta,
        "username": device.usuario_ssh,
        "timeout": 30,
        "conn_timeout": 30,
        "banner_timeout": 20,
        "blocking_timeout": 60,
        **_build_auth_kwargs(device),
    }
    with ConnectHandler(**conn) as net:
        for disable_cmd in ("terminal length 0", "screen-length 0 temporary", "screen-length 0"):
            try:
                net.send_command_timing(disable_cmd, delay_factor=2)
            except Exception:
                pass
        net.write_channel("show running-config\n")
        output = ""
        start = time.time()
        last_data = time.time()
        next_more_search = 0  # posição a partir da qual procurar o próximo More
        TOTAL_TIMEOUT = 600
        IDLE_TIMEOUT = 8.0
        while True:
            now = time.time()
            if now - start > TOTAL_TIMEOUT:
                break
            chunk = net.read_channel()
            if chunk:
                output += chunk
                last_data = now
                # Procura o próximo More a partir da posição não processada.
                # NÃO substituímos no buffer — o regex precisa do padding completo
                # pra match no _clean_output (More + espaços vêm em chunks separados).
                m = _MORE_RE.search(output, next_more_search)
                if m:
                    net.write_channel(" ")
                    next_more_search = m.end()
            else:
                if now - last_data > IDLE_TIMEOUT:
                    break
                time.sleep(0.3)

    cleaned = _clean_output(output)
    if not cleaned.strip():
        return "falha", "Sem resposta do equipamento"
    return "sucesso", cleaned

def _run_huawei_netmiko(device: Device) -> tuple[str, str]:
    """Huawei VRP: prompts dinâmicos (<AS123-BGP>) e configs grandes quebram o
    send_command padrão. Usa o mesmo loop manual do datacom — desabilita
    paginação, lê em chunks até idle, trata --More--."""
    is_telnet = device.protocolo == Protocolo.telnet
    conn = {
        "device_type": "huawei_telnet" if is_telnet else "huawei",
        "host": _clean_host(device.ip),
        "port": device.porta,
        "username": device.usuario_ssh,
        "timeout": 30,
        "conn_timeout": 30,
        "banner_timeout": 20,
        "blocking_timeout": 60,
        **_build_auth_kwargs(device),
    }
    with ConnectHandler(**conn) as net:
        for disable_cmd in ("screen-length 0 temporary", "screen-length disable"):
            try:
                net.send_command_timing(disable_cmd, delay_factor=2)
            except Exception:
                pass
        net.write_channel("display current-configuration\n")
        output = ""
        start = time.time()
        last_data = time.time()
        next_more_search = 0  # posição a partir da qual procurar o próximo More
        TOTAL_TIMEOUT = 600
        IDLE_TIMEOUT = 8.0
        while True:
            now = time.time()
            if now - start > TOTAL_TIMEOUT:
                break
            chunk = net.read_channel()
            if chunk:
                output += chunk
                last_data = now
                # Procura o próximo More a partir da posição não processada.
                # NÃO substituímos no buffer — o regex precisa do padding completo
                # pra match no _clean_output (More + espaços vêm em chunks separados).
                m = _MORE_RE.search(output, next_more_search)
                if m:
                    net.write_channel(" ")
                    next_more_search = m.end()
            else:
                if now - last_data > IDLE_TIMEOUT:
                    break
                time.sleep(0.3)

    cleaned = _clean_output(output)
    if not cleaned.strip():
        return "falha", "Sem resposta do equipamento"
    return "sucesso", cleaned

def _run_zte_netmiko(device: Device) -> tuple[str, str]:
    """ZTE ZXR10/ZXA10 (C300/C320/C600): igual ao Huawei/Datacom, o send_command
    do Netmiko quebra com 'Pattern not detected: ZXAN#' quando o hostname da OLT
    não é o default `ZXAN` (ex.: cliente renomeou pra `OLT-camon`) ou quando o
    output do running-config é grande o suficiente pra estourar o read_timeout
    de detecção de prompt. Usa o mesmo loop manual: write_channel +
    read_channel até idle, paginação manual via space (--More--)."""
    is_telnet = device.protocolo == Protocolo.telnet
    conn = {
        # zte_zxros_telnet: driver dedicado da ZTE no Netmiko. Antes usava
        # cisco_ios_telnet como fallback, mas ele auto-envia `terminal width 511`
        # no session_preparation, e a ZTE C320 retorna `%Error 20200: Invalid
        # input` — Netmiko não vê o echo esperado e dispara "Pattern not
        # detected: 'terminal width 511'" antes mesmo de entrar no loop manual.
        "device_type": "zte_zxros_telnet" if is_telnet else "zte_zxros",
        "host": _clean_host(device.ip),
        "port": device.porta,
        "username": device.usuario_ssh,
        "timeout": 30,
        "conn_timeout": 30,
        "banner_timeout": 20,
        "blocking_timeout": 60,
        **_build_auth_kwargs(device),
    }
    # Debug opcional: `ZTE_DEBUG_LOG=1` no .env grava o stream raw da sessão
    # Netmiko (tudo que vai/volta da OLT) em /tmp/zte_session_<device_id>.log.
    # Usado pra diagnosticar coleta incompleta — ver onde o loop paginação trava.
    # Sobrescreve a cada coleta (não acumula). Manter desligado em prod normal.
    if os.environ.get("ZTE_DEBUG_LOG") == "1":
        conn["session_log"] = f"/tmp/zte_session_{device.id}.log"
        conn["session_log_file_mode"] = "write"
    with ConnectHandler(**conn) as net:
        # ZTE aceita `terminal length 0` (cisco-like) na maioria dos firmwares;
        # `screen-length 0` aparece em alguns ZXA10 mais antigos; `terminal no
        # length` é variante extra que aparece em firmware ZXA10 com syntax
        # cisco-like estrita. Tenta todos silenciosamente — se um falhar o outro
        # ainda desabilita a paginação.
        for disable_cmd in ("terminal length 0", "screen-length 0", "terminal no length"):
            try:
                net.send_command_timing(disable_cmd, delay_factor=2)
            except Exception:
                pass
        net.write_channel("show running-config\n")
        output = ""
        start = time.time()
        last_data = time.time()
        next_more_search = 0
        # ZTE imprime a linha literal `end` (sozinha) no fim do running-config —
        # mesma convenção do Cisco IOS. Detectar essa marca permite sair na hora
        # quando o output terminou, em vez de esperar IDLE_TIMEOUT só pra
        # confirmar fim de stream. Sem isso, OLT que pausa >IDLE entre seções
        # internas (pon-onu-mng → username/snmp/ntp em config grande) faz o loop
        # sair cedo e retornar truncado — bug observado em v1.2.5/v1.2.6.
        # Procuro apenas nos últimos 200 chars pra performance em output grande.
        _END_RE = re.compile(r"\nend\r?\n")
        # IDLE/TOTAL_TIMEOUT viram fallback pra caso `end` não venha (erro de
        # comando, conexão derrubada no meio, etc.). IDLE diferente por
        # protocolo: SSH na ZTE C320 tem rate-limit/flow-control interno mais
        # agressivo — chega a pausar >2min entre seções `pon-onu-mng` e o resto
        # da config (username/snmp/ntp etc) em OLT com muitas ONUs. Validado via
        # session_log: o stream chega completo no Netmiko, mas só depois do
        # nosso loop ter saído por idle. Telnet não tem essa pausa.
        TOTAL_TIMEOUT = 900
        IDLE_TIMEOUT = 60.0 if is_telnet else 180.0
        while True:
            now = time.time()
            if now - start > TOTAL_TIMEOUT:
                break
            chunk = net.read_channel()
            if chunk:
                output += chunk
                last_data = now
                m = _MORE_RE.search(output, next_more_search)
                if m:
                    net.write_channel(" ")
                    next_more_search = m.end()
                if _END_RE.search(output, max(0, len(output) - 200)):
                    break
            else:
                if now - last_data > IDLE_TIMEOUT:
                    break
                time.sleep(0.3)

    cleaned = _clean_output(output)
    if not cleaned.strip():
        return "falha", "Sem resposta do equipamento"
    return "sucesso", cleaned

def _run_mikrotik_paramiko(device: Device) -> tuple[str, str]:
    # COMMANDS já mapeia /export e /export show-sensitive por versão.
    # Usado direto via Paramiko porque Netmiko quebra a detecção de prompt
    # do RouterOS quando o output é grande.
    cmd = COMMANDS.get(device.fabricante, "/export")
    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy())
    connect_kwargs: dict = dict(
        hostname=_clean_host(device.ip),
        port=device.porta,
        username=device.usuario_ssh,
        timeout=30,
        banner_timeout=20,
        auth_timeout=30,
        look_for_keys=False,
        allow_agent=False,
    )
    if device.auth_method == AuthMethod.ssh_key:
        if not device.chave_privada_enc:
            return "falha", "Dispositivo configurado para chave SSH mas sem chave cadastrada"
        pem = decrypt(device.chave_privada_enc)
        passphrase = decrypt(device.chave_passphrase_enc) if device.chave_passphrase_enc else None
        connect_kwargs["pkey"] = load_pkey(pem, passphrase)
    else:
        if not device.senha_ssh_enc:
            return "falha", "Dispositivo configurado para senha mas sem senha cadastrada"
        connect_kwargs["password"] = decrypt(device.senha_ssh_enc)
    try:
        client.connect(**connect_kwargs)
        _, stdout, stderr = client.exec_command(cmd, timeout=60)
        output = stdout.read().decode("utf-8", errors="replace")
        if not output.strip():
            err = stderr.read().decode("utf-8", errors="replace")
            return "falha", f"Sem resposta do equipamento{': ' + err if err.strip() else ''}"
        return "sucesso", output
    finally:
        client.close()

def run_backup(device: Device) -> tuple[str, str]:
    is_telnet = device.protocolo == Protocolo.telnet
    # Telnet só suporta senha. Falha cedo para evitar erros confusos.
    if is_telnet and device.auth_method == AuthMethod.ssh_key:
        return "falha", "Telnet não suporta autenticação por chave SSH — altere o protocolo para SSH ou use senha."
    try:
        if device.fabricante in (DeviceVendor.mikrotik, DeviceVendor.mikrotik_v7) and not is_telnet:
            return _run_mikrotik_paramiko(device)

        if device.fabricante == DeviceVendor.datacom:
            return _run_datacom_netmiko(device)

        if device.fabricante == DeviceVendor.huawei:
            return _run_huawei_netmiko(device)

        if device.fabricante == DeviceVendor.zte:
            return _run_zte_netmiko(device)

        type_map = DEVICE_TYPES_TELNET if is_telnet else DEVICE_TYPES_SSH
        conn = {
            "device_type": type_map.get(device.fabricante, "generic_termserver" if is_telnet else "generic"),
            "host": _clean_host(device.ip),
            "port": device.porta,
            "username": device.usuario_ssh,
            "timeout": 30,
            "conn_timeout": 30,
            "banner_timeout": 20,
            "blocking_timeout": 60,
            **_build_auth_kwargs(device),
        }
        with ConnectHandler(**conn) as net:
            command = COMMANDS.get(device.fabricante, "show running-config")
            output = net.send_command(command, read_timeout=60)

        if not output.strip():
            return "falha", "Sem resposta do equipamento"
        return "sucesso", output

    except PasswordRequiredException:
        return "falha", "Chave SSH é protegida por passphrase — preencha o campo Passphrase no dispositivo."

    except ParamikoAuthError:
        if device.auth_method == AuthMethod.ssh_key:
            return "falha", "Falha de autenticação: chave SSH rejeitada pelo equipamento (verifique se a chave pública correspondente está cadastrada no dispositivo)"
        return "falha", "Falha de autenticação: usuário ou senha incorretos"

    except NetmikoAuthenticationException:
        if device.auth_method == AuthMethod.ssh_key:
            return "falha", "Falha de autenticação: chave SSH rejeitada pelo equipamento (verifique se a chave pública correspondente está cadastrada no dispositivo)"
        return "falha", "Falha de autenticação: usuário ou senha incorretos"

    except NetmikoTimeoutException as e:
        # Netmiko empacota qualquer falha de socket (network unreachable, conexão
        # recusada, port filtered) em NetmikoTimeoutException. Tenta dar uma
        # mensagem útil em vez do "Timeout" genérico que confunde diagnóstico.
        msg = str(e).lower()
        if "network is unreachable" in msg or "no route to host" in msg:
            return "falha", (
                f"Rota indisponível para {device.ip}: o backend não consegue alcançar esse endereço. "
                "Verifique a conectividade da rede do servidor (ex.: IPv6 habilitado se for endereço v6)."
            )
        if "tcp connection to device failed" in msg:
            return "falha", (
                f"Conexão TCP falhou com {device.ip}:{device.porta} — "
                "verifique IP, porta, firewall intermediário e se o serviço SSH está ativo no equipamento."
            )
        return "falha", f"Timeout: {device.ip} não respondeu no tempo esperado (30s)"

    except SSHException as e:
        if _is_telnet_banner_error(str(e)):
            return "falha", (
                f"Protocolo incorreto: o equipamento {device.ip} respondeu com dados Telnet numa conexão SSH. "
                "Edite o dispositivo e altere o protocolo para Telnet (porta padrão: 23)."
            )
        return "falha", f"Erro SSH: {e}"

    except (SocketTimeout, TimeoutError):
        return "falha", f"Conexão recusada ou timeout ao alcançar {device.ip}:{device.porta}"

    except SocketGAIError:
        return "falha", f"Host não encontrado: não foi possível resolver '{device.ip}'"

    except ConnectionRefusedError:
        return "falha", f"Conexão recusada em {device.ip}:{device.porta} — verifique IP, porta e se o serviço está ativo"

    except Exception as e:
        return "falha", str(e)
