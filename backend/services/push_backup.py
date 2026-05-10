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
                     db: Session, nome_arquivo: str | None = None,
                     protocolo: str = "FTP") -> None:
    """Aplica dedupe diário + insert + retenção + audit + alerta de volume.
    Caller responsabiliza pelo db.commit() (ou pode passar já em transação).

    nome_arquivo: nome original do arquivo recebido. Crítico pra UNM2000 que
    pode mandar múltiplos arquivos por dia (1 zip + N cfgs de OLTs distintas)
    usando a mesma credencial FTP — sem o nome, perde-se a identificação.

    protocolo: "FTP" | "SFTP" | "TFTP" — gravado no detalhe da Atividade pra
    a UI de Logs distinguir qual servidor recebeu (sem precisar consultar
    Device.protocolo no render).

    Dedupe: pra devices que recebem só 1 arquivo/dia (OLT direto), substitui
    o backup do dia. Pra UNM2000 (tipo='unm2000'), permite múltiplos arquivos
    por dia (não dedupa) — cada arquivo é um backup distinto."""
    agora = datetime.now(timezone.utc)
    inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)

    # Dedupe diário só pra devices não-UNM2000. UNM2000 envia múltiplos arquivos
    # distintos por dia (banco próprio + uma OLT cada arquivo) — dedupar
    # apagaria os arquivos anteriores e perderia tudo menos o último.
    if device.tipo is None or device.tipo.value != "unm2000":
        db.execute(
            delete(Backup).where(
                Backup.device_id == device.id,
                Backup.criado_em >= inicio_dia,
            )
        )

    db.add(Backup(
        device_id=device.id, status="sucesso", conteudo=conteudo,
        erro=None, log_scheduler_id=None, origem="push",
        nome_arquivo=nome_arquivo,
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
        detalhe=_formatar_detalhe(protocolo, _humanizar_bytes(tamanho), nome_arquivo),
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
                detalhe=_formatar_detalhe(protocolo, f"{uploads} uploads em 24h"),
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
                          empresa_id: int | None, detalhe: str,
                          protocolo: str = "FTP") -> None:
    db.add(Atividade(
        tipo=TipoAtividade.ftp_acesso_negado, usuario_id=None,
        usuario_nome="ftp", empresa_id=empresa_id, ip=ip,
        alvo_tipo="device", alvo_nome=alvo_nome,
        detalhe=_formatar_detalhe(protocolo, detalhe),
    ))
    db.commit()
    # Alerta Telegram. Em ambientes com brute-force ativo isso pode spammar —
    # fail2ban (jail nexus-ftp com docker-allports) bane o IP após 3 falhas em
    # 10 min, então o volume é limitado naturalmente.
    enviar_alerta(
        db,
        titulo="🛑 Acesso push negado",
        detalhes=(
            f"<b>Protocolo:</b> {protocolo}\n"
            f"<b>IP origem:</b> <code>{ip or '?'}</code>\n"
            f"<b>Tentou usuário:</b> <code>{alvo_nome or '?'}</code>\n"
            f"<b>Motivo:</b> <i>{detalhe}</i>"
        ),
        empresa_id=empresa_id,
        categoria="push_negado",
    )


def auditar_falha_push(db: Session, device: Device | None, ip: str | None,
                       motivo: str, protocolo: str,
                       nome_arquivo: str | None = None,
                       empresa_id: int | None = None) -> None:
    """Registra falha de upload APÓS autenticação bem-sucedida.

    Cobre o gap entre 'auth_ok' e 'backup persistido': arquivo de 0 bytes,
    tamanho excedido, exceção no parser binário, OSError lendo arquivo, etc.

    Distinto de auditar_acesso_negado: aqui a credencial foi aceita — o
    problema foi do lado do conteúdo/transferência. UI de Logs exibe com
    ícone diferente (warning vs. denied).

    Pode ser chamado com device=None em casos onde o auth foi por IP only
    (TFTP) e o device foi resolvido mas a sessão quebrou antes do persist —
    nesses casos passar empresa_id explicitamente se conhecido.
    """
    alvo_nome = device.nome if device else None
    emp = empresa_id if empresa_id is not None else (device.empresa_id if device else None)
    db.add(Atividade(
        tipo=TipoAtividade.ftp_backup_falha, usuario_id=None,
        usuario_nome="ftp", empresa_id=emp, ip=ip,
        alvo_tipo="device", alvo_nome=alvo_nome,
        detalhe=_formatar_detalhe(protocolo, motivo, nome_arquivo),
    ))
    db.commit()
    # Sem alerta Telegram aqui — falhas de upload podem entrar em loop com
    # device mal configurado e spammar. O alerta ftp_volume_alto cobre o
    # caso macro (volume anormal); pra cada falha individual a UI de Logs
    # já dá visibilidade suficiente.


# ───────────────────────── helpers internos ─────────────────────────

def _formatar_detalhe(protocolo: str, motivo: str,
                      nome_arquivo: str | None = None) -> str:
    """Padroniza o campo detalhe da Atividade pra UI parsear: 'PROTO · motivo[ · arquivo]'.

    Frontend faz split por ' · ' pra extrair as partes (ver Logs.jsx).
    """
    partes = [protocolo, motivo]
    if nome_arquivo:
        partes.append(nome_arquivo)
    return " · ".join(partes)


def _humanizar_bytes(n: int) -> str:
    """Formata bytes pra leitura humana (ex.: 8421 → '8.2 KB', 3500000 → '3.3 MB').

    Sem dependência externa — i18n simples (KB/MB/GB) cobre todos os tamanhos
    plausíveis de backup (limite hard hoje é 50 MB pro UNM2000).
    """
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"
