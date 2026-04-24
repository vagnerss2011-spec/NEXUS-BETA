from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Configuracao, UserRole
from schemas import ScheduleOut, ScheduleUpdate
from auth import require_role
from services.scheduler import atualizar_scheduler

router = APIRouter(prefix="/api/settings", tags=["settings"])


async def _get_or_create_config(db: AsyncSession) -> Configuracao:
    result = await db.execute(select(Configuracao))
    config = result.scalar_one_or_none()
    if config is None:
        config = Configuracao(backup_hour=2, backup_minute=0)
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


@router.get("/schedule", response_model=ScheduleOut)
async def get_schedule(db: AsyncSession = Depends(get_db)):
    return await _get_or_create_config(db)


@router.put("/schedule", response_model=ScheduleOut)
async def update_schedule(
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador)),
):
    if not (0 <= body.backup_hour <= 23):
        raise HTTPException(status_code=422, detail="Hora deve ser entre 0 e 23")
    if not (0 <= body.backup_minute <= 59):
        raise HTTPException(status_code=422, detail="Minuto deve ser entre 0 e 59")
    if body.log_retention_days is not None and not (0 <= body.log_retention_days <= 3650):
        raise HTTPException(status_code=422, detail="Retenção de logs deve ser entre 0 e 3650 dias")

    config = await _get_or_create_config(db)
    config.backup_hour = body.backup_hour
    config.backup_minute = body.backup_minute
    if body.log_retention_days is not None:
        config.log_retention_days = body.log_retention_days
    await db.commit()
    await db.refresh(config)

    atualizar_scheduler(config.backup_hour, config.backup_minute)
    return config
