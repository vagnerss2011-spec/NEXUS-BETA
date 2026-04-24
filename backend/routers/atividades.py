from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Atividade, User
from auth import get_current_user, is_master
from schemas import AtividadeOut
from typing import List, Optional

router = APIRouter(prefix="/api/atividades", tags=["atividades"])

@router.get("/", response_model=List[AtividadeOut])
async def listar_atividades(
    empresa_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Atividade).order_by(Atividade.criado_em.desc()).limit(limit)
    if is_master(user):
        if empresa_id is not None:
            q = q.where(Atividade.empresa_id == empresa_id)
    else:
        if user.empresa_id is None:
            return []
        q = q.where(Atividade.empresa_id == user.empresa_id)
    result = await db.execute(q)
    return result.scalars().all()
