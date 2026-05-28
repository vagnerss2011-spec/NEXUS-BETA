"""Orquestra updates disparados pelo painel (v2.4.0 — Fase 2 do auto-update).

Como funciona (resumo):
1. Admin clica "Atualizar pelo painel" → frontend chama POST /api/version/update/trigger
2. Backend valida (admin master, versão alvo válida, nada em andamento) e:
   - Cria row UpdateLog (status=queued)
   - Escreve `request.json` em /var/lib/nexus/update/ (volume bind-mounted)
3. No HOST, um systemd path unit (nexus-update.path) watches o `request.json`
   e dispara nexus-update.service, que roda /usr/local/bin/nexus-update.sh:
   - Lê o request.json
   - Escreve status.json com state=running
   - git fetch + git checkout <tag>
   - docker compose up -d --build backend frontend
   - Escreve status.json com state=success ou state=failed
   - Remove request.json
4. Frontend faz polling em GET /api/version/update/status durante o update.

O backend nunca toca em docker.sock — toda a parte privilegiada roda no host
via systemd. Se o helper não está instalado (servidores antigos), o backend
detecta e a UI mostra orientação pra rodar scripts/setup-update-helper.sh.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import UpdateLog

log = logging.getLogger("nexus.update")

# Diretório bind-mounted entre backend e host. O host helper deve apontar o
# systemd PathChanged pra REQUEST_FILE_HOST_PATH (= raiz do host + 'request.json').
UPDATE_DIR = Path("/var/lib/nexus/update")
REQUEST_FILE = UPDATE_DIR / "request.json"
STATUS_FILE = UPDATE_DIR / "status.json"
# Marker criado pelo setup-update-helper.sh confirmando que o host helper
# está instalado e o systemd path unit habilitado. Sem o marker, o backend
# recusa requests pra não criar trigger file que ninguém vai consumir.
HOST_READY_MARKER = UPDATE_DIR / "host-ready"

# Estados válidos no fluxo. `rolled_back` (v2.5.0) sinaliza que o update foi
# disparado, falhou no health check pós-rebuild e o script REVERTEU pra
# versão anterior automaticamente — sistema está saudável (na versão velha),
# mas o admin precisa saber que a tentativa não vingou.
ESTADOS_ATIVOS = ("queued", "running")
ESTADOS_FINAIS = ("success", "failed", "rolled_back")


def _ensure_dir():
    """Cria o diretório se não existir. O volume já vem do docker-compose,
    mas a 1ª request precisa garantir o subdiretório."""
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("não consegui criar %s: %s", UPDATE_DIR, e)


def host_helper_disponivel() -> bool:
    """O host helper foi instalado? Confere o marker file criado pelo
    setup-update-helper.sh. Instâncias pré-Fase-2 (sem o helper) recebem False."""
    return HOST_READY_MARKER.exists()


def ler_status_arquivo() -> Optional[dict]:
    """Lê status.json escrito pelo script no host. None = ainda não escreveu."""
    try:
        if not STATUS_FILE.exists():
            return None
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("falha lendo %s: %s", STATUS_FILE, e)
        return None


def request_pendente() -> bool:
    """Há request file que ainda não foi consumido pelo host?"""
    return REQUEST_FILE.exists()


async def update_ativo_em_db(db: AsyncSession) -> Optional[UpdateLog]:
    """Última row do UpdateLog que ainda está em estado ativo (queued/running)."""
    rows = (await db.execute(
        select(UpdateLog)
        .where(UpdateLog.status.in_(ESTADOS_ATIVOS))
        .order_by(UpdateLog.iniciado_em.desc())
        .limit(1)
    )).scalar_one_or_none()
    return rows


async def ultimo_log(db: AsyncSession) -> Optional[UpdateLog]:
    return (await db.execute(
        select(UpdateLog).order_by(UpdateLog.iniciado_em.desc()).limit(1)
    )).scalar_one_or_none()


def escrever_request(versao_de: str, versao_para: str, canal: str,
                     usuario_nome: str, log_id: int) -> None:
    """Cria/atualiza request.json. Escrita atômica (tempfile + rename) pra que
    o systemd path unit nunca veja conteúdo parcial."""
    _ensure_dir()
    payload = {
        "log_id": log_id,
        "versao_de": versao_de,
        "versao_para": versao_para,
        "canal": canal,
        "usuario_nome": usuario_nome,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = REQUEST_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, REQUEST_FILE)
    log.info("update request escrito: log_id=%s versao=%s -> %s", log_id, versao_de, versao_para)


async def sync_status_to_db(db: AsyncSession) -> Optional[UpdateLog]:
    """Lê status.json do host e atualiza o UpdateLog correspondente.

    Idempotente: pode chamar quantas vezes quiser, só aplica mudança se o
    status do arquivo for "mais avançado" que o do banco. Garante que o
    audit no banco fique consistente mesmo se a UI/backend reiniciar no meio.
    Retorna o UpdateLog atualizado (ou o último, se nada a sincronizar).
    """
    status = ler_status_arquivo()
    ultimo = await ultimo_log(db)
    if not status or not ultimo:
        return ultimo
    log_id = status.get("log_id")
    if log_id != ultimo.id:
        # Status pertence a um log diferente (script antigo + reinício do backend
        # que perdeu o trace). Ignora — não vamos sobrescrever audit aleatório.
        return ultimo
    novo_estado = status.get("state")
    if not novo_estado or novo_estado == ultimo.status:
        return ultimo
    # Só atualiza pra estados "à frente" no fluxo
    ordem = {"queued": 0, "running": 1, "success": 2, "failed": 2, "rolled_back": 2}
    if ordem.get(novo_estado, -1) <= ordem.get(ultimo.status, -1) and novo_estado not in ESTADOS_FINAIS:
        return ultimo
    ultimo.status = novo_estado
    if status.get("mensagem"):
        ultimo.mensagem = status["mensagem"][:5000]  # truncate defensivo
    if novo_estado in ESTADOS_FINAIS and ultimo.concluido_em is None:
        ultimo.concluido_em = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(ultimo)
    return ultimo
