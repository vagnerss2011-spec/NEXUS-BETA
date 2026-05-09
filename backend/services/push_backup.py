"""Lógica comum de processamento de backup recebido via push (FTP/SFTP/TFTP).

Cada servidor (ftp_server, sftp_server, tftp_server) tem sua própria forma de
autenticar e identificar o device, mas depois disso o que é feito com o
arquivo é idêntico:
- dedupe diário (substitui se já existe backup do device hoje)
- insere Backup com log_scheduler_id=NULL (origem manual/push)
- aplica retenção (mantém últimos N do device)
- registra atividade ftp_backup_recebido
- detecta volume alto (>5 uploads/24h → 1 alerta por janela)
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import sessionmaker, Session
from config import settings
from models import Device, Backup, Atividade, TipoAtividade
from services.telegram import enviar_alerta

# Engine SÍNCRONO compartilhado entre todos os servidores de push (FTP/SFTP/TFTP).
# A DATABASE_URL é configurada com +asyncpg para o resto do app; aqui trocamos
# pelo driver síncrono (psycopg2-binary já está no requirements).
_sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
sync_engine = create_engine(_sync_url, pool_size=5, max_overflow=10, pool_pre_ping=True)
SyncSessionLocal = sessionmaker(sync_engine, autoflush=False, autocommit=False)

VOLUME_ALTO_LIMITE = 5  # uploads/24h que disparam alerta


def processar_upload(device: Device, conteudo: str, ip: str | None, tamanho: int,
                     db: Session) -> None:
    """Aplica dedupe diário + insert + retenção + audit + alerta de volume.
    Caller responsabiliza pelo db.commit() (ou pode passar já em transação)."""
    agora = datetime.now(timezone.utc)
    inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)

    # Dedupe: substitui qualquer backup do device feito hoje
    db.execute(
        delete(Backup).where(
            Backup.device_id == device.id,
            Backup.criado_em >= inicio_dia,
        )
    )

    db.add(Backup(
        device_id=device.id, status="sucesso", conteudo=conteudo,
        erro=None, log_scheduler_id=None, origem="push",
    ))

    # Retenção: mantém últimos N (BACKUP_RETENTION_DAYS), apaga o resto.
    ids_excedentes = db.execute(
        select(Backup.id)
        .where(Backup.device_id == device.id)
        .order_by(Backup.criado_em.desc())
        .offset(settings.BACKUP_RETENTION_DAYS)
    ).scalars().all()
    if ids_excedentes:
        db.execute(delete(Backup).where(Backup.id.in_(ids_excedentes)))

    db.add(Atividade(
        tipo=TipoAtividade.ftp_backup_recebido, usuario_id=None,
        usuario_nome="ftp", empresa_id=device.empresa_id, ip=ip,
        alvo_tipo="device", alvo_nome=device.nome,
        detalhe=f"{tamanho} bytes",
    ))

    # Volume alto: 1 alerta por device por janela de 24h.
    corte = agora - timedelta(hours=24)
    uploads = db.execute(
        select(func.count(Atividade.id)).where(
            Atividade.tipo == TipoAtividade.ftp_backup_recebido,
            Atividade.alvo_nome == device.nome,
            Atividade.criado_em >= corte,
        )
    ).scalar() or 0
    if uploads >= VOLUME_ALTO_LIMITE:
        ja = db.execute(
            select(Atividade.id).where(
                Atividade.tipo == TipoAtividade.ftp_volume_alto,
                Atividade.alvo_nome == device.nome,
                Atividade.criado_em >= corte,
            )
        ).scalar_one_or_none()
        if not ja:
            db.add(Atividade(
                tipo=TipoAtividade.ftp_volume_alto, usuario_id=None,
                usuario_nome="ftp", empresa_id=device.empresa_id, ip=ip,
                alvo_tipo="device", alvo_nome=device.nome,
                detalhe=f"{uploads} uploads em 24h",
            ))
            # Alerta Telegram (1x por janela 24h, mesma logica de dedupe da atividade).
            # Falha-tolerante — não propaga se Telegram fora do ar.
            enviar_alerta(
                db,
                titulo=f"📈 Volume alto de uploads — {device.nome}",
                detalhes=(
                    f"<b>Device:</b> {device.nome}\n"
                    f"<b>IP origem:</b> <code>{ip or '?'}</code>\n"
                    f"<b>Uploads em 24h:</b> {uploads}\n"
                    f"<i>Limite normal: até {VOLUME_ALTO_LIMITE} uploads/24h. "
                    f"Verifique se está em loop ou se houve erro de scheduler do equipamento.</i>"
                ),
                empresa_id=device.empresa_id,
                categoria="volume_alto",
            )


def auditar_acesso_negado(db: Session, ip: str | None, alvo_nome: str | None,
                          empresa_id: int | None, detalhe: str) -> None:
    db.add(Atividade(
        tipo=TipoAtividade.ftp_acesso_negado, usuario_id=None,
        usuario_nome="ftp", empresa_id=empresa_id, ip=ip,
        alvo_tipo="device", alvo_nome=alvo_nome, detalhe=detalhe,
    ))
    db.commit()
    # Alerta Telegram. Em ambientes com brute-force ativo isso pode spammar —
    # fail2ban (jail nexus-ftp com docker-allports) bane o IP após 3 falhas em
    # 10 min, então o volume é limitado naturalmente.
    enviar_alerta(
        db,
        titulo="🛑 Acesso push negado",
        detalhes=(
            f"<b>IP origem:</b> <code>{ip or '?'}</code>\n"
            f"<b>Tentou usuário:</b> <code>{alvo_nome or '?'}</code>\n"
            f"<b>Motivo:</b> <i>{detalhe}</i>"
        ),
        empresa_id=empresa_id,
        categoria="push_negado",
    )
