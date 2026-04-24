from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database import get_db
from models import Backup, Device, UserRole
from auth import require_role, get_current_user
from schemas import BackupOut, BackupWithDevice
from services.ssh_service import run_backup
from services.scheduler import _limpar_backups_antigos
from typing import List

router = APIRouter(prefix="/api/backups", tags=["backups"])

@router.get("/", response_model=List[BackupWithDevice])
async def listar_backups(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(
        select(Backup).join(Device).order_by(Backup.criado_em.desc()).limit(200)
    )
    return result.scalars().all()

@router.get("/device/{device_id}", response_model=List[BackupOut])
async def backups_por_device(device_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(
        select(Backup).where(Backup.device_id == device_id).order_by(Backup.criado_em.desc())
    )
    return result.scalars().all()

@router.post("/run/{device_id}", response_model=BackupOut,
             dependencies=[Depends(require_role(UserRole.admin, UserRole.operador))])
async def executar_backup_manual(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")

    status, conteudo = run_backup(device)
    backup = Backup(
        device_id=device.id,
        status=status,
        conteudo=conteudo if status == "sucesso" else None,
        erro=conteudo if status == "falha" else None,
    )
    db.add(backup)
    await db.flush()
    await _limpar_backups_antigos(db, device.id)
    await db.commit()
    await db.refresh(backup)
    return backup

@router.get("/{backup_id}/download")
async def download_backup(backup_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    from fastapi.responses import PlainTextResponse
    result = await db.execute(select(Backup).where(Backup.id == backup_id))
    backup = result.scalar_one_or_none()
    if not backup or not backup.conteudo:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    return PlainTextResponse(content=backup.conteudo, media_type="text/plain",
                             headers={"Content-Disposition": f"attachment; filename=backup_{backup_id}.txt"})
