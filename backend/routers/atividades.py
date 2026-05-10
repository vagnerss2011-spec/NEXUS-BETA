from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Atividade, Empresa, TipoAtividade, User
from auth import get_current_user, is_master
from schemas import AtividadeOut
from typing import List, Optional

router = APIRouter(prefix="/api/atividades", tags=["atividades"])

@router.get("/", response_model=List[AtividadeOut])
async def listar_atividades(
    empresa_id: Optional[int] = Query(default=None),
    tipos: Optional[str] = Query(
        default=None,
        description="CSV de TipoAtividade — ex.: 'ftp_backup_recebido,ftp_backup_falha'. "
                    "Tipos desconhecidos são silenciosamente ignorados.",
    ),
    limit: int = Query(default=50, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = (
        select(Atividade, Empresa.nome.label("empresa_nome"))
        .outerjoin(Empresa, Empresa.id == Atividade.empresa_id)
        .order_by(Atividade.criado_em.desc())
        .limit(limit)
    )
    if is_master(user):
        if empresa_id is not None:
            q = q.where(Atividade.empresa_id == empresa_id)
    else:
        if user.empresa_id is None:
            return []
        q = q.where(Atividade.empresa_id == user.empresa_id)

    if tipos:
        # Parse CSV → set de TipoAtividade. Strings inválidas são puladas
        # (resiliente a typo do caller; nunca 400 por tipo errado).
        tipos_validos = []
        for t in tipos.split(","):
            t = t.strip()
            try:
                tipos_validos.append(TipoAtividade(t))
            except ValueError:
                continue
        if tipos_validos:
            q = q.where(Atividade.tipo.in_(tipos_validos))
        else:
            return []  # caller pediu filtro mas nenhum tipo válido — sem resultados

    rows = (await db.execute(q)).all()
    out: List[AtividadeOut] = []
    for atv, empresa_nome in rows:
        item = AtividadeOut.model_validate(atv)
        item.empresa_nome = empresa_nome
        out.append(item)
    return out
