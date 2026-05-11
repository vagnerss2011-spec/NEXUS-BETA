"""Coleta de backup via RouterOS API binária (Mikrotik v6 e v7).

Why API e não SSH:
- Não exige abrir SSH/Telnet no equipamento (alguns clientes bloqueiam SSH)
- Não tem problema de detecção de prompt (Mikrotik tem prompt dinâmico)
- Mais robusto pra automação — não depende de paginação/--More--

Why API binária e não REST:
- REST só existe no RouterOS v7 (Mikrotik); v6 ainda usa só a binária.
- Mesma biblioteca (`librouteros`) funciona nas duas versões.

Formato do backup: `/export` texto (igual ao SSH atual), pra manter
compatibilidade dos backups históricos do mesmo device.

Caminho de coleta:
1. Plano A — `/export` direto via API: alguns firmwares retornam o output
   como sequência de replies. Validado em runtime via try/except.
2. Plano B — escreve arquivo temp na OLT, lê via `/file/print detail`
   (`.contents`), e remove. Funciona em firmwares onde Plano A não devolve
   linhas via API.
"""
import ssl
import time
import logging
from typing import Tuple

from models import Device, DeviceVendor
from services.crypto import decrypt

log = logging.getLogger(__name__)


def _connect(device: Device):
    """Conecta via librouteros. TLS se device.api_tls."""
    # Import local pra não pagar import quando o serviço só usa SSH.
    from librouteros import connect
    from librouteros.login import plain

    if not device.senha_ssh_enc:
        raise ValueError("API Mikrotik exige senha (não suporta chave SSH).")
    if not device.usuario_ssh:
        raise ValueError("API Mikrotik exige usuário preenchido.")

    senha = decrypt(device.senha_ssh_enc)
    host = device.ip.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1].strip()

    kwargs = dict(
        username=device.usuario_ssh,
        password=senha,
        host=host,
        port=device.porta,
        timeout=30,
        # `plain` em vez do default (que tenta `token` primeiro). RouterOS v7
        # mudou o handshake e o login token quebra em v6. `plain` funciona
        # nas duas versões e a senha viaja por dentro do canal TLS quando
        # api_tls=True. Sem TLS é texto plano — aceitável só em LAN confiável.
        login_method=plain,
    )

    if device.api_tls:
        # SSL context que aceita self-signed (Mikrotik geralmente não tem
        # cert publicamente confiável — gera self-signed via /certificate).
        # Em produção crítica, dar pro usuário a opção de pin do cert, mas
        # esse caso é raro. Aceitar self-signed por padrão é o pragmatismo.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_wrapper"] = ctx.wrap_socket

    return connect(**kwargs)


def _export_via_command(api) -> str | None:
    """Plano A: tenta `/export` direto. Em alguns firmwares cada linha de
    config vem como `!re` reply contendo `=ret=<line>` ou `=line=<line>`.

    Retorna a config como texto ou None se o firmware não suporta esse modo.
    """
    try:
        # /export sem args; iteramos os replies (não usar list comprehension
        # com try interno; queremos detectar falha cedo).
        path = api.path("/export")
        linhas = []
        for row in path:
            # Diferentes versões do RouterOS expõem chaves diferentes nos
            # replies do `/export`. Tentamos as conhecidas em ordem.
            for key in ("ret", "line", "message"):
                if key in row:
                    linhas.append(str(row[key]))
                    break
        if not linhas:
            return None
        return "\n".join(linhas)
    except Exception as e:
        log.info("export via API direto falhou (esperado em alguns firmwares): %s", e)
        return None


def _export_via_arquivo(api, fabricante: DeviceVendor) -> str:
    """Plano B: escreve arquivo temp, lê via /file/print detail, deleta.

    show-sensitive=yes em v7 pra incluir senhas/PSKs no export — v6 já
    inclui por default e não reconhece esse argumento.
    """
    nome_tmp = f"nexus-export-{int(time.time())}.rsc"

    # Monta args do /export. Mikrotik não tem aceitação consistente de bool
    # como int 0/1 vs string "yes"/"no" — usamos string que aceita em ambas.
    export_args: dict = {"file": nome_tmp.removesuffix(".rsc")}
    if fabricante == DeviceVendor.mikrotik_v7:
        export_args["show-sensitive"] = "yes"

    try:
        api.path("/export")(**export_args)
    except Exception as e:
        # Algumas builds não retornam silenciosamente — re-tenta sem
        # show-sensitive (degradação: senhas mascaradas em vez de falhar).
        if "show-sensitive" in export_args:
            log.warning("export com show-sensitive falhou (%s), retentando sem", e)
            export_args.pop("show-sensitive", None)
            api.path("/export")(**export_args)
        else:
            raise

    # Aguardar até 5s o arquivo aparecer (export é assíncrono em alguns
    # firmwares — o reply de /export volta antes do flush completo no disco).
    deadline = time.time() + 5.0
    contents = None
    while time.time() < deadline:
        files = list(api.path("/file").select("name", "contents").where(name=nome_tmp))
        if files and files[0].get("contents"):
            contents = files[0]["contents"]
            break
        time.sleep(0.3)

    # Cleanup do arquivo temp mesmo se a leitura falhou — não deixa lixo no device.
    try:
        for f in api.path("/file").select(".id").where(name=nome_tmp):
            api.path("/file").remove(f[".id"])
    except Exception:
        log.exception("falha ao remover arquivo temp %s do device", nome_tmp)

    if not contents:
        raise RuntimeError(
            f"Não foi possível ler o conteúdo do arquivo {nome_tmp} via API "
            "(.contents vazio). Pode ser limitação do firmware — "
            "considere usar SSH para este device."
        )
    return contents


def run_backup_via_api(device: Device) -> Tuple[str, str]:
    """Entry point chamado pelo run_backup principal."""
    try:
        api = _connect(device)
    except ImportError:
        return "falha", (
            "Dependência 'librouteros' não instalada — rebuild do backend "
            "necessário (pip install librouteros)."
        )
    except ValueError as e:
        return "falha", str(e)
    except Exception as e:
        return "falha", f"Falha na conexão API Mikrotik: {e}"

    try:
        # Plano A
        texto = _export_via_command(api)
        if texto and texto.strip():
            return "sucesso", texto
        # Plano B (fallback)
        texto = _export_via_arquivo(api, device.fabricante)
        if texto and texto.strip():
            return "sucesso", texto
        return "falha", "Export retornou vazio (firmware pode não suportar export via API)."
    except Exception as e:
        return "falha", f"Erro coletando export via API: {e}"
    finally:
        try:
            api.close()
        except Exception:
            pass
