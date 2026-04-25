import re
import time
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
from paramiko import SSHClient, AutoAddPolicy
from paramiko.ssh_exception import SSHException, AuthenticationException as ParamikoAuthError
from socket import timeout as SocketTimeout, gaierror as SocketGAIError
from models import Device, DeviceVendor, Protocolo
from services.crypto import decrypt

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_MORE_RE = re.compile(r"--\s*More\s*--")

DEVICE_TYPES_SSH = {
    DeviceVendor.huawei:    "huawei",
    DeviceVendor.ubiquiti:  "ubiquiti_edge",
    DeviceVendor.intelbras: "cisco_ios",
    DeviceVendor.datacom:   "cisco_ios",
    DeviceVendor.cisco:     "cisco_ios",
    DeviceVendor.juniper:   "juniper_junos",
    DeviceVendor.outro:     "generic",
}

DEVICE_TYPES_TELNET = {
    DeviceVendor.mikrotik:  "generic_termserver",
    DeviceVendor.huawei:    "huawei_telnet",
    DeviceVendor.ubiquiti:  "generic_termserver",
    DeviceVendor.intelbras: "cisco_ios_telnet",
    DeviceVendor.datacom:   "cisco_ios_telnet",
    DeviceVendor.cisco:     "cisco_ios_telnet",
    DeviceVendor.juniper:   "juniper_junos_telnet",
    DeviceVendor.outro:     "generic_termserver",
}

COMMANDS = {
    DeviceVendor.mikrotik:  "/export",
    DeviceVendor.huawei:    "display current-configuration",
    DeviceVendor.ubiquiti:  "show configuration",
    DeviceVendor.intelbras: "show running-config",
    DeviceVendor.datacom:   "show running-config",
    DeviceVendor.cisco:     "show running-config",
    DeviceVendor.juniper:   "show configuration | display set",
    DeviceVendor.outro:     "show running-config",
}

def _clean_host(ip: str) -> str:
    if not ip:
        return ip
    s = ip.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    return s

def _is_telnet_banner_error(msg: str) -> bool:
    msg_lower = msg.lower()
    return (
        "0xff" in msg_lower
        or ("banner" in msg_lower and "ssh" in msg_lower)
        or "codec can't decode" in msg_lower
        or "invalid start byte" in msg_lower
    )

def _run_datacom_netmiko(device: Device, senha: str) -> tuple[str, str]:
    is_telnet = device.protocolo == Protocolo.telnet
    conn = {
        "device_type": "cisco_ios_telnet" if is_telnet else "cisco_ios",
        "host": _clean_host(device.ip),
        "port": device.porta,
        "username": device.usuario_ssh,
        "password": senha,
        "timeout": 30,
        "conn_timeout": 30,
        "banner_timeout": 20,
        "blocking_timeout": 60,
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
                if _MORE_RE.search(output[-400:]):
                    net.write_channel(" ")
                    output = _MORE_RE.sub("", output)
            else:
                if now - last_data > IDLE_TIMEOUT:
                    break
                time.sleep(0.3)

    cleaned = _ANSI_RE.sub("", output)
    cleaned = _MORE_RE.sub("", cleaned)
    cleaned = cleaned.replace("\x08", "").replace("\r", "")
    if not cleaned.strip():
        return "falha", "Sem resposta do equipamento"
    return "sucesso", cleaned

def _run_mikrotik_paramiko(device: Device, senha: str) -> tuple[str, str]:
    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy())
    try:
        client.connect(
            hostname=_clean_host(device.ip),
            port=device.porta,
            username=device.usuario_ssh,
            password=senha,
            timeout=30,
            banner_timeout=20,
            auth_timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        _, stdout, stderr = client.exec_command("/export", timeout=60)
        output = stdout.read().decode("utf-8", errors="replace")
        if not output.strip():
            err = stderr.read().decode("utf-8", errors="replace")
            return "falha", f"Sem resposta do equipamento{': ' + err if err.strip() else ''}"
        return "sucesso", output
    finally:
        client.close()

def run_backup(device: Device) -> tuple[str, str]:
    try:
        senha = decrypt(device.senha_ssh_enc)
        is_telnet = device.protocolo == Protocolo.telnet

        if device.fabricante == DeviceVendor.mikrotik and not is_telnet:
            return _run_mikrotik_paramiko(device, senha)

        if device.fabricante == DeviceVendor.datacom:
            return _run_datacom_netmiko(device, senha)

        type_map = DEVICE_TYPES_TELNET if is_telnet else DEVICE_TYPES_SSH
        conn = {
            "device_type": type_map.get(device.fabricante, "generic_termserver" if is_telnet else "generic"),
            "host": _clean_host(device.ip),
            "port": device.porta,
            "username": device.usuario_ssh,
            "password": senha,
            "timeout": 30,
            "conn_timeout": 30,
            "banner_timeout": 20,
            "blocking_timeout": 60,
        }
        with ConnectHandler(**conn) as net:
            command = COMMANDS.get(device.fabricante, "show running-config")
            output = net.send_command(command, read_timeout=60)

        if not output.strip():
            return "falha", "Sem resposta do equipamento"
        return "sucesso", output

    except ParamikoAuthError:
        return "falha", "Falha de autenticação: usuário ou senha incorretos"

    except NetmikoAuthenticationException:
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
