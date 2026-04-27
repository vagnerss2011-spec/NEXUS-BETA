from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, case
from sqlalchemy.orm import selectinload
from database import get_db
from models import LogScheduler, Backup, Device, Empresa, UserRole
from auth import get_current_user, require_role
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/api/logs", tags=["logs"])


class EmpresaResumoLog(BaseModel):
    empresa_id: int
    nome: str
    sucessos: int
    falhas: int


class LogSchedulerOut(BaseModel):
    id: int
    inicio: datetime
    fim: Optional[datetime]
    total: Optional[int]
    sucessos: Optional[int]
    falhas: Optional[int]
    erro_geral: Optional[str]
    criado_em: datetime
    empresas: list[EmpresaResumoLog] = []

    class Config:
        from_attributes = True


class DeviceMinOut(BaseModel):
    id: int
    nome: str
    ip: str
    fabricante: str

    class Config:
        from_attributes = True


class BackupLogDetalhe(BaseModel):
    id: int
    status: str
    erro: Optional[str]
    criado_em: datetime
    device: Optional[DeviceMinOut]

    class Config:
        from_attributes = True


@router.get("/scheduler", response_model=list[LogSchedulerOut])
async def listar_logs(
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(LogScheduler)
        .order_by(LogScheduler.criado_em.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    if not logs:
        return []

    # Agrega sucessos/falhas por (log_scheduler_id, empresa) em uma única query.
    # Usado pelo painel do admin master para ver o "espalhamento" da execução.
    log_ids = [l.id for l in logs]
    rows = (await db.execute(
        select(
            Backup.log_scheduler_id,
            Empresa.id.label("empresa_id"),
            Empresa.nome.label("empresa_nome"),
            func.count(case((Backup.status == "sucesso", 1))).label("sucessos"),
            func.count(case((Backup.status == "falha", 1))).label("falhas"),
        )
        .join(Device, Backup.device_id == Device.id)
        .join(Empresa, Device.empresa_id == Empresa.id)
        .where(Backup.log_scheduler_id.in_(log_ids))
        .group_by(Backup.log_scheduler_id, Empresa.id, Empresa.nome)
        .order_by(Empresa.nome)
    )).all()

    por_log: dict[int, list[EmpresaResumoLog]] = {}
    for r in rows:
        por_log.setdefault(r.log_scheduler_id, []).append(EmpresaResumoLog(
            empresa_id=r.empresa_id, nome=r.empresa_nome,
            sucessos=int(r.sucessos or 0), falhas=int(r.falhas or 0),
        ))

    out: list[LogSchedulerOut] = []
    for l in logs:
        item = LogSchedulerOut.model_validate(l)
        item.empresas = por_log.get(l.id, [])
        out.append(item)
    return out


@router.get("/scheduler/{log_id}/backups", response_model=list[BackupLogDetalhe])
async def backups_do_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    log = await db.get(LogScheduler, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log não encontrado")

    result = await db.execute(
        select(Backup)
        .options(selectinload(Backup.device))
        .where(Backup.log_scheduler_id == log_id)
        .order_by(Backup.status)  # falhas primeiro
    )
    return result.scalars().all()


@router.delete("/scheduler/{log_id}",
               dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))])
async def deletar_log(log_id: int, db: AsyncSession = Depends(get_db)):
    log = await db.get(LogScheduler, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log não encontrado")
    await db.delete(log)
    await db.commit()
    return {"ok": True}


@router.delete("/scheduler",
               dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))])
async def deletar_todos_logs(db: AsyncSession = Depends(get_db)):
    """Remove TODOS os logs do scheduler. Backups vinculados ficam com log_scheduler_id=NULL (ON DELETE SET NULL)."""
    result = await db.execute(delete(LogScheduler))
    await db.commit()
    return {"ok": True, "removidos": result.rowcount or 0}
