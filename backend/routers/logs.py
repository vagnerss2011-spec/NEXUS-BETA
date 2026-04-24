from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from database import get_db
from models import LogScheduler, Backup, Device, UserRole
from auth import get_current_user, require_role
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/api/logs", tags=["logs"])


class LogSchedulerOut(BaseModel):
    id: int
    inicio: datetime
    fim: Optional[datetime]
    total: Optional[int]
    sucessos: Optional[int]
    falhas: Optional[int]
    erro_geral: Optional[str]
    criado_em: datetime

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
    return result.scalars().all()


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
