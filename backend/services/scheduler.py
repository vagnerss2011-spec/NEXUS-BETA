import asyncio
import logging
import time
import traceback
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal
from models import Device, Backup, Configuracao, LogScheduler, Protocolo
from services.ssh_service import run_backup
from services.telegram import enviar_alerta_async
from config import settings

log = logging.getLogger(__name__)
# uvicorn default suprime INFO de loggers não-uvicorn — usar WARNING quando
# o evento for crítico (pico/alerta) garante que aparece em `docker logs`.

scheduler = AsyncIOScheduler()

# Bundling: se falhas em uma run >= esse limite, manda 1 mensagem agregada
# em vez de N individuais — evita spammar o grupo do Telegram quando uma
# queda de internet derruba 50 devices ao mesmo tempo.
MAX_ALERTAS_INDIVIDUAIS = 5

# Tamanho da janela histórica usada pra calcular a média de duração por device
# (e detectar picos). 10 backups cobre ~1 semana e meia em retenção default 7.
JANELA_MEDIA_DURACAO = 10

# Limite mínimo de coletas históricas pra considerar a média "confiável". Se o
# device tem só 1-2 backups, qualquer variação parece pico — só checamos depois
# que houver pelo menos esse número de durações registradas.
MIN_HISTORICO_PARA_PICO = 3

# Ratio (tamanho_novo / tamanho_anterior) abaixo do qual disparamos "alerta de
# redução suspeita" — mesma regra que o frontend já aplica visualmente em
# Backups.jsx (ALERTA_RATIO=0.5). Mantido sincronizado.
ALERTA_RATIO_TAMANHO = 0.5

PURGA_LOGS_JOB_ID = "purga_logs_diaria"


def _ext_arquivo(nome: str | None) -> str:
    """Extensão lowercase de nome_arquivo. Replica _extOf() do frontend pra
    que UNM2000 (envia .txt + .zip) não compare tamanho cross-extension."""
    if not nome:
        return ""
    i = nome.rfind(".")
    return nome[i:].lower() if i >= 0 else ""


async def executar_backups():
    """Job diário que pola SSH/Telnet/API sequencialmente.

    Comportamento v2 (delay adaptativo):
    - Após cada device, calcula um delay proporcional à duração do anterior
      (lê Configuracao.backup_delay_min_seg e backup_delay_fator) e aguarda
      esse tempo antes do próximo. Dá fôlego pro container/rede quando há
      muitos devices, e o tempo total da janela cresce de forma previsível.
    - Detecta picos (duração > N× a média histórica do device) e alerta no
      Telegram (categoria volume_alto) + WARNING crítico em docker logs.
    - Detecta redução suspeita de tamanho (< 50% do último sucesso com a
      mesma extensão) e alerta — sem converter em falha (a config pode ter
      genuinamente encolhido).
    """
    inicio = datetime.now(timezone.utc)
    log_run = LogScheduler(inicio=inicio)

    async with AsyncSessionLocal() as db:
        db.add(log_run)
        await db.flush()

        # Lê parâmetros de tuning (ou cria com defaults). Lê 1x no início e
        # reusa — mesmo que o admin mude no meio da run, o ritmo se mantém
        # consistente até a próxima execução.
        config = (await db.execute(select(Configuracao))).scalar_one_or_none()
        if config is None:
            config = Configuracao()
            db.add(config)
            await db.flush()
        delay_min = max(0, int(config.backup_delay_min_seg or 0))
        delay_fator = max(0.0, float(config.backup_delay_fator or 0.0))
        pico_fator = max(1.0, float(config.backup_pico_fator_critico or 3.0))

        total = sucessos = falhas = 0
        picos_detectados = 0
        alertas_tamanho_run = 0
        duracoes: list[int] = []  # uma entrada por device — pra média da run
        # Coleta as falhas/picos pra decidir individual vs bundle no fim.
        falhas_da_run: list[tuple[Device, str, int | None]] = []
        alertas_da_run: list[tuple[Device, str, int | None]] = []  # picos + reduções de tamanho

        try:
            # Devices em modo push (ftp_push/sftp_push/tftp_push) se auto-enviam
            # via servidor embutido. Não devem ser polados pelo scheduler — não
            # têm credencial SSH cadastrada e geravam falso-positivo de "senha
            # não cadastrada" no alerta Telegram.
            # Também filtra devices marcados como "manual apenas" (admin pediu
            # explicitamente pra não rodar no automático).
            # Ordenação por id garante reprodutibilidade — admin sabe quem
            # tende a ser primeiro / último na janela.
            result = await db.execute(
                select(Device).where(
                    Device.ativo == True,
                    Device.backup_manual_apenas == False,
                    Device.protocolo.notin_((
                        Protocolo.ftp_push, Protocolo.sftp_push, Protocolo.tftp_push,
                    )),
                ).order_by(Device.id)
            )
            devices = result.scalars().all()
            total = len(devices)

            for idx, device in enumerate(devices):
                ok, erro_msg, duracao_seg, alerta_tipo, alerta_msg = await _backup_device(
                    db, device, log_run.id, pico_fator,
                )
                if duracao_seg is not None:
                    duracoes.append(duracao_seg)
                if ok:
                    sucessos += 1
                else:
                    falhas += 1
                    falhas_da_run.append((device, erro_msg, device.empresa_id))

                if alerta_tipo == "pico":
                    picos_detectados += 1
                    alertas_da_run.append((device, alerta_msg, device.empresa_id))
                elif alerta_tipo == "reducao":
                    alertas_tamanho_run += 1
                    alertas_da_run.append((device, alerta_msg, device.empresa_id))

                # Delay adaptativo antes do PRÓXIMO device (não pra o último).
                if idx < total - 1 and (delay_min > 0 or delay_fator > 0):
                    base = duracao_seg if duracao_seg is not None else 0
                    delay = max(delay_min, int(round(delay_fator * base)))
                    if delay > 0:
                        await asyncio.sleep(delay)

            fim = datetime.now(timezone.utc)
            log_run.fim = fim
            log_run.total = total
            log_run.sucessos = sucessos
            log_run.falhas = falhas
            log_run.duracao_total_segundos = int((fim - inicio).total_seconds())
            log_run.duracao_media_segundos = (sum(duracoes) / len(duracoes)) if duracoes else None
            log_run.picos_detectados = picos_detectados
            log_run.alertas_tamanho = alertas_tamanho_run
            await db.commit()

            await _disparar_alertas_telegram(db, falhas_da_run)
            if alertas_da_run:
                await _disparar_alertas_telegram_v2(db, alertas_da_run)

        except Exception:
            log_run.fim = datetime.now(timezone.utc)
            log_run.total = total
            log_run.sucessos = sucessos
            log_run.falhas = falhas
            log_run.duracao_total_segundos = int((datetime.now(timezone.utc) - inicio).total_seconds())
            log_run.duracao_media_segundos = (sum(duracoes) / len(duracoes)) if duracoes else None
            log_run.picos_detectados = picos_detectados
            log_run.alertas_tamanho = alertas_tamanho_run
            log_run.erro_geral = traceback.format_exc()
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


async def _backup_device(
    db: AsyncSession, device: Device, log_id: int, pico_fator: float,
) -> tuple[bool, str | None, int | None, str | None, str | None]:
    """Roda backup do device. Retorna tupla:

    (sucesso, erro_resumido, duracao_segundos, alerta_tipo, alerta_msg)

    - sucesso: bool — backup criado com status='sucesso'
    - erro_resumido: primeira linha do erro (pra Telegram) ou None se ok
    - duracao_segundos: tempo de coleta (sempre preenchido, mesmo em falha)
    - alerta_tipo: None | 'pico' | 'reducao' — só pra runs bem-sucedidas
    - alerta_msg: descrição curta do alerta (pra Telegram + log)
    """
    t_inicio = time.monotonic()
    duracao = 0
    status: str = "falha"
    conteudo: str | None = None
    erro_resumido: str | None = None
    erro_tb: str | None = None

    try:
        status, conteudo = run_backup(device)
        duracao = int(round(time.monotonic() - t_inicio))
        if status != "sucesso":
            erro_resumido = (conteudo or "Falha sem detalhe").splitlines()[0][:300]
    except Exception:
        duracao = int(round(time.monotonic() - t_inicio))
        erro_tb = traceback.format_exc()
        status = "falha"
        erro_resumido = erro_tb.splitlines()[-1][:300]

    backup = Backup(
        device_id=device.id,
        log_scheduler_id=log_id,
        status=status,
        conteudo=conteudo if status == "sucesso" else None,
        erro=(conteudo if status == "falha" and conteudo else erro_tb),
        origem="scheduler",
        duracao_segundos=duracao,
    )
    db.add(backup)
    await db.flush()
    await _limpar_backups_antigos(db, device.id)

    if status != "sucesso":
        return False, erro_resumido, duracao, None, None

    # ===== Validações pós-coleta (só pra sucesso) =====
    alerta_tipo: str | None = None
    alerta_msg: str | None = None

    # 1) Detecção de pico — duração > pico_fator × média histórica.
    media_duracao = await _media_duracao_historica(db, device.id, exclude_backup_id=backup.id)
    if media_duracao is not None and duracao > pico_fator * media_duracao:
        alerta_tipo = "pico"
        alerta_msg = (
            f"Backup levou {duracao}s — {duracao / media_duracao:.1f}× "
            f"a média histórica ({media_duracao:.0f}s)"
        )
        log.warning(
            "PICO scheduler: device=%s id=%s duracao=%ss media=%.0fs ratio=%.2f",
            device.nome, device.id, duracao, media_duracao, duracao / media_duracao,
        )

    # 2) Redução suspeita de tamanho — < 50% do último sucesso (mesma extensão).
    # Só checa se ainda não há alerta de pico (não polui com 2 alertas pro mesmo backup).
    if alerta_tipo is None and conteudo:
        anterior_tamanho = await _ultimo_sucesso_tamanho(
            db, device.id, _ext_arquivo(backup.nome_arquivo), exclude_backup_id=backup.id,
        )
        novo_tamanho = len(conteudo)
        if anterior_tamanho is not None and anterior_tamanho > 0 \
           and novo_tamanho < ALERTA_RATIO_TAMANHO * anterior_tamanho:
            alerta_tipo = "reducao"
            alerta_msg = (
                f"Backup com {novo_tamanho} bytes — só "
                f"{(novo_tamanho / anterior_tamanho * 100):.0f}% do anterior "
                f"({anterior_tamanho} bytes)"
            )
            log.warning(
                "REDUÇÃO scheduler: device=%s id=%s novo=%s anterior=%s ratio=%.2f",
                device.nome, device.id, novo_tamanho, anterior_tamanho,
                novo_tamanho / anterior_tamanho,
            )

    return True, None, duracao, alerta_tipo, alerta_msg


async def _media_duracao_historica(
    db: AsyncSession, device_id: int, *, exclude_backup_id: int | None = None,
) -> float | None:
    """Média (segundos) das últimas JANELA_MEDIA_DURACAO durações registradas
    do device. Ignora backups sem duracao_segundos (push, pré-feature) e o
    backup recém-inserido (passado em exclude_backup_id). Retorna None quando
    histórico < MIN_HISTORICO_PARA_PICO."""
    q = (
        select(Backup.duracao_segundos)
        .where(Backup.device_id == device_id, Backup.duracao_segundos.is_not(None))
        .order_by(Backup.criado_em.desc())
        .limit(JANELA_MEDIA_DURACAO)
    )
    if exclude_backup_id is not None:
        q = q.where(Backup.id != exclude_backup_id)
    rows = (await db.execute(q)).fetchall()
    duracoes = [r[0] for r in rows if r[0] is not None]
    if len(duracoes) < MIN_HISTORICO_PARA_PICO:
        return None
    return sum(duracoes) / len(duracoes)


async def _ultimo_sucesso_tamanho(
    db: AsyncSession, device_id: int, extensao: str, *, exclude_backup_id: int,
) -> int | None:
    """Retorna tamanho (len do conteudo) do ÚLTIMO backup bem-sucedido anterior
    do device com a MESMA extensão de nome_arquivo. UNM2000 envia .txt+.zip
    e cada extensão tem ordem de magnitude diferente — comparar cross-extension
    daria sempre falso-positivo de "redução"."""
    q = (
        select(Backup.conteudo, Backup.nome_arquivo)
        .where(
            Backup.device_id == device_id,
            Backup.status == "sucesso",
            Backup.id != exclude_backup_id,
        )
        .order_by(Backup.criado_em.desc())
        .limit(5)  # checa só os últimos 5 — se não bate extensão neles, desiste
    )
    rows = (await db.execute(q)).fetchall()
    for conteudo, nome_arq in rows:
        if _ext_arquivo(nome_arq) != extensao:
            continue
        return len(conteudo) if conteudo else 0
    return None


async def _disparar_alertas_telegram_v2(
    db: AsyncSession, alertas: list[tuple[Device, str, int | None]],
) -> None:
    """Alertas de pico de duração / redução de tamanho.

    Usa categoria 'volume_alto' (reaproveita o flag já existente do Telegram —
    o admin não precisa habilitar nada novo se já tinha esse alerta ligado).
    Aplica o mesmo bundling de _disparar_alertas_telegram pra não spammar.
    """
    if not alertas:
        return
    por_empresa: dict[int | None, list[tuple[Device, str]]] = {}
    for dev, msg, emp_id in alertas:
        por_empresa.setdefault(emp_id, []).append((dev, msg))

    for emp_id, items in por_empresa.items():
        if len(items) <= MAX_ALERTAS_INDIVIDUAIS:
            for dev, msg in items:
                titulo = f"⚠️ Atenção no backup — {dev.nome}"
                detalhes = (
                    f"<b>IP:</b> <code>{dev.ip}</code>\n"
                    f"<b>Fabricante:</b> {dev.fabricante.value if dev.fabricante else '—'}\n"
                    f"<b>Alerta:</b> <i>{msg}</i>"
                )
                await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="volume_alto")
        else:
            nomes = ", ".join(d.nome for d, _ in items[:10])
            mais = f" e mais {len(items) - 10}" if len(items) > 10 else ""
            titulo = f"⚠️ {len(items)} alertas no scheduler"
            detalhes = (
                f"<b>Devices:</b> {nomes}{mais}\n"
                f"<i>Picos de duração ou redução de tamanho — ver Logs do painel.</i>"
            )
            await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="volume_alto")


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
