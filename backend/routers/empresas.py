from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from database import get_db
from models import Empresa, User, UserRole, Device, Backup, Atividade, TipoAtividade
from auth import get_current_user, require_master, is_master
from schemas import EmpresaCreate, EmpresaUpdate, EmpresaOut, EmpresaStatsOut, UltimaAlteracaoDevice
from typing import List

router = APIRouter(prefix="/api/empresas", tags=["empresas"])

@router.get("/", response_model=List[EmpresaOut])
async def listar_empresas(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    q = select(Empresa).order_by(Empresa.nome)
    if not is_master(user):
        if user.empresa_id is None:
            return []
        q = q.where(Empresa.id == user.empresa_id)
    result = await db.execute(q)
    return result.scalars().all()

@router.get("/stats", response_model=List[EmpresaStatsOut])
async def listar_empresas_com_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Empresas acessíveis
    q = select(Empresa).order_by(Empresa.nome)
    if not is_master(user):
        if user.empresa_id is None:
            return []
        q = q.where(Empresa.id == user.empresa_id)
    empresas = (await db.execute(q)).scalars().all()
    if not empresas:
        return []
    ids = [e.id for e in empresas]

    # Último backup por device (DISTINCT ON do Postgres).
    last_bk = (
        select(Backup.device_id, Backup.status)
        .distinct(Backup.device_id)
        .order_by(Backup.device_id, Backup.criado_em.desc())
        .subquery()
    )

    # Contadores agregados por empresa.
    stats_rows = (await db.execute(
        select(
            Device.empresa_id,
            func.count(Device.id).label("total"),
            func.coalesce(func.sum(case((last_bk.c.status == "sucesso", 1), else_=0)), 0).label("sucessos"),
            func.coalesce(func.sum(case((last_bk.c.status == "falha", 1), else_=0)), 0).label("falhas"),
        )
        .select_from(Device)
        .outerjoin(last_bk, last_bk.c.device_id == Device.id)
        .where(Device.empresa_id.in_(ids))
        .group_by(Device.empresa_id)
    )).all()
    stats_map = {r.empresa_id: r for r in stats_rows}

    # Última atividade de add/remove device por empresa (DISTINCT ON).
    atv_rows = (await db.execute(
        select(
            Atividade.empresa_id,
            Atividade.tipo,
            Atividade.usuario_nome,
            Atividade.alvo_nome,
            Atividade.criado_em,
        )
        .where(
            Atividade.empresa_id.in_(ids),
            Atividade.tipo.in_([TipoAtividade.device_criado, TipoAtividade.device_removido]),
        )
        .distinct(Atividade.empresa_id)
        .order_by(Atividade.empresa_id, Atividade.criado_em.desc())
    )).all()
    atv_map = {r.empresa_id: r for r in atv_rows}

    out: List[EmpresaStatsOut] = []
    for e in empresas:
        s = stats_map.get(e.id)
        total = int(s.total) if s else 0
        sucessos = int(s.sucessos) if s else 0
        falhas = int(s.falhas) if s else 0
        sem_backup = max(total - sucessos - falhas, 0)
        ua = atv_map.get(e.id)
        ultima = (
            UltimaAlteracaoDevice(
                tipo=ua.tipo.value if hasattr(ua.tipo, "value") else str(ua.tipo),
                usuario_nome=ua.usuario_nome,
                alvo_nome=ua.alvo_nome,
                criado_em=ua.criado_em,
            ) if ua else None
        )
        out.append(EmpresaStatsOut(
            id=e.id, nome=e.nome, cnpj=e.cnpj, ativo=e.ativo, criado_em=e.criado_em,
            total_devices=total, sucessos=sucessos, falhas=falhas, sem_backup=sem_backup,
            ultima_alteracao=ultima,
        ))
    return out

@router.post("/", response_model=EmpresaOut, dependencies=[Depends(require_master())])
async def criar_empresa(data: EmpresaCreate, db: AsyncSession = Depends(get_db)):
    exist = await db.execute(select(Empresa).where(Empresa.nome == data.nome))
    if exist.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Já existe uma empresa com esse nome")
    empresa = Empresa(nome=data.nome, cnpj=data.cnpj)
    db.add(empresa)
    await db.commit()
    await db.refresh(empresa)
    return empresa

@router.get("/{empresa_id}", response_model=EmpresaOut)
async def obter_empresa(empresa_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if not is_master(user) and user.empresa_id != empresa_id:
        raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalar_one_or_none()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return empresa

@router.put("/{empresa_id}", response_model=EmpresaOut)
async def atualizar_empresa(
    empresa_id: int, data: EmpresaUpdate,
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    # admin master: qualquer empresa. admin_empresa: só a própria.
    if not is_master(user):
        if user.role != UserRole.admin_empresa or user.empresa_id != empresa_id:
            raise HTTPException(status_code=403, detail="Permissão insuficiente")
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalar_one_or_none()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(empresa, field, value)
    await db.commit()
    await db.refresh(empresa)
    return empresa

@router.delete("/{empresa_id}", dependencies=[Depends(require_master())])
async def deletar_empresa(empresa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalar_one_or_none()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    await db.delete(empresa)
    await db.commit()
    return {"ok": True}
