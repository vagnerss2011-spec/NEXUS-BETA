import traceback
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal
from models import Device, Backup, Configuracao, LogScheduler
from services.ssh_service import run_backup
from config import settings

scheduler = AsyncIOScheduler()

PURGA_LOGS_JOB_ID = "purga_logs_diaria"


async def executar_backups():
    inicio = datetime.now(timezone.utc)
    log = LogScheduler(inicio=inicio)

    async with AsyncSessionLocal() as db:
        db.add(log)
        await db.flush()

        total = sucessos = falhas = 0
        try:
            result = await db.execute(select(Device).where(Device.ativo == True))
            devices = result.scalars().all()
            total = len(devices)

            for device in devices:
                ok = await _backup_device(db, device, log.id)
                if ok:
                    sucessos += 1
                else:
                    falhas += 1

            log.fim = datetime.now(timezone.utc)
            log.total = total
            log.sucessos = sucessos
            log.falhas = falhas
            await db.commit()

        except Exception:
            log.fim = datetime.now(timezone.utc)
            log.total = total
            log.sucessos = sucessos
            log.falhas = falhas
            log.erro_geral = traceback.format_exc()
            await db.commit()


async def _backup_device(db: AsyncSession, device: Device, log_id: int) -> bool:
    try:
        status, conteudo = run_backup(device)
        backup = Backup(
            device_id=device.id,
            log_scheduler_id=log_id,
            status=status,
            conteudo=conteudo if status == "sucesso" else None,
            erro=conteudo if status == "falha" else None,
        )
        db.add(backup)
        await db.flush()
        await _limpar_backups_antigos(db, device.id)
        return status == "sucesso"
    except Exception:
        backup = Backup(
            device_id=device.id,
            log_scheduler_id=log_id,
            status="falha",
            erro=traceback.format_exc(),
        )
        db.add(backup)
        await db.flush()
        return False


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


async def purgar_logs_antigos():
    """Remove logs do scheduler mais velhos que Configuracao.log_retention_days.
    Se log_retention_days == 0, a purga fica desativada."""
    async with AsyncSessionLocal() as db:
        config = (await db.execute(select(Configuracao))).scalar_one_or_none()
        if not config or config.log_retention_days <= 0:
            return
        corte = datetime.now(timezone.utc) - timedelta(days=config.log_retention_days)
        await db.execute(delete(LogScheduler).where(LogScheduler.criado_em < corte))
        await db.commit()


async def _carregar_horario() -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Configuracao))
        config = result.scalar_one_or_none()
        if config:
            return config.backup_hour, config.backup_minute
        config = Configuracao(backup_hour=2, backup_minute=0)
        db.add(config)
        await db.commit()
        return 2, 0


def atualizar_scheduler(hour: int, minute: int):
    scheduler.reschedule_job("backup_diario", trigger="cron", hour=hour, minute=minute)


async def iniciar_scheduler():
    hour, minute = await _carregar_horario()
    scheduler.add_job(executar_backups, "cron", hour=hour, minute=minute, id="backup_diario")
    # Purga de logs: todo dia às 03:00 UTC
    scheduler.add_job(purgar_logs_antigos, "cron", hour=3, minute=0, id=PURGA_LOGS_JOB_ID)
    scheduler.start()
