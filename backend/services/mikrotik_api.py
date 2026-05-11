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
import ssl
import time
import logging
from typing import Tuple

from sqlalchemy import select
from models import Device, DeviceVendor, Protocolo, Backup
from services.crypto import decrypt
from config import settings
from services.push_backup import SyncSessionLocal

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


def _export_via_upload_sftp(api, device: Device) -> str:
    """Plano C: faz o Mikrotik gerar /export file=tmp.rsc e usar /tool/fetch
    upload=yes mode=sftp pra empurrar o arquivo pro nosso SFTP server.

    Usa cred SFTP auto-gerada do device (Device.ftp_user + ftp_senha_enc).
    Pipeline normal de push processa o arquivo e cria backup no banco;
    pollamos a tabela `backups` esperando esse backup novo aparecer, lemos
    o conteúdo, deletamos esse backup (pra evitar duplicação) e retornamos
    o conteúdo pro caller criar backup com origem='manual' como nos outros
    fluxos.

    Por que esse "deleta-e-retorna" em vez de retornar status-só:
    - Caller (routers/backups.py) sempre cria um Backup com o tupple
      (status, conteudo). Se retornássemos "sucesso, async", precisaria
      ajustar toda a chain. Tem o pequeno custo de uma row a mais por
      poucos ms no banco, mas é transparente pro resto do código.
    """
    if not device.ftp_user or not device.ftp_senha_enc:
        raise RuntimeError(
            "Device API sem credencial SFTP gerada (ftp_user/ftp_senha). "
            "Recadastre o device — em v1.4.4+ a cred é gerada automaticamente."
        )
    if not settings.FTP_MASQUERADE_ADDRESS:
        raise RuntimeError(
            "FTP_MASQUERADE_ADDRESS não configurado no .env. "
            "Sem isso o Mikrotik não tem endereço pra enviar o backup."
        )

    senha_sftp = decrypt(device.ftp_senha_enc)
    nome_base = f"nexus-api-{device.id}-{int(time.time())}"
    nome_rsc = f"{nome_base}.rsc"
    nome_destino = f"backup-api-{device.id}-{int(time.time())}.rsc"

    # 1) Gera o /export no /file do Mikrotik. Args iguais ao Plano B.
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

    # 2) Snapshot do último backup ID antes do upload — pra detectar qual
    # backup é o "novo" criado pela pipeline SFTP (push).
    with SyncSessionLocal() as db:
        ultimo_antes = db.execute(
            select(Backup.id).where(Backup.device_id == device.id)
            .order_by(Backup.id.desc()).limit(1)
        ).scalar_one_or_none() or 0

    # 3) Dispara /tool/fetch upload=yes mode=sftp pro nosso server.
    fetch_args = {
        "upload": "yes",
        "mode": "sftp",
        "address": settings.FTP_MASQUERADE_ADDRESS,
        "port": "22",
        "user": device.ftp_user,
        "password": senha_sftp,
        "src-path": nome_rsc,
        "dst-path": nome_destino,
    }
    try:
        list(api("/tool/fetch", **fetch_args))
    except Exception as e:
        # Cleanup do arquivo temp no Mikrotik antes de levantar.
        _try_remove_file(api, nome_rsc)
        raise RuntimeError(f"Falha no /tool/fetch upload pro SFTP do NEXUS: {e}")

    # 4) Polla a tabela backups por backup novo (criado pela pipeline SFTP)
    # com timeout generoso. Pipeline normal é rápida (segundos), mas dev em
    # rede lenta / arquivo grande pode demorar mais.
    deadline = time.time() + 60.0
    backup_novo_id = None
    backup_conteudo = None
    while time.time() < deadline:
        with SyncSessionLocal() as db:
            row = db.execute(
                select(Backup.id, Backup.conteudo, Backup.status)
                .where(Backup.device_id == device.id, Backup.id > ultimo_antes)
                .order_by(Backup.id.desc()).limit(1)
            ).first()
            if row:
                backup_novo_id = row[0]
                backup_conteudo = row[1] if row[2] == "sucesso" else None
                break
        time.sleep(0.5)

    # 5) Cleanup do arquivo temp no Mikrotik (a essa altura ele já foi
    # uploadado; remoção é responsabilidade nossa pra não deixar lixo).
    _try_remove_file(api, nome_rsc)

    if backup_novo_id is None:
        raise RuntimeError(
            f"/tool/fetch disparado mas backup não chegou em 60s. Possíveis "
            f"causas: Mikrotik sem rota pra {settings.FTP_MASQUERADE_ADDRESS}:22, "
            "firewall do NEXUS bloqueando, ou cred SFTP incorreta. Verifique "
            "logs do SFTP server."
        )
    if not backup_conteudo:
        # Apaga o row de falha antes de levantar pra não duplicar com a falha
        # que vai vir pelo caller.
        with SyncSessionLocal() as db:
            db.execute(Backup.__table__.delete().where(Backup.id == backup_novo_id))
            db.commit()
        raise RuntimeError("Backup chegou via SFTP mas marcado como falha pela pipeline (arquivo vazio/corrompido).")

    # 6) Apaga o backup que a pipeline criou pra evitar duplicação — o caller
    # vai criar novo Backup com origem='manual' usando o conteúdo retornado.
    with SyncSessionLocal() as db:
        db.execute(Backup.__table__.delete().where(Backup.id == backup_novo_id))
        db.commit()

    return backup_conteudo


def _try_remove_file(api, nome: str) -> None:
    """Remove arquivo do /file no Mikrotik silenciosamente."""
    try:
        arq = _find_file(api, nome)
        if arq and ".id" in arq:
            api("/file/remove", **{".id": arq[".id"]})
    except Exception:
        log.exception("falha ao remover arquivo temp %s do device", nome)


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

        # Plano C — upload SFTP iniciado pelo Mikrotik. Pula o Plano B (file/print
        # .contents) porque ele tem limite de ~4KB no firmware Mikrotik que
        # quebra silenciosamente em configs maiores. O Plano C é robusto pra
        # arquivos de qualquer tamanho — exige só FTP_MASQUERADE_ADDRESS
        # configurado no .env e cred SFTP gerada no device (auto desde v1.4.4).
        texto = _export_via_upload_sftp(api, device)
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
