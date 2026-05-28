"""Endpoints de checagem de versão e canal de atualização (v2.3.0).

`/check` retorna estado completo (current, canal atual, latest_lts, latest_edge,
target, dias_em_producao, changelog). Qualquer user autenticado pode consultar.

`/channel` permite admin master trocar entre 'lts' (estável, default) e
'edge' (releases mais novas, incluindo Pre-release).

Update propriamente dito segue manual via SSH na Fase 1 — a Fase 2 vai
adicionar `/api/update/trigger` + systemd path unit no host.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from auth import get_current_user, require_role
from database import get_db
from models import Configuracao, User, UserRole
from schemas import UpdateChannelIn, UpdateChannelOut, VersionCheckOut
from services.version_check import check_version, to_dict

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
