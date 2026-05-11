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

Caminho de coleta (3 planos em cascata):
1. Plano A — `/export` direto via API: alguns firmwares retornam o output
   como sequência de replies. Mais rápido quando funciona.
2. Plano B — `/export file=tmp` + `/file/print` lê `.contents`. Tem limite
   de ~4KB no Mikrotik (firmware trunca o atributo `.contents`); só serve
   pra configs pequenas.
3. Plano C — `/export file=tmp` + `/tool/fetch upload=yes mode=sftp` faz
   o Mikrotik enviar o arquivo pro nosso SFTP server usando credencial
   auto-gerada no device. Funciona pra arquivos de qualquer tamanho.
"""
import os
import queue
import ssl
import threading
import time
import logging
from typing import Tuple

from sqlalchemy import select
from models import Device, DeviceVendor, Protocolo, Backup
from services.crypto import decrypt
from config import settings
from services.push_backup import SyncSessionLocal

# Fila em memória pra entrega cross-thread do conteúdo recebido via SFTP push.
# Quando o Plano C dispara /tool/fetch, registra device_id aqui antes; quando
# o SFTP server processa um upload, checa se device_id está no dict — se sim,
# entrega conteúdo na Queue e PULA o processar_upload normal (evita criar
# backup push duplicado e o dedupe diário que apagaria backups históricos do
# mesmo dia). Thread-safe: dict + Queue protegido por lock.
_pending_lock = threading.Lock()
_pending_api_uploads: dict[int, queue.Queue] = {}


def has_pending_api_upload(device_id: int) -> bool:
    """SFTP server (em thread separada) chama isso pra saber se deve pular
    o `processar_upload` normal (que cria backup push + dedupe diário).
    """
    with _pending_lock:
        return device_id in _pending_api_uploads


def deliver_api_upload(device_id: int, conteudo: str) -> bool:
    """SFTP server chama isso quando o upload é do Plano C — entrega o
    conteúdo na Queue. Retorna True se entregue, False se ninguém estava
    esperando (caller deve seguir fluxo de push normal)."""
    with _pending_lock:
        q = _pending_api_uploads.get(device_id)
    if q is not None:
        try:
            q.put_nowait(conteudo)
            return True
        except queue.Full:
            log.warning("Queue de Plano C cheia pro device %s — descartando", device_id)
    return False

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
                "Chaves observadas: %s. Caindo pro Plano C (upload SFTP).",
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


def _export_via_upload_ftp(api, device: Device) -> str:
    """Plano C: faz o Mikrotik gerar /export file=tmp.rsc e usar /tool/fetch
    upload=yes mode=ftp pra empurrar o arquivo pro nosso FTP server.

    Why FTP e não SFTP: RouterOS `/tool/fetch mode=` aceita só ftp/http/https/scp
    (validado em prod 2026-05-11 — mode=sftp dispara "input does not match any
    value of mode"). SCP exige subsystem exec no nosso paramiko, que não está
    implementado. FTP plain funciona out-of-the-box com pyftpdlib que já roda
    pra fluxo push. A credencial é única por device e gerada com 24 chars —
    risco do tráfego plain em LAN/VPN é aceitável; rede pública NÃO deve usar.

    Pra evitar duplicação com a pipeline normal de push (que aplica dedupe
    diário e apagaria os backups históricos do dia), o FTP server consulta
    `_pending_api_uploads` ao receber um arquivo. Se device_id está
    registrado, entrega conteúdo direto na Queue desta função e PULA o
    `processar_upload`. Retorna conteúdo pro caller (run_backup) criar
    backup com origem='manual' como nos outros fluxos.
    """
    if not device.ftp_user or not device.ftp_senha_enc:
        raise RuntimeError(
            "Device API sem credencial FTP gerada (ftp_user/ftp_senha). "
            "Recadastre o device — em v1.4.4+ a cred é gerada automaticamente."
        )
    if not settings.FTP_MASQUERADE_ADDRESS:
        raise RuntimeError(
            "FTP_MASQUERADE_ADDRESS não configurado no .env. "
            "Sem isso o Mikrotik não tem endereço pra enviar o backup."
        )

    senha_ftp = decrypt(device.ftp_senha_enc)
    nome_base = f"nexus-api-{device.id}-{int(time.time())}"
    nome_rsc = f"{nome_base}.rsc"
    nome_destino = f"backup-api-{device.id}-{int(time.time())}.rsc"

    # 1) Registra fila pra esse device antes de disparar nada — garante que
    # qualquer upload que chegue pra esse user/device seja capturado.
    fila: queue.Queue = queue.Queue(maxsize=1)
    with _pending_lock:
        _pending_api_uploads[device.id] = fila

    try:
        # 1.5) Cleanup de qualquer nexus-api-*.rsc órfão antes de criar novo —
        # protege a memória NAND do Mikrotik contra acúmulo de coletas que
        # falharam no meio (timeout no fetch, erro de rede etc).
        _cleanup_orfaos_nexus(api)

        # 2) Gera o /export no /file do Mikrotik.
        export_args: dict = {"file": nome_base}
        if device.fabricante == DeviceVendor.mikrotik_v7:
            export_args["show-sensitive"] = "yes"
        try:
            list(api("/export", **export_args))
        except Exception as e:
            if "show-sensitive" in export_args:
                log.warning("export com show-sensitive falhou (%s), retentando sem", e)
                export_args.pop("show-sensitive", None)
                list(api("/export", **export_args))
            else:
                raise

        # 3) Dispara /tool/fetch upload=yes mode=ftp pro nosso FTP server.
        # mode=sftp não existe no RouterOS — só ftp/http/https/scp.
        fetch_args = {
            "upload": "yes",
            "mode": "ftp",
            "address": settings.FTP_MASQUERADE_ADDRESS,
            "port": "21",
            "user": device.ftp_user,
            "password": senha_ftp,
            "src-path": nome_rsc,
            "dst-path": nome_destino,
        }
        try:
            list(api("/tool/fetch", **fetch_args))
        except Exception as e:
            _try_remove_file(api, nome_rsc)
            raise RuntimeError(
                f"Falha no /tool/fetch upload pro FTP do NEXUS: {e}. "
                "Verifique permissões do usuário Mikrotik (precisa de "
                "policy 'read,write,ftp,test,sensitive') e se há rota "
                f"pra {settings.FTP_MASQUERADE_ADDRESS}:21."
            )

        # 4) Aguarda a Queue ser populada pelo SFTP server (cross-thread).
        # Timeout generoso pra cobrir rede lenta / arquivo grande.
        try:
            conteudo = fila.get(timeout=60.0)
        except queue.Empty:
            _try_remove_file(api, nome_rsc)
            raise RuntimeError(
                f"/tool/fetch disparado mas arquivo não chegou em 60s. Possíveis "
                f"causas: Mikrotik sem rota pra {settings.FTP_MASQUERADE_ADDRESS}:21, "
                "firewall do NEXUS bloqueando a faixa passiva 30000-30099, "
                "ou cred FTP incorreta. Verifique logs do FTP server."
            )

        # 5) Cleanup do arquivo temp no Mikrotik.
        _try_remove_file(api, nome_rsc)

        if not conteudo or not conteudo.strip():
            raise RuntimeError("Arquivo chegou via SFTP mas com conteúdo vazio.")
        return conteudo
    finally:
        # Sempre desregistra — protege caso outra coleta posterior pro mesmo
        # device chegue (sem nada esperando, pipeline normal processa).
        with _pending_lock:
            _pending_api_uploads.pop(device.id, None)


def _try_remove_file(api, nome: str) -> None:
    """Remove arquivo do /file no Mikrotik silenciosamente."""
    try:
        arq = _find_file(api, nome)
        if arq and ".id" in arq:
            api("/file/remove", **{".id": arq[".id"]})
    except Exception:
        log.exception("falha ao remover arquivo temp %s do device", nome)


def _cleanup_orfaos_nexus(api) -> int:
    """Remove TODOS os arquivos `nexus-api-*.rsc` do /file do Mikrotik.

    Chamado antes de criar arquivo novo no Plano C — protege contra acúmulo
    de órfãos quando coletas anteriores falharam no meio do caminho (timeout
    no fetch, erro de rede, etc.) e o cleanup do try/finally não rodou.
    Memória NAND do Mikrotik é pequena (16-64 MB típicos), config grande
    + dezenas de órfãos pode bater o limite.

    Retorna número de arquivos removidos pra log.
    """
    removidos = 0
    try:
        for row in list(api("/file/print")):
            name = row.get("name", "")
            if name.startswith("nexus-api-") and name.endswith(".rsc"):
                file_id = row.get(".id")
                if not file_id:
                    continue
                try:
                    api("/file/remove", **{".id": file_id})
                    removidos += 1
                except Exception:
                    log.warning("falha removendo orfao %s", name)
    except Exception:
        log.exception("falha no cleanup de orfaos nexus-api-*")
    if removidos:
        log.info("Plano C: removidos %d arquivos orfaos nexus-api-*.rsc", removidos)
    return removidos


def aplicar_config_padrao(device_id: int) -> tuple[bool, str]:
    """Aplica NTP cliente (apontando pro nosso server) + timezone via API.

    Idempotente — se já está configurado igual, RouterOS não dá erro.
    Best-effort: caller deve tratar exception/false sem hard fail (o backup
    não precisa disso pra funcionar).

    Chamado após criação do device API (uma vez). NÃO chamado em todo backup
    pra evitar mexer em config do equipamento sem ação consciente do admin.

    Recebe device_id (não o objeto Device) porque caller roda em thread
    separada via to_thread — passar objeto de AsyncSession entre threads
    pode disparar erros opacos no SQLAlchemy. Recarrega via SyncSession
    aqui, autossuficiente.
    """
    if not settings.FTP_MASQUERADE_ADDRESS:
        return False, "FTP_MASQUERADE_ADDRESS vazio — sem endereço pro NTP."

    with SyncSessionLocal() as db:
        device = db.execute(select(Device).where(Device.id == device_id)).scalar_one_or_none()
    if not device:
        return False, f"device id={device_id} não encontrado no banco"

    try:
        api = _connect(device)
    except Exception as e:
        return False, f"conexão API falhou: {e}"

    erros = []
    try:
        # Timezone — sintaxe igual em v6 e v7. list() força o iterator do
        # librouteros a consumir o reply — sem isso o comando NÃO é
        # efetivamente enviado pra rede (bug encontrado em prod 2026-05-11).
        try:
            list(api("/system/clock/set", **{
                "time-zone-name": "America/Sao_Paulo",
                "time-zone-autodetect": "no",
            }))
        except Exception as e:
            erros.append(f"timezone: {e}")

        # NTP — v7 usa `servers=`, v6 usa `primary-ntp=`. Tenta v7 primeiro;
        # se firmware não reconhecer (TrapError unknown parameter em v6),
        # cai pra v6. Idempotente em ambas.
        try:
            list(api("/system/ntp/client/set",
                     enabled="yes",
                     servers=settings.FTP_MASQUERADE_ADDRESS))
        except Exception:
            try:
                list(api("/system/ntp/client/set",
                         enabled="yes",
                         **{"primary-ntp": settings.FTP_MASQUERADE_ADDRESS}))
            except Exception as e:
                erros.append(f"ntp: {e}")
    finally:
        try:
            api.close()
        except Exception:
            pass

    if erros:
        return False, "; ".join(erros)
    return True, f"NTP={settings.FTP_MASQUERADE_ADDRESS}, timezone=America/Sao_Paulo"


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
        # Plano A — /export direto via API (mais rápido quando funciona)
        texto = _export_via_command(api)
        if texto and texto.strip():
            return "sucesso", texto

        # Plano C — upload FTP iniciado pelo Mikrotik. Robusto pra arquivos
        # de qualquer tamanho. Exige FTP_MASQUERADE_ADDRESS no .env + cred FTP
        # gerada no device (auto desde v1.4.4) + policy 'write,ftp' no grupo
        # do usuário no Mikrotik (read padrão não basta).
        texto = _export_via_upload_ftp(api, device)
        if texto and texto.strip():
            return "sucesso", texto
        return "falha", "Export retornou vazio em todos os planos (A: vazio; C: arquivo recebido vazio)."
    except Exception as e:
        return "falha", f"Erro coletando export via API: {e}"
    finally:
        try:
            api.close()
        except Exception:
            pass
