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
    """Plano A: tenta `/export` direto via `api(cmd)`. Em alguns firmwares
    cada linha de config vem como `!re` reply.

    Retorna a config como texto ou None se o firmware não suporta esse modo.
    Quando vier vazio/no formato inesperado, loga as chaves observadas pra
    facilitar diagnóstico de firmwares específicos.
    """
    try:
        replies = list(api("/export"))
        if not replies:
            log.warning("Plano A /export: 0 replies — firmware não retorna via API")
            return None
        linhas = []
        for row in replies:
            for key in ("ret", "line", "message"):
                if key in row:
                    linhas.append(str(row[key]))
                    break
        if not linhas:
            chaves = sorted({k for r in replies[:20] for k in r.keys()})
            log.warning(
                "Plano A /export: %d replies mas nenhuma chave conhecida (ret/line/message). "
                "Chaves observadas: %s. Caindo pro Plano B.",
                len(replies), chaves
            )
            return None
        return "\n".join(linhas)
    except Exception as e:
        log.info("Plano A /export falhou (esperado em alguns firmwares): %s", e)
        return None


def _find_file(api, nome: str) -> dict | None:
    """Acha um arquivo pelo nome no /file. Itera linear (não usa Path.select.where
    porque o where do librouteros 3.4 retorna Query que precisa de fields fixos
    e dispara confusão de tipos). Loop direto é simples e funciona em qualquer FW."""
    for row in api("/file/print"):
        if row.get("name") == nome:
            return row
    return None


def _export_via_arquivo(api, fabricante: DeviceVendor) -> str:
    """Plano B: escreve arquivo temp, lê o conteúdo via /file/print, deleta.

    show-sensitive=yes em v7 pra incluir senhas/PSKs — v6 já inclui por default
    e não reconhece esse argumento.
    """
    nome_arquivo_base = f"nexus-export-{int(time.time())}"
    nome_arquivo = f"{nome_arquivo_base}.rsc"

    # Args do /export. Mikrotik aceita "yes"/"no" como string em todas as versões.
    export_args: dict = {"file": nome_arquivo_base}
    if fabricante == DeviceVendor.mikrotik_v7:
        export_args["show-sensitive"] = "yes"

    try:
        list(api("/export", **export_args))
    except Exception as e:
        # Algumas builds não aceitam show-sensitive — re-tenta sem (degradação
        # graceful: senhas mascaradas em vez de falhar a coleta inteira).
        if "show-sensitive" in export_args:
            log.warning("export com show-sensitive falhou (%s), retentando sem", e)
            export_args.pop("show-sensitive", None)
            list(api("/export", **export_args))
        else:
            raise

    # Export é assíncrono em alguns firmwares — espera o arquivo aparecer com
    # conteúdo populado. Timeout generoso (30s) cobre routers com disco lento
    # ou com export grande. Antes era 5s e era estourado em CRS328-Asa_Norte.
    deadline = time.time() + 30.0
    arquivo = None
    while time.time() < deadline:
        arquivo = _find_file(api, nome_arquivo)
        if arquivo and arquivo.get("contents"):
            break
        time.sleep(0.5)

    contents = arquivo.get("contents") if arquivo else None
    size_no_disco = arquivo.get("size") if arquivo else None

    # Cleanup do arquivo temp — não deixa lixo no device mesmo se a leitura falhou.
    try:
        if arquivo and ".id" in arquivo:
            api("/file/remove", **{".id": arquivo[".id"]})
    except Exception:
        log.exception("falha ao remover arquivo temp %s do device", nome_arquivo)

    if not contents:
        # Diagnóstico específico ajuda a escolher entre "esperar mais" e "trocar
        # de protocolo": se o arquivo existe com size > 0, o `.contents` foi
        # limitado pelo firmware (Mikrotik trunca em ~4KB em /file/print).
        if arquivo and size_no_disco:
            raise RuntimeError(
                f"Arquivo {nome_arquivo} criado ({size_no_disco} bytes) mas "
                "`.contents` veio vazio — firmware limita conteúdo retornado "
                "via API. Use SSH para este device, ou aumente o limite via "
                "config do RouterOS."
            )
        raise RuntimeError(
            f"Arquivo {nome_arquivo} não apareceu em 30s após /export. "
            "Verifique permissões do usuário (precisa de policy 'read,sensitive') "
            "ou trate o device via SSH."
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
