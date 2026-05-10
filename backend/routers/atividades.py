from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from database import get_db
from models import Atividade, Empresa, TipoAtividade, User, UserRole
from auth import get_current_user, is_master, require_role
from schemas import AtividadeOut
from typing import List, Optional

router = APIRouter(prefix="/api/atividades", tags=["atividades"])


def _parse_tipos_csv(tipos: Optional[str]) -> Optional[List[TipoAtividade]]:
    """Parse CSV de TipoAtividade. Retorna None se input é None/vazio,
    lista vazia se nenhum tipo válido (caller deve tratar como 'no-op'),
    ou lista de TipoAtividade. Tipos inválidos são silenciosamente ignorados."""
    if not tipos:
        return None
    out: List[TipoAtividade] = []
    for t in tipos.split(","):
        t = t.strip()
        try:
            out.append(TipoAtividade(t))
        except ValueError:
            continue
    return out

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

    tipos_validos = _parse_tipos_csv(tipos)
    if tipos_validos is not None:
        if not tipos_validos:
            return []  # caller pediu filtro mas nenhum tipo válido — sem resultados
        q = q.where(Atividade.tipo.in_(tipos_validos))

    rows = (await db.execute(q)).all()
    out: List[AtividadeOut] = []
    for atv, empresa_nome in rows:
        item = AtividadeOut.model_validate(atv)
        item.empresa_nome = empresa_nome
        out.append(item)
    return out


@router.delete("/{atividade_id}", status_code=204,
               dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))])
async def deletar_atividade(
    atividade_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove uma atividade pontual. Útil pra limpar evento de push barulhento
    (ex.: brute-force banido pelo fail2ban que poluiu o log) sem apagar o resto.
    """
    atv = await db.get(Atividade, atividade_id)
    if not atv:
        raise HTTPException(status_code=404, detail="Atividade não encontrada")
    # admin_empresa só pode mexer em atividades da própria empresa.
    # Atividades com empresa_id=NULL (auth fail antes do device ser resolvido)
    # ficam restritas ao master — admin_empresa não tem como saber se é dele.
    if not is_master(user):
        if atv.empresa_id is None or atv.empresa_id != user.empresa_id:
            raise HTTPException(status_code=403, detail="Sem acesso a esta atividade")
    await db.delete(atv)
    await db.commit()
    return None


@router.delete("/", status_code=200,
               dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))])
async def deletar_atividades(
    tipos: Optional[str] = Query(
        default=None,
        description="CSV de tipos a apagar. Recomendado usar pra evitar wipe acidental "
                    "(ex.: tipos=ftp_backup_recebido,ftp_backup_falha,ftp_acesso_negado,ftp_volume_alto). "
                    "Sem o filtro, apaga TUDO (admin master só, escopo da empresa).",
    ),
    empresa_id: Optional[int] = Query(default=None,
        description="Restringe à empresa especificada. admin_empresa ignora este "
                    "parâmetro — sempre usa a empresa do próprio usuário."),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mass delete de atividades. Combinação de filtros tipos + empresa_id.

    - admin master sem filtro: apaga TUDO da tabela atividades (perigoso — UI
      sempre passa tipos= explícito pra evitar isso).
    - admin master + tipos: apaga só os tipos pedidos (ex.: limpa só push).
    - admin master + empresa_id: restringe à empresa.
    - admin_empresa: sempre restrito à própria empresa, ignora empresa_id.
    """
    q = delete(Atividade)

    if not is_master(user):
        if user.empresa_id is None:
            return {"ok": True, "removidos": 0}
        q = q.where(Atividade.empresa_id == user.empresa_id)
    elif empresa_id is not None:
        q = q.where(Atividade.empresa_id == empresa_id)

    tipos_validos = _parse_tipos_csv(tipos)
    if tipos_validos is not None:
        if not tipos_validos:
            return {"ok": True, "removidos": 0}
        q = q.where(Atividade.tipo.in_(tipos_validos))

    result = await db.execute(q)
    await db.commit()
    return {"ok": True, "removidos": result.rowcount or 0}
