"""Endpoint de checagem de versão.

Qualquer user autenticado pode consultar — operador/viewer também precisam
saber se há update disponível, mesmo que a ação de promover seja só
admin (manual via SSH, vide RELEASING.md).
"""
from fastapi import APIRouter, Depends, Query
from auth import get_current_user
from models import User
from services.version_check import check_version, to_dict

router = APIRouter(prefix="/api/version", tags=["version"])


@router.get("/check")
async def get_version_check(
    refresh: bool = Query(
        False,
        description="Força refresh do cache (TTL 1h normal). Use só pra debug — "
                    "abuso pode estourar rate limit do GitHub.",
    ),
    _user: User = Depends(get_current_user),
):
    """Estado atual de versão.

    Retorna sempre um payload completo. Em caso de falha (sem GITHUB_TOKEN,
    API GitHub fora, etc.), `latest=null` e `update_available=false` —
    frontend trata como "sem dados, sem banner".
    """
    result = await check_version(force=refresh)
    return to_dict(result)
