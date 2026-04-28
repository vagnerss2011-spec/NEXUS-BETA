import traceback
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal
from models import Device, Backup, Configuracao, LogScheduler
from services.ssh_service import run_backup
from services.telegram import enviar_alerta_async
from config import settings

scheduler = AsyncIOScheduler()

# Bundling: se falhas em uma run >= esse limite, manda 1 mensagem agregada
# em vez de N individuais — evita spammar o grupo do Telegram quando uma
# queda de internet derruba 50 devices ao mesmo tempo.
MAX_ALERTAS_INDIVIDUAIS = 5

PURGA_LOGS_JOB_ID = "purga_logs_diaria"


async def executar_backups():
    inicio = datetime.now(timezone.utc)
    log = LogScheduler(inicio=inicio)

    async with AsyncSessionLocal() as db:
        db.add(log)
        await db.flush()

        total = sucessos = falhas = 0
        # Coleta as falhas pra decidir individual vs bundle no fim da run.
        # Cada item: (device, erro_resumido, empresa_id)
        falhas_da_run: list[tuple[Device, str, int | None]] = []
        try:
            result = await db.execute(select(Device).where(Device.ativo == True))
            devices = result.scalars().all()
            total = len(devices)

            for device in devices:
                ok, erro_msg = await _backup_device(db, device, log.id)
                if ok:
                    sucessos += 1
                else:
                    falhas += 1
                    falhas_da_run.append((device, erro_msg, device.empresa_id))

            log.fim = datetime.now(timezone.utc)
            log.total = total
            log.sucessos = sucessos
            log.falhas = falhas
            await db.commit()

            await _disparar_alertas_telegram(db, falhas_da_run)

        except Exception:
            log.fim = datetime.now(timezone.utc)
            log.total = total
            log.sucessos = sucessos
            log.falhas = falhas
            log.erro_geral = traceback.format_exc()
            await db.commit()


async def _disparar_alertas_telegram(
    db: AsyncSession, falhas: list[tuple[Device, str, int | None]],
) -> None:
    """Envia alertas Telegram para a run inteira.

    Lógica de bundling:
    - 0 falhas: silencioso (run perfeita não merece notificação)
    - 1 a MAX_ALERTAS_INDIVIDUAIS: 1 mensagem por device falhado, agrupado por empresa
      (mensagem vai pro chat da empresa quando ela tem chat próprio)
    - acima: 1 mensagem agregada por empresa, listando os devices

    Falhas com empresa_id=None (devices órfãos) viram alerta no chat default.
    """
    if not falhas:
        return

    # Agrupa por empresa pra respeitar override de chat_id por empresa
    por_empresa: dict[int | None, list[tuple[Device, str]]] = {}
    for dev, msg, emp_id in falhas:
        por_empresa.setdefault(emp_id, []).append((dev, msg))

    for emp_id, items in por_empresa.items():
        if len(items) <= MAX_ALERTAS_INDIVIDUAIS:
            for dev, msg in items:
                titulo = f"❌ Falha de backup — {dev.nome}"
                detalhes = (
                    f"<b>IP:</b> <code>{dev.ip}</code>\n"
                    f"<b>Fabricante:</b> {dev.fabricante.value if dev.fabricante else '—'}\n"
                    f"<b>Erro:</b> <i>{(msg or '')[:300]}</i>"
                )
                await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="falha_backup")
        else:
            # Bundle agregado — lista nomes dos primeiros 10 e mostra contagem total.
            nomes = ", ".join(d.nome for d, _ in items[:10])
            mais = f" e mais {len(items) - 10}" if len(items) > 10 else ""
            titulo = f"⚠️ {len(items)} backups falharam"
            detalhes = (
                f"<b>Devices:</b> {nomes}{mais}\n"
                f"<i>Provável causa comum (rede, scheduler). Veja o painel para detalhes.</i>"
            )
            await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="falha_backup")


async def _backup_device(db: AsyncSession, device: Device, log_id: int) -> tuple[bool, str | None]:
    """Roda backup do device. Retorna (sucesso, erro_resumido).

    erro_resumido é None quando ok; caso contrário, primeiras linhas do erro
    pra alimentar a mensagem do Telegram (sem traceback completo).
    """
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
        if status == "sucesso":
            return True, None
        return False, (conteudo or "Falha sem detalhe").splitlines()[0][:300]
    except Exception:
        tb = traceback.format_exc()
        backup = Backup(
            device_id=device.id,
            log_scheduler_id=log_id,
            status="falha",
            erro=tb,
        )
        db.add(backup)
        await db.flush()
        return False, tb.splitlines()[-1][:300]


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
    # Purga de logs: todo dia às 03:00 (TZ do container — definido em docker-compose)
    scheduler.add_job(purgar_logs_antigos, "cron", hour=3, minute=0, id=PURGA_LOGS_JOB_ID)
    scheduler.start()
