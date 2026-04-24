import paramiko
from models import Device, DeviceVendor
from services.crypto import decrypt

COMMANDS = {
    DeviceVendor.mikrotik: "/export",
    DeviceVendor.huawei: "display current-configuration",
    DeviceVendor.ubiquiti: "cat /tmp/system.cfg",
    DeviceVendor.intelbras: "show running-config",
    DeviceVendor.outro: "show running-config",
}

def run_backup(device: Device) -> tuple[str, str]:
    """Retorna (status, conteudo_ou_erro)"""
    try:
        senha = decrypt(device.senha_ssh_enc)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=device.ip,
            port=device.porta,
            username=device.usuario_ssh,
            password=senha,
            timeout=30,
            allow_agent=False,
            look_for_keys=False,
        )
        command = COMMANDS.get(device.fabricante, "show running-config")
        _, stdout, stderr = client.exec_command(command, timeout=60)
        output = stdout.read().decode(errors="replace")
        error = stderr.read().decode(errors="replace")
        client.close()

        if not output.strip():
            return "falha", error or "Sem resposta do equipamento"
        return "sucesso", output
    except Exception as e:
        return "falha", str(e)
