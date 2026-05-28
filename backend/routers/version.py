"""Endpoints de checagem de versão e canal de atualização (v2.3.0).

`/check` retorna estado completo (current, canal atual, latest_lts, latest_edge,
target, dias_em_producao, changelog). Qualquer user autenticado pode consultar.

`/channel` permite admin master trocar entre 'lts' (estável, default) e
'edge' (releases mais novas, incluindo Pre-release).

Update propriamente dito segue manual via SSH na Fase 1 — a Fase 2 vai
adicionar `/api/update/trigger` + systemd path unit no host.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from auth import get_current_user, require_role
from database import get_db
from models import Configuracao, UpdateLog, User, UserRole, LogScheduler
from schemas import (
    UpdateChannelIn, UpdateChannelOut, UpdateStatusOut, UpdateTriggerIn,
    VersionCheckOut,
)
from services import update_runner
from services.version_check import check_version, to_dict
from version import APP_VERSION

router = APIRouter(prefix="/api/version", tags=["version"])


async def _get_canal(db: AsyncSession) -> str:
    """Lê o canal atual da Configuracao (cria com default 'lts' se não existir)."""
    config = (await db.execute(select(Configuracao))).scalar_one_or_none()
    if config is None:
        # Instância nova / banco vazio — usa default. Não cria aqui pra não
        # competir com _get_or_create_config do router de settings; a 1ª
        # gravação acontece em outro fluxo.
        return "lts"
    return config.update_channel or "lts"


@router.get("/check", response_model=VersionCheckOut)
async def get_version_check(
    refresh: bool = Query(
        False,
        description="Força refresh do cache (TTL 1h normal). Use só pra debug — "
                    "abuso pode estourar rate limit do GitHub.",
    ),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Estado de versão da instância no canal escolhido.

    Em caso de falha (sem GITHUB_TOKEN, API GitHub fora, repo sem releases
    nem tags, etc.), retorna `target=null` / `update_available=false` — UI
    trata como 'sem dados, sem banner'.
    """
    canal = await _get_canal(db)
    result = await check_version(channel=canal, force=refresh)
    return to_dict(result)


@router.get("/channel", response_model=UpdateChannelOut)
async def get_channel(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return UpdateChannelOut(channel=await _get_canal(db))


@router.patch(
    "/channel", response_model=UpdateChannelOut,
    dependencies=[Depends(require_role(UserRole.admin))],
)
async def patch_channel(
    body: UpdateChannelIn,
    db: AsyncSession = Depends(get_db),
):
    """Troca o canal global da instância. Só admin master — afeta todos os
    usuários, não é decisão por empresa. Próxima checagem (pode ser <1h via
    cache) usa o canal novo."""
    config = (await db.execute(select(Configuracao))).scalar_one_or_none()
    if config is None:
        config = Configuracao(backup_hour=2, backup_minute=0, update_channel=body.channel)
        db.add(config)
    else:
        config.update_channel = body.channel
    await db.commit()
    # Não invalida o cache aqui — o cache em version_check é por canal,
    # então a próxima request com o canal novo já busca dados frescos.
    return UpdateChannelOut(channel=body.channel)


# ───────────────────────── Update via painel (Fase 2 — v2.4.0) ─────────────────────────


def _normalizar_tag(t: str) -> str:
    """Garante prefixo 'v' (canonical) — usuário pode digitar com ou sem."""
    t = (t or "").strip()
    return t if t.startswith("v") else f"v{t}"


def _versao_recente(t: str) -> bool:
    return _normalizar_tag(t) == _normalizar_tag(APP_VERSION)


async def _scheduler_em_andamento(db: AsyncSession) -> bool:
    """Algum job do scheduler diário rodando agora? Janela de 2h pra cobrir
    jobs longos (multi-empresa). Bloqueia update pra não interromper coleta."""
    corte = datetime.now(timezone.utc) - timedelta(hours=2)
    row = (await db.execute(
        select(LogScheduler.id)
        .where(LogScheduler.fim.is_(None), LogScheduler.inicio >= corte)
        .limit(1)
    )).scalar_one_or_none()
    return row is not None


def _status_payload_idle(host_ok: bool) -> UpdateStatusOut:
    return UpdateStatusOut(state="idle", host_helper_disponivel=host_ok)


def _status_payload_from_log(row: UpdateLog, host_ok: bool) -> UpdateStatusOut:
    return UpdateStatusOut(
        state=row.status,
        versao_de=row.versao_de,
        versao_para=row.versao_para,
        canal=row.canal,
        iniciado_em=row.iniciado_em.isoformat() if row.iniciado_em else None,
        concluido_em=row.concluido_em.isoformat() if row.concluido_em else None,
        mensagem=row.mensagem,
        usuario_nome=row.usuario_nome,
        host_helper_disponivel=host_ok,
    )


@router.get("/update/status", response_model=UpdateStatusOut)
async def get_update_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Estado do último update disparado. Sincroniza com status.json do host
    a cada chamada (idempotente) — frontend faz polling enquanto in queued/running.
    """
    # Sincroniza primeiro pra refletir o que o host helper já escreveu.
    await update_runner.sync_status_to_db(db)
    host_ok = update_runner.host_helper_disponivel()
    row = await update_runner.ultimo_log(db)
    if not row:
        return _status_payload_idle(host_ok)
    return _status_payload_from_log(row, host_ok)


@router.post(
    "/update/trigger", response_model=UpdateStatusOut,
    dependencies=[Depends(require_role(UserRole.admin))],
)
async def trigger_update(
    body: UpdateTriggerIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Dispara o update via painel — escreve trigger file, audit, retorna status.

    Validações em ordem:
      1. host helper instalado (marker file)
      2. confirm_version bate com target_tag (anti-acidente)
      3. target_tag é uma tag conhecida do canal atual (latest_lts ou latest_edge)
      4. target_tag é mais novo que a versão atual (sem rollback pelo painel)
      5. nenhum update já em queued/running
      6. nenhum scheduler de backup em andamento (janela de 2h)
    """
    host_ok = update_runner.host_helper_disponivel()
    if not host_ok:
        raise HTTPException(
            status_code=503,
            detail="Helper de update do host não instalado. Rode `bash scripts/setup-update-helper.sh` no servidor (uma vez)."
        )

    target = _normalizar_tag(body.target_tag)
    confirm = _normalizar_tag(body.confirm_version)
    if target != confirm:
        raise HTTPException(status_code=400, detail="A confirmação não bate com a versão alvo digitada.")

    if _versao_recente(target):
        raise HTTPException(status_code=400, detail="Versão alvo é a mesma já instalada.")

    # Versão precisa ser uma das releases que o backend conhece no canal atual.
    canal = await _get_canal(db)
    vc = await check_version(channel=canal)
    candidatos = {r.tag for r in (vc.latest_lts, vc.latest_edge) if r}
    if target not in candidatos:
        raise HTTPException(
            status_code=400,
            detail=f"Versão alvo {target} não é uma release conhecida do canal {canal}. "
                   "Atualize a checagem ou crie o Release no GitHub primeiro."
        )

    # Anti-update-concorrente
    em_andamento = await update_runner.update_ativo_em_db(db)
    if em_andamento:
        raise HTTPException(
            status_code=409,
            detail=f"Já há um update em andamento (status={em_andamento.status}). "
                   "Aguarde concluir antes de disparar outro."
        )

    if await _scheduler_em_andamento(db):
        raise HTTPException(
            status_code=409,
            detail="Há job de backup do scheduler em andamento. Tente novamente após a coleta terminar."
        )

    # Audit primeiro, depois trigger file (se o backend cair entre os dois,
    # fica row em queued sem trigger — admin vê e pode tentar de novo).
    log_row = UpdateLog(
        versao_de=APP_VERSION,
        versao_para=target,
        canal=canal,
        status="queued",
        usuario_id=user.id,
        usuario_nome=user.nome,
    )
    db.add(log_row)
    await db.flush()
    update_runner.escrever_request(
        versao_de=APP_VERSION, versao_para=target, canal=canal,
        usuario_nome=user.nome, log_id=log_row.id,
    )
    await db.commit()
    await db.refresh(log_row)
    return _status_payload_from_log(log_row, host_ok)
