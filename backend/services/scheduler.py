from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal
from models import Device, Backup
from services.ssh_service import run_backup
from services.crypto import encrypt
from datetime import datetime, timedelta
from config import settings

scheduler = AsyncIOScheduler()

async def executar_backups():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Device).where(Device.ativo == True))
        devices = result.scalars().all()
        for device in devices:
            await _backup_device(db, device)

async def _backup_device(db: AsyncSession, device: Device):
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

async def _limpar_backups_antigos(db: AsyncSession, device_id: int):
    result = await db.execute(
        select(Backup.id)
        .where(Backup.device_id == device_id)
        .order_by(Backup.criado_em.desc())
        .offset(settings.BACKUP_RETENTION_DAYS)
    )
    ids_antigos = [row[0] for row in result.fetchall()]
    if ids_antigos:
        await db.execute(delete(Backup).where(Backup.id.in_(ids_antigos)))

def iniciar_scheduler():
    scheduler.add_job(executar_backups, "cron", hour=2, minute=0, id="backup_diario")
    scheduler.start()
